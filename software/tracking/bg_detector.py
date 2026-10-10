"""PS-side running-average background model, replacing frame differencing.

Why this exists
---------------
The project's detector differences each frame against the previous one. That
reports *changed pixels*, and it fails in two ways that were measured on a
parameterised synthetic sweep (``eval_detection_modes.py``):

  * A small or slow object produces a change strip only a few pixels wide. The
    3x3 erosion and the 3x3 opening in the chain erase it before the contour
    stage, so it is never detected at all. Recall for a 16x20 object moving
    2 px/frame is 0.000, and lowering the grey threshold or the contour area
    floor does not help -- the pixels are already gone.
  * One object yields exactly two boxes (its leading and trailing edge), so
    ``boxes/obj`` is 2.00 on every configuration that detects anything. A real
    hand fragments further, and the tracker then jumps between fragments.

A background model compares each frame against a slowly updated estimate of the
empty scene, so a moving object appears as its whole silhouette instead of its
edges. The same sweep gives recall 0.77-0.99 and about 1.3 boxes per object.

Why the background is float32
-----------------------------
An 8-bit background cannot express a slow update. The increment is
``rate * difference``, and :func:`cv2.addWeighted` rounds the result to the
nearest integer, so once ``rate * difference`` falls below 0.5 the background
stops moving entirely. At ``rate = 0.008`` that dead zone starts at a grey
difference of about 62 -- while the detection threshold is 30, so a scene
change stalls at a difference that is still being reported as foreground.

Measured in ``eval_scene_change.py`` after a permanent change of scene:

    alpha_foreground   mean|current - background| after 1500 frames
    0.002 (uint8)      30.5, never falls further -> 2 false boxes forever
    0.002 (float32)    below the threshold       -> 0 boxes

Keeping the background in float32 removes the dead zone, and
:func:`cv2.accumulateWeighted` accepts an 8-bit mask, so the conditional update
no longer needs a scratch buffer and a copy either.

Cost on the board (Cortex-A9, 320x240), measured with ``bench_bg3.py``.

Known limitation
----------------
A permanent change is absorbed at ``alpha_foreground``, so an object that never
moves is eventually absorbed too. The default trades that away deliberately:
``alpha_foreground`` is set for recovering from a scene change in a few seconds,
because a background model is meaningless the moment the camera itself moves.

The arithmetic matches what would be written into ``frame_diff.cpp`` if this is
later moved into the PL, so this module doubles as the reference implementation
for that change.
"""

import cv2
import numpy as np

from motion_common import (
    HEIGHT,
    WIDTH,
    emulate_current_pl_erosion,
    process_mask,
)

# Defaults measured two ways: on the size/speed grid in eval_bg_configs.py,
# where every morphology setting scored the same (recall 0.686, precision
# 0.857), and on the board in bench_bg3.py, where dropping the 7x7 close and
# the erosion takes the detector from 31.13 ms to 19.92 ms per frame. Both of
# those steps exist to repair frame-difference artefacts, so a background model
# does not need them -- and without the erosion the project's original
# MIN_CONTOUR_AREA of 300 stays correct instead of rejecting small objects.
DEFAULT_ALPHA = 0.06
# Very low now that the relearn state handles genuine scene changes. The
# foreground rate is what leaves a ghost: an object dwelling d frames pulls the
# background 1-(1-alpha_fg)^d of the way toward its own colour, and with the old
# 0.01 that was ~7% of the contrast -- about 8 grey levels for a bright object,
# invisible to a fixed threshold of 30 but clearly visible to an adapted one.
DEFAULT_ALPHA_FOREGROUND = 0.002
DEFAULT_THRESHOLD = 30
DEFAULT_MIN_AREA = 300
DEFAULT_MAX_AREA = 50000


class BackgroundDetector:
    """Running-average background subtraction with a conditional update.

    Parameters
    ----------
    alpha : float
        Adaptation rate for background pixels. Time constant about
        ``1 / alpha`` frames.
    alpha_foreground : float
        Adaptation rate for pixels currently flagged as foreground. Keeping
        this below ``alpha`` is what stops a moving object from leaving a ghost
        trail: the background behind it is never taught the object's colour, so
        there is nothing to decay afterwards. It also bounds how long a
        permanent scene change stays a false positive, and how long a
        completely still object keeps being reported.
    threshold : int
        Grey-level difference above which a pixel counts as foreground.
    min_area, max_area : float
        Contour area filter. The project default of 300 is correct here, but
        only because ``erosion`` is off: a 16x20 object erodes to 14x18 = 252
        and would be rejected.
    erosion : bool
        Apply the PL chain's 3x3 erosion. Off by default: it costs 2.8 ms per
        frame, shrinks small objects, and only existed to suppress thin-strip
        noise from frame differencing.
    open_ksize : int
        Opening kernel. This is what removes speckle, and it is kept.
    close_ksize : int
        Closing kernel, off by default (1 disables it). It was tried for
        rejoining fragments of one object, and does help with holes smaller than
        the kernel -- but it cannot bridge a real one (the test scene has a
        19 px hole, which would need a 21x21 kernel), and it widens thin
        curtain stripes from 3 px to 7 px, which defeats ``min_thickness``.
        Fragments are joined by grouping boxes in
        :func:`strip_merge.merge_fragments_2d` instead, which handles any gap
        and does not touch the mask.
    min_fill_ratio : float
        Smallest allowed contourArea / bounding-box area, which rejects a
        sprawling connected region. Note this does NOT catch a moving curtain:
        measured, each stripe is its own thin contour whose fill ratio is 0.50 to
        0.99, no lower than a solid object's 0.82. ``min_thickness`` is what
        catches those.
    min_thickness : int
        Smallest allowed width and height of a bounding box, in pixels. A
        swaying curtain or a moving edge produces 2-3 px wide stripes running
        the height of the frame; measured over a stripe pattern they filled
        440 contours. At 320x240 a real object half a metre away is at least
        ~16 px across, so rejecting anything thinner removes them without
        touching real targets.
    max_box_fraction : float
        Largest allowed bounding-box area, as a fraction of the frame. This is
        what rejects the sprawling boxes that were measured at 265x240, 88% of
        the frame, whose centre sits at the middle of the picture regardless of
        where anything actually is.
    init_frames : int
        Frames used to build the initial background at ``init_alpha`` before
        switching to the conditional update. Anything in view during these
        frames is learnt into the background and then fades only at
        ``alpha_foreground``, so start the service on an empty scene.
    global_change_fraction : float
        When more than this fraction of pixels differ from the background, the
        frame is treated as a global change -- a light switching on, or the
        camera being moved -- rather than an object. Detection is suppressed for
        that frame and the background is rebuilt at ``init_alpha`` instead of
        being protected at ``alpha_foreground``. See the note below.
    adaptive_threshold : bool
        Derive ``threshold`` from the scene instead of using the fixed 30. See
        the note on scene independence below.
    target_foreground_fraction : float
        Fraction of pixels the calibration aims to leave above the threshold on
        a still frame. This is the knob that makes sensitivity scene
        independent: the threshold is whatever value leaves this fraction of
        pixels above it.
    min_threshold, max_threshold : int
        Clamps for the calibrated threshold.
    calibration_max_fraction : float
        Calibration only runs on frames with less foreground than this, so a
        large moving object cannot push the threshold up and hide itself.
    calibration_min_background : float
        Calibration is skipped unless at least this fraction of the frame is
        background, for the same reason: the target is a fraction of the
        background population, so a large object would otherwise shrink the
        target and raise the threshold.
    calibration_rate : float
        EMA rate applied to the calibrated threshold once start-up is over.
    scene_change_fraction : float
        Foreground fraction above which the scene MAY have changed (camera
        moved, room re-arranged) rather than an object moving. Size alone is not
        enough to tell those apart -- a hand held close to the lens also covers
        a large part of the frame -- so ``scene_change_overlap`` is required as
        well.
    scene_change_overlap : float
        How much of this frame's foreground must also have been foreground in
        the previous frame, for the change to look like a scene change rather
        than a moving object.

        A camera that has been moved leaves a mismatch region that is the same
        shape every frame, because both the new scene and the stored background
        are static: overlap is near 1. An object, however large, translates, so
        its overlap is lower. Without this test the relearn state fired 14 times
        during one round of hand waving, and each time it stopped reporting and
        learnt the hand into the background -- measured live, which is exactly
        the "detection became extremely insensitive" that was reported.
    scene_change_frames : int
        Consecutive frames meeting both conditions before relearning starts.
    stable_fraction, stable_frames : int
        Foreground fraction and duration that end relearning.
    relearn_alpha : float
        Background update rate while relearning; high, because the old
        background is known to be wrong.
    """

    def __init__(
        self,
        alpha=DEFAULT_ALPHA,
        alpha_foreground=DEFAULT_ALPHA_FOREGROUND,
        threshold=DEFAULT_THRESHOLD,
        min_area=DEFAULT_MIN_AREA,
        max_area=DEFAULT_MAX_AREA,
        erosion=False,
        close_ksize=1,
        open_ksize=3,
        min_fill_ratio=0.3,
        min_thickness=6,
        max_box_fraction=0.5,
        init_frames=8,
        init_alpha=0.5,
        global_change_fraction=0.5,
        adaptive_threshold=True,
        target_foreground_fraction=0.002,
        min_threshold=6,
        max_threshold=45,
        calibration_rate=0.05,
        calibration_max_fraction=0.05,
        calibration_min_background=0.7,
        scene_change_fraction=0.8,
        scene_change_overlap=0.85,
        scene_change_frames=3,
        stable_fraction=0.05,
        stable_frames=10,
        relearn_alpha=0.25,
    ):
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        if not 0.0 <= alpha_foreground <= 1.0:
            raise ValueError("alpha_foreground must be in [0, 1]")
        if init_frames < 1:
            raise ValueError("init_frames must be >= 1")
        if not 0.0 <= min_fill_ratio <= 1.0:
            raise ValueError("min_fill_ratio must be in [0, 1]")
        if min_thickness < 1:
            raise ValueError("min_thickness must be >= 1")
        if not 0.0 < max_box_fraction <= 1.0:
            raise ValueError("max_box_fraction must be in (0, 1]")
        if not 0.0 < global_change_fraction <= 1.0:
            raise ValueError("global_change_fraction must be in (0, 1]")
        if not 0.0 < target_foreground_fraction < 1.0:
            raise ValueError("target_foreground_fraction must be in (0, 1)")
        if min_threshold < 1:
            raise ValueError("min_threshold must be >= 1")
        if max_threshold < min_threshold:
            raise ValueError("max_threshold must be >= min_threshold")
        if scene_change_frames < 1:
            raise ValueError("scene_change_frames must be >= 1")
        if stable_frames < 1:
            raise ValueError("stable_frames must be >= 1")
        if not 0.0 < relearn_alpha <= 1.0:
            raise ValueError("relearn_alpha must be in (0, 1]")

        self.adaptive_threshold = adaptive_threshold
        self.target_foreground_fraction = target_foreground_fraction
        self.min_threshold = min_threshold
        self.max_threshold = max_threshold
        self.calibration_rate = calibration_rate
        self.calibration_max_fraction = calibration_max_fraction
        self.calibration_min_background = calibration_min_background
        self.scene_change_fraction = scene_change_fraction
        self.scene_change_overlap = scene_change_overlap
        self.scene_change_frames = scene_change_frames
        self.stable_fraction = stable_fraction
        self.stable_frames = stable_frames
        self.relearn_alpha = relearn_alpha

        self.alpha = alpha
        self.alpha_foreground = alpha_foreground
        self.threshold = threshold
        self.min_area = min_area
        self.max_area = max_area
        self.erosion = erosion
        self.min_fill_ratio = min_fill_ratio
        self.min_thickness = min_thickness
        self.max_box_fraction = max_box_fraction
        self.init_frames = init_frames
        self.init_alpha = init_alpha
        self.global_change_fraction = global_change_fraction

        self._close_kernel = (
            np.ones((close_ksize, close_ksize), np.uint8)
            if close_ksize > 1 else None
        )
        self._open_kernel = np.ones((open_ksize, open_ksize), np.uint8)
        self._max_box_area = max_box_fraction * WIDTH * HEIGHT

        self.background = None          # float32
        self._background_u8 = None      # uint8 mirror, used for the difference
        self._not_foreground = None
        self.frames = 0
        self.global_changes = 0
        self.relearn_events = 0
        self.relearning = False
        self._change_streak = 0
        self._stable_streak = 0
        self._foreground_fraction = 0.0
        self._mask_overlap = 1.0
        self._previous_binary = None
        self._previous_foreground = 0
        self._and_buffer = None
        self.on_relearn = None

    def reset(self):
        self.background = None
        self._background_u8 = None
        self._not_foreground = None
        self.frames = 0
        self.global_changes = 0
        self.relearn_events = 0
        self.relearning = False
        self._change_streak = 0
        self._stable_streak = 0
        self._foreground_fraction = 0.0
        self._mask_overlap = 1.0
        self._previous_binary = None
        self._previous_foreground = 0
        self.last_centers = []
        #: Centres of the boxes from the last ``process`` call, same order and
        #: length. The tracker uses these as the localisation point instead of
        #: the bounding-box centre.
        self.last_centers = []

    def _foreground_overlap(self, binary, changed):
        """Fraction of this frame's foreground that was foreground last frame.

        Near 1 means the mismatch region is not moving -- the signature of a
        camera that has been moved or a scene that has changed -- while a
        translating object gives a lower value.

        Undefined when there was no foreground at all last frame; that is a
        sudden appearance, not evidence that the region is moving, so it counts
        as overlap 1.
        """
        if self._previous_binary is None:
            self._previous_binary = binary.copy()
            self._previous_foreground = changed
            return 1.0
        if self._previous_foreground == 0 or changed == 0:
            self._previous_binary[:] = binary
            self._previous_foreground = changed
            return 1.0
        if self._and_buffer is None:
            self._and_buffer = np.empty_like(binary)
        cv2.bitwise_and(binary, self._previous_binary,
                        dst=self._and_buffer)
        intersection = cv2.countNonZero(self._and_buffer)
        self._previous_binary[:] = binary
        self._previous_foreground = changed
        return intersection / float(changed)

    # -- internals --------------------------------------------------------

    def _post_process(self, binary):
        """Erosion (optional) + close (optional) + open + filters -> boxes."""
        mask = emulate_current_pl_erosion(binary) if self.erosion else binary
        if self._close_kernel is not None:
            # Close before opening: join the fragments of one object, then let
            # the opening clean up whatever speckle the closing joined in.
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._close_kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._open_kernel)

        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        boxes = []
        centers = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if not self.min_area <= area <= self.max_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            box_area = float(w * h)
            if box_area <= 0:
                continue
            # Reject line-like fringes first: a swaying curtain edge is a 2-3 px
            # wide stripe running the height of the frame, and each stripe is
            # its own high-fill contour, so the fill test below cannot see it.
            if min(w, h) < self.min_thickness:
                continue
            # Reject sprawling regions: a huge bounding box with little inside.
            if area / box_area < self.min_fill_ratio:
                continue
            # Reject anything claiming half the picture or more.
            if box_area > self._max_box_area:
                continue
            boxes.append((x, y, w, h))
            centers.append(self._contour_center(contour, x, y, w, h))
        self.last_centers = centers
        return mask, boxes

    @staticmethod
    def _contour_center(contour, x, y, w, h):
        """Pixel centroid of the region, not the centre of its bounding box.

        The localisation point has to be steady, and the bounding-box centre is
        the least steady choice available: a single stray pixel on the edge
        moves it by half that pixel's distance from the box. Measured on the
        tracked point, a one-pixel error at the box edge displaces the box
        centre by up to d/2 while the pixel centroid moves by d/N, which for a
        couple of thousand foreground pixels is two orders of magnitude smaller.

        This costs nothing in lag: it is a per-frame spatial estimate, not a
        average over time, and averaging over time was measured to lose to the
        raw measurement for waving motion because the lag dominates.
        """
        moments = cv2.moments(contour)
        if moments["m00"] <= 0:
            return (x + w / 2.0, y + h / 2.0)
        return (moments["m10"] / moments["m00"],
                moments["m01"] / moments["m00"])

    def _conditional_update(self, gray, foreground_mask):
        """Advance the background fast outside the object, slowly inside it."""
        if self._not_foreground is None:
            self._not_foreground = np.empty_like(foreground_mask)
        cv2.bitwise_not(foreground_mask, dst=self._not_foreground)
        cv2.accumulateWeighted(
            gray, self.background, self.alpha, mask=self._not_foreground
        )
        if self.alpha_foreground > 0.0:
            cv2.accumulateWeighted(
                gray, self.background, self.alpha_foreground,
                mask=foreground_mask,
            )

    def _calibrate(self, difference, background_mask):
        """Set the threshold so only a fixed fraction of pixels stay above it.

        A fixed grey-level threshold is scene dependent: measured on a battery
        of synthetic rooms, recall for the same object ranged from 0.00 to 1.00
        depending only on the background, because an object has to differ from
        its background by more than the threshold to be seen at all. A light
        sleeve against a light wall is about ten grey levels; a dark object
        against a window is over a hundred.

        Driving the threshold from the scene makes sensitivity comparable
        everywhere: on a quiet scene it falls and faint objects are found, on a
        dim noisy scene it rises and noise is not mistaken for motion.

        The histogram is taken over ``background_mask`` -- the pixels that are
        NOT currently foreground. Measuring the whole frame instead would let a
        large moving object raise the threshold until it hid itself, and it
        would also freeze the calibration entirely, since an object present in
        most frames means the frame is never "still enough" to calibrate on.
        """
        histogram = cv2.calcHist(
            [difference], [0], background_mask, [256], [0, 256]
        ).ravel()
        population = float(histogram.sum())
        if population < 100:
            return None
        # Skip calibration when too much of the frame is an object. The target
        # is a fraction of the BACKGROUND population, so a large object shrinks
        # both the population and the target, and the threshold then climbs to
        # keep only that smaller count above it -- the object raises the
        # threshold that is meant to detect it. Measured live: an object
        # covering 42% of the frame pushed the threshold from 15 to 32.
        if population < self.calibration_min_background * difference.size:
            return None
        # counts_ge[v] = number of background pixels whose difference is >= v.
        counts_ge = np.cumsum(histogram[::-1])[::-1]
        target = self.target_foreground_fraction * population
        below = np.flatnonzero(counts_ge <= target)
        if below.size == 0:
            return self.max_threshold
        return int(below[0])

    # -- main entry point -------------------------------------------------

    def process(self, frame_bgr):
        """Returns (mask, boxes). The first frames return an empty mask."""
        if frame_bgr.shape[:2] != (HEIGHT, WIDTH):
            frame_bgr = cv2.resize(frame_bgr, (WIDTH, HEIGHT))

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        # Cleared up front so the centres can never be stale relative to the
        # boxes: several paths below return no boxes at all.
        self.last_centers = []

        if self.background is None:
            self.background = gray.astype(np.float32)
            self._background_u8 = gray.copy()
            self.frames = 1
            return np.zeros((HEIGHT, WIDTH), np.uint8), []

        difference = cv2.absdiff(gray, self._background_u8)
        total = difference.size

        _, binary = cv2.threshold(
            difference, int(round(self.threshold)), 255, cv2.THRESH_BINARY
        )

        # Calibrate against the background pixels only. Runs both while the
        # scene is still and while an object is moving, so the threshold keeps
        # tracking the room instead of freezing at whatever it was when
        # something first appeared.
        if self.adaptive_threshold and self.frames > self.init_frames:
            if self._not_foreground is None:
                self._not_foreground = np.empty_like(binary)
            cv2.bitwise_not(binary, dst=self._not_foreground)
            wanted = self._calibrate(difference, self._not_foreground)
            if wanted is not None:
                wanted = int(np.clip(wanted, self.min_threshold,
                                     self.max_threshold))
                rate = 1.0 if self.frames < 2 * self.init_frames \
                    else self.calibration_rate
                self.threshold += rate * (wanted - self.threshold)
                self.threshold = float(np.clip(self.threshold,
                                               self.min_threshold,
                                               self.max_threshold))
                _, binary = cv2.threshold(
                    difference, int(round(self.threshold)),
                    255, cv2.THRESH_BINARY
                )

        changed = cv2.countNonZero(binary)
        fraction = changed / float(total)
        self._foreground_fraction = fraction
        self._mask_overlap = self._foreground_overlap(binary, changed)

        # --- scene change and relearning --------------------------------
        # A camera that has been moved, or a room whose lighting has changed,
        # leaves a background that is simply wrong. Reporting it produces a
        # screen full of boxes and a buzzer that will not stop, and the
        # conditional update protects that wrong background for hundreds of
        # frames because every pixel counts as foreground. Detect the sustained
        # condition, stop reporting, and rebuild instead.
        #
        # Large is not enough: a hand held near the lens also covers a big part
        # of the frame. The extra requirement is that the region is NOT MOVING,
        # which separates "the world changed" from "something is passing".
        scene_like = (
            fraction > self.scene_change_fraction
            and self._mask_overlap > self.scene_change_overlap
        )
        if scene_like:
            self._change_streak += 1
        else:
            self._change_streak = 0

        # Not during start-up: the background is still being built then, so a
        # high foreground fraction is expected and counting it would report a
        # handful of relearns before the camera has even settled.
        settled = self.frames > 2 * self.init_frames
        if (not self.relearning and settled
                and self._change_streak >= self.scene_change_frames):
            self.relearning = True
            self.relearn_events += 1
            self._stable_streak = 0
            if self.on_relearn is not None:
                self.on_relearn(fraction, self._mask_overlap, self.frames)

        if self.relearning:
            cv2.accumulateWeighted(gray, self.background, self.relearn_alpha)
            if fraction < self.stable_fraction:
                self._stable_streak += 1
            else:
                self._stable_streak = 0
            if self._stable_streak >= self.stable_frames:
                self.relearning = False
                self._change_streak = 0
            cv2.convertScaleAbs(self.background, dst=self._background_u8)
            self.frames += 1
            return np.zeros((HEIGHT, WIDTH), np.uint8), []

        if scene_like:
            # Hold the background still while the streak builds, so the
            # one-frame global-change path below cannot rebuild it first and
            # dissolve the evidence before relearning is reached.
            self.frames += 1
            return np.zeros((HEIGHT, WIDTH), np.uint8), []

        # --- ordinary frame ---------------------------------------------
        if changed > self.global_change_fraction * total:
            # A one-frame jump this large is a light switching, not an object.
            self.global_changes += 1
            cv2.accumulateWeighted(gray, self.background, self.init_alpha)
            cv2.convertScaleAbs(self.background, dst=self._background_u8)
            self.frames += 1
            return np.zeros((HEIGHT, WIDTH), np.uint8), []

        mask, boxes = self._post_process(binary)

        if self.frames < self.init_frames:
            cv2.accumulateWeighted(gray, self.background, self.init_alpha)
        else:
            self._conditional_update(gray, mask)

        cv2.convertScaleAbs(self.background, dst=self._background_u8)
        self.frames += 1
        return mask, boxes

    # -- diagnostics ------------------------------------------------------

    def info(self):
        return {
            "detector": "bg",
            "alpha": self.alpha,
            "alpha_foreground": self.alpha_foreground,
            "threshold": round(self.threshold, 1),
            "adaptive_threshold": self.adaptive_threshold,
            "min_area": self.min_area,
            "erosion": self.erosion,
            "foreground_fraction": round(self._foreground_fraction, 4),
            "mask_overlap": round(self._mask_overlap, 3),
            "relearning": self.relearning,
            "relearn_events": self.relearn_events,
            "global_changes": self.global_changes,
            "frames": self.frames,
        }
