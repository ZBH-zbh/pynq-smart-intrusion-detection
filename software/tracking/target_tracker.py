"""Multi-object tracker for the PYNQ-Z2 motion-detection pipeline.

Why this exists
---------------
``motion_common.detect_targets`` returns an independent list of boxes for every
frame. Nothing links a box to the box seen in the previous frame, so the system
cannot answer "is this the same object?", and therefore cannot compute direction,
speed, line crossings or dwell time.

This module adds that missing layer: a small, dependency-light tracker that
assigns stable ids and keeps a short trajectory per object.

Design notes
------------
* Frames arrive at only ~15 FPS (camera limit), so two consecutive samples of a
  walking person are ~66 ms apart and the displacement is small (single-digit
  pixels). Velocity is therefore estimated over a *window* of samples rather
  than from the last two, which would be dominated by box jitter.
* Matching is greedy on (IoU, centroid distance). Greedy is fine here because
  the scene has very few objects; it avoids pulling in a Hungarian
  implementation for no practical gain.
* ``merge_nearby_boxes`` upstream can fuse two objects into one box. When that
  happens the surviving track simply absorbs the merge and the other track times
  out -- acceptable, and the miss counter keeps it from flapping.

The module deliberately depends only on numpy so it can be tested off-board.
"""

from collections import deque
import math
import time

import numpy as np


# --------------------------------------------------------------------------
# geometry helpers (plain tuples, no cv2 dependency)
# --------------------------------------------------------------------------

def box_iou(a, b):
    """IoU of two (x, y, w, h) boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh

    ix = min(ax2, bx2) - max(ax, bx)
    iy = min(ay2, by2) - max(ay, by)
    if ix <= 0 or iy <= 0:
        return 0.0

    inter = float(ix * iy)
    union = float(aw * ah + bw * bh) - inter
    return inter / union if union > 0 else 0.0


def box_centroid(box):
    x, y, w, h = box
    return (x + w / 2.0, y + h / 2.0)


def box_diagonal(box):
    _, _, w, h = box
    return math.hypot(w, h)


def _distance(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


DIRECTION_LABELS = (
    (22.5, "E", "右"),
    (67.5, "NE", "右上"),
    (112.5, "N", "上"),
    (157.5, "NW", "左上"),
    (180.0, "W", "左"),
)


def direction_label(angle_deg):
    """Human label for an angle where 0 deg = right and 90 deg = up."""
    if angle_deg is None:
        return "?"
    a = abs(angle_deg)
    if a <= 22.5:
        return "E"
    if a <= 67.5:
        return "NE" if angle_deg > 0 else "SE"
    if a <= 112.5:
        return "N" if angle_deg > 0 else "S"
    if a <= 157.5:
        return "NW" if angle_deg > 0 else "SW"
    return "W"


# --------------------------------------------------------------------------
# Track
# --------------------------------------------------------------------------

class Track:
    """One tracked object.

    ``box``      latest (x, y, w, h)
    ``history``  deque of (timestamp, cx, cy) samples, newest last
    ``velocity`` (vx, vy) in pixels per second, estimated over the window
    ``speed``    |velocity| in pixels per second
    ``angle``    direction in degrees, 0 = right, 90 = up, None when stationary
    """

    __slots__ = (
        "id", "box", "created_at", "updated_at", "hits", "misses",
        "history", "window", "_confirmed_at", "_smooth_size",
    )

    #: Exponential rate used to smooth the reported box size. A class attribute
    #: so it can be set on the class without adding it to __slots__.
    size_smoothing = 0.4

    def __init__(self, track_id, box, timestamp, history_len, window_seconds,
                 center=None):
        self.id = track_id
        self.box = tuple(box)
        self.created_at = timestamp
        self.updated_at = timestamp
        self.hits = 1
        self.misses = 0
        self.window = window_seconds
        self.history = deque(maxlen=history_len)
        self.history.append((
            timestamp,
            *(center if center is not None else box_centroid(box)),
        ))
        self._confirmed_at = None
        self._smooth_size = (float(box[2]), float(box[3]))

    # -- state ------------------------------------------------------------

    @property
    def centroid(self):
        return box_centroid(self.box)

    @property
    def age(self):
        """Seconds since the track was created."""
        return self.updated_at - self.created_at

    @property
    def alive(self):
        """Frames since the track was last matched."""
        return self.misses

    def confirm(self, min_hits):
        if self._confirmed_at is None and self.hits >= min_hits:
            self._confirmed_at = self.updated_at
        return self._confirmed_at is not None

    @property
    def confirmed(self):
        return self._confirmed_at is not None

    # -- updates ----------------------------------------------------------

    def update(self, box, timestamp, center=None):
        self.box = tuple(box)
        self.updated_at = timestamp
        self.hits += 1
        self.misses = 0
        self.history.append((
            timestamp,
            *(center if center is not None else box_centroid(box)),
        ))
        self._update_size(box)

    def _update_size(self, box):
        """Exponential average of the box size.

        The detector's box changes size every frame as the mask's edge moves by
        a pixel; drawing that raw makes the box itself look unstable even when
        the object is not moving.
        """
        rate = self.size_smoothing
        self._smooth_size = (
            self._smooth_size[0] + rate * (box[2] - self._smooth_size[0]),
            self._smooth_size[1] + rate * (box[3] - self._smooth_size[1]),
        )

    def mark_missed(self):
        self.misses += 1

    # -- motion -----------------------------------------------------------

    def _window_samples(self):
        """History samples inside the velocity window (newest last)."""
        if not self.history:
            return []
        newest_t = self.history[-1][0]
        cutoff = newest_t - self.window
        samples = [s for s in self.history if s[0] >= cutoff]
        # Need at least two samples spanning a usable amount of time.
        if len(samples) < 2:
            samples = list(self.history)[-2:]
        return samples

    def motion(self):
        """Return (vx, vy, speed, angle_deg). Angle is None when speed ~ 0.

        Velocity comes from a least-squares line through the window rather than
        from the two end samples. The end-sample difference uses only two noisy
        points, so box jitter passes straight into the speed and direction.
        """
        fit = self.fit()
        if fit is None:
            # Too little history to fit: fall back to the two end samples.
            samples = self._window_samples()
            if len(samples) < 2:
                return (0.0, 0.0, 0.0, None)
            t0, x0, y0 = samples[0]
            t1, x1, y1 = samples[-1]
            dt = t1 - t0
            if dt <= 1e-6:
                return (0.0, 0.0, 0.0, None)
            vx = (x1 - x0) / dt
            vy = (y1 - y0) / dt
        else:
            _, _, vx, vy = fit
        speed = math.hypot(vx, vy)

        # Below ~3 px/s the direction is box jitter, not real motion.
        if speed < 3.0:
            return (vx, vy, speed, None)

        # Image y grows downward, so negate to make 0 deg = right, 90 deg = up.
        angle = math.degrees(math.atan2(-vy, vx))
        return (vx, vy, speed, angle)

    def fit(self):
        """Least-squares line through the recent centroids.

        Returns ``(cx, cy, vx, vy)`` where the position is evaluated at the
        newest sample, or None when there is too little history.

        Two reasons to fit a line instead of smoothing with an exponential
        average:

        * An exponential average lags. For an object moving at a constant
          velocity a straight-line fit has no lag at all, so the reported point
          sits on the object instead of trailing behind it.
        * The box jitters in both position and size every frame, so a raw centre
          visibly shakes. Averaging over the window is what removes that, and it
          also gives a far steadier speed and direction.
        """
        samples = self._window_samples()
        if len(samples) < 3:
            return None

        newest = samples[-1][0]
        # Time relative to the newest sample: t = 0 there, negative going back.
        count = len(samples)
        sum_t = sum_t2 = sum_x = sum_y = sum_tx = sum_ty = 0.0
        for timestamp, x, y in samples:
            t = timestamp - newest
            sum_t += t
            sum_t2 += t * t
            sum_x += x
            sum_y += y
            sum_tx += t * x
            sum_ty += t * y

        denominator = count * sum_t2 - sum_t * sum_t
        if abs(denominator) < 1e-9:
            return None

        vx = (count * sum_tx - sum_t * sum_x) / denominator
        vy = (count * sum_ty - sum_t * sum_y) / denominator
        cx = (sum_x - vx * sum_t) / count
        cy = (sum_y - vy * sum_t) / count
        return (cx, cy, vx, vy)

    @property
    def center(self):
        """The tracked localisation point.

        This is the latest observed centre, which the detector supplies as the
        region's pixel centroid -- already a spatially robust estimate, so it
        needs no smoothing in time.

        Temporal smoothing was measured and rejected. On a simulated wave at 15
        FPS, an exponential average and a least-squares fit were both beaten by
        the unsmoothed measurement at every jitter level and every speed: their
        error was dominated by lag and barely moved when jitter changed, while
        the raw error stayed smaller. Smoothing only wins once the box jumps are
        large relative to the motion, and even then only modestly. The steady
        result has to come from a better per-frame estimate, not from averaging.
        """
        if self.history:
            _, x, y = self.history[-1]
            return (x, y)
        return self.centroid

    @property
    def size(self):
        """Box size, smoothed over time: the detector's box grows and shrinks
        from frame to frame and that alone makes a drawn box look unstable."""
        return self._smooth_size

    @property
    def display_box(self):
        """Box for drawing: the localisation point plus the smoothed size.

        Centring the drawn box on the tracked point rather than on the raw
        bounding box keeps the picture consistent with what is being reported.
        """
        cx, cy = self.center
        w, h = self._smooth_size
        return (cx - w / 2.0, cy - h / 2.0, w, h)

    #: Kept for callers written before the rename.
    smoothed_box = display_box

    @property
    def velocity(self):
        vx, vy, _, _ = self.motion()
        return (vx, vy)

    @property
    def speed(self):
        return self.motion()[2]

    @property
    def angle(self):
        return self.motion()[3]

    @property
    def direction(self):
        return direction_label(self.angle)

    @property
    def trail(self):
        """Trajectory as a list of (cx, cy), oldest first."""
        return [(s[1], s[2]) for s in self.history]

    def predicted_box(self, dt):
        """Where the box is expected ``dt`` seconds after its last update.

        Returns a plain (x, y, w, h) tuple; size is assumed constant because the
        detector gives no reliable size signal at this frame rate.
        """
        vx, vy, _, _ = self.motion()
        x, y, w, h = self.box
        return (x + vx * dt, y + vy * dt, w, h)

    def __repr__(self):
        return (
            f"Track(id={self.id}, box={self.box}, hits={self.hits}, "
            f"misses={self.misses}, speed={self.speed:.1f}px/s, "
            f"dir={self.direction})"
        )


# --------------------------------------------------------------------------
# Tracker
# --------------------------------------------------------------------------

class TargetTracker:
    """Greedy IoU/distance tracker.

    Parameters
    ----------
    iou_threshold : float
        Minimum IoU for two boxes to be considered the same object.
    gate_scale : float
        When IoU is zero, two boxes may still match if their centroid distance
        is below ``gate_scale * max(diagonal)``. This lets fast-moving objects
        (displacement larger than the box) stay on the same id.
    min_gate_px : float
        Lower bound on the centroid gate. Without it a small box can never
        accumulate the second hit it needs to estimate a velocity, so fast small
        objects break into one id per frame. 40 px is ~1/8 of the frame width.
    max_predict_s : float
        Upper bound on how far a track's motion is extrapolated when gating a
        detection after several missed frames.
    max_misses : int
        Frames a track may go unmatched before it is dropped.
    min_hits : int
        Matches required before a track is reported as confirmed.
    motion_min_speed : float
        Speed, in px/s, above which a track counts as moving and is matched
        against its predicted position only. See the note in ``_match_cost``.
    motion_min_hits : int
        Matches a track needs before its velocity is trusted for prediction.
    prediction_weight : float
        How much the predicted position counts versus the last observed box when
        a track is moving. 0 judges by position alone (which prefers whatever is
        stationary); 1 uses the prediction alone (which is fragile when the
        velocity is noisy). The default leans on the prediction for close calls
        while still accepting a jittering detection.
    history_len : int
        Trajectory samples kept per track.
    window_seconds : float
        Time window used to estimate velocity.
    """

    def __init__(
        self,
        iou_threshold=0.1,
        gate_scale=0.6,
        min_gate_px=40.0,
        max_predict_s=0.5,
        max_misses=12,
        min_hits=2,
        motion_min_speed=10.0,
        motion_min_hits=4,
        prediction_weight=0.3,
        history_len=24,
        window_seconds=0.5,
    ):
        if max_misses < 1:
            raise ValueError("max_misses must be >= 1")
        if min_hits < 1:
            raise ValueError("min_hits must be >= 1")
        if motion_min_hits < 1:
            raise ValueError("motion_min_hits must be >= 1")
        if not 0.0 <= prediction_weight <= 1.0:
            raise ValueError("prediction_weight must be in [0, 1]")

        self.iou_threshold = iou_threshold
        self.gate_scale = gate_scale
        self.min_gate_px = min_gate_px
        self.max_predict_s = max_predict_s
        self.max_misses = max_misses
        self.min_hits = min_hits
        self.motion_min_speed = motion_min_speed
        self.motion_min_hits = motion_min_hits
        self.prediction_weight = prediction_weight
        self.history_len = history_len
        self.window_seconds = window_seconds

        self.tracks = []
        self._next_id = 1
        self.frames = 0
        self.total_ids = 0

    # -- matching ---------------------------------------------------------

    def _match_cost(self, track, box, timestamp):
        """Cost of pairing a track with a detection; None means 'not matchable'.

        Both the track's last box and its predicted box are scored, blended with
        a weight on the prediction. That weight is what stops a track sliding
        off a moving object onto still background.

        A stationary false detection sits exactly where the track already is, so
        it scores a perfect IoU against the last box -- while the real object,
        having moved, scores lower. Matching on the last box alone therefore
        prefers whatever is not moving, and once the track takes it the velocity
        decays and it is stuck: measured on a synthetic object crossing a
        stationary distractor, the track ended 174 px away from the object.

        Restricting the match to the predicted box only fixes that but costs
        robustness, because a real velocity estimate is noisy and the detections
        themselves jitter in position and size, so a strict prediction misses
        and the box blinks. Blending keeps both: the prediction decides close
        calls, the last box keeps a match alive through jitter.
        """
        dt = timestamp - track.updated_at
        dt = min(max(dt, 0.0), self.max_predict_s)
        vx, vy, speed, _ = track.motion()
        predicted = track.predicted_box(dt)

        iou_last = box_iou(track.box, box)
        iou_pred = box_iou(predicted, box) if speed > 0.0 else iou_last

        if speed >= self.motion_min_speed and track.hits >= self.motion_min_hits:
            weight = self.prediction_weight
        else:
            # No trustworthy velocity yet: judge by where the box actually is.
            weight = 0.0

        iou = (1.0 - weight) * iou_last + weight * iou_pred
        if iou >= self.iou_threshold:
            return 1.0 - iou

        # Fall back to centroid distance against the predicted position.
        dist = _distance(box_centroid(predicted), box_centroid(box))
        gate = self.gate_scale * max(
            box_diagonal(predicted), box_diagonal(box)
        ) + speed * dt
        gate = max(gate, self.min_gate_px)
        if dist > gate:
            return None
        return 1.0 + dist / gate

    def _assign(self, boxes, timestamp):
        """Greedy assignment. Returns {track_index: detection_index}."""
        pairs = []
        for ti, track in enumerate(self.tracks):
            for di, box in enumerate(boxes):
                cost = self._match_cost(track, box, timestamp)
                if cost is not None:
                    pairs.append((cost, ti, di))

        pairs.sort(key=lambda p: p[0])

        used_tracks = set()
        used_dets = set()
        assignment = {}
        for _, ti, di in pairs:
            if ti in used_tracks or di in used_dets:
                continue
            used_tracks.add(ti)
            used_dets.add(di)
            assignment[ti] = di
        return assignment

    # -- main entry point -------------------------------------------------

    def update(self, boxes, timestamp=None, coast=0, min_speed=0.0,
               centers=None):
        """Feed one frame of detections.

        Parameters
        ----------
        boxes : iterable of (x, y, w, h)
        timestamp : float, optional
            Monotonic seconds. Defaults to ``time.monotonic()``.
        coast : int
            How many frames a confirmed track may go unmatched and still be
            reported. Defaults to 0, i.e. only tracks actually detected in this
            frame are returned. Coasting a frame or two smooths the display, but
            a coasting track must never be counted as a present object.

        Returns
        -------
        list[Track]
            Confirmed tracks live in this frame.
        """
        if timestamp is None:
            timestamp = time.monotonic()

        boxes = [tuple(int(v) for v in b) for b in boxes]
        if centers is not None and len(centers) != len(boxes):
            raise ValueError("centers must be parallel to boxes")
        self.frames += 1

        assignment = self._assign(boxes, timestamp)
        matched_dets = set(assignment.values())

        for ti, di in assignment.items():
            self.tracks[ti].update(
                boxes[di], timestamp,
                centers[di] if centers is not None else None,
            )

        for ti, track in enumerate(self.tracks):
            if ti not in assignment:
                track.mark_missed()

        for di, box in enumerate(boxes):
            if di in matched_dets:
                continue
            track = Track(
                self._next_id, box, timestamp,
                self.history_len, self.window_seconds,
                centers[di] if centers is not None else None,
            )
            self.tracks.append(track)
            self._next_id += 1
            self.total_ids += 1

        # Drop tracks that have been missing too long.
        self.tracks = [t for t in self.tracks if t.misses <= self.max_misses]

        for track in self.tracks:
            track.confirm(self.min_hits)

        return self.active_tracks(coast, min_speed)

    # -- reporting --------------------------------------------------------

    def active_tracks(self, coast=0, min_speed=0.0):
        """Confirmed tracks matched within the last ``coast`` frames.

        This is what callers should draw and count. ``confirmed_tracks()``
        deliberately still includes tracks that are coasting, which is only
        useful for introspection.

        ``min_speed`` drops tracks that are not actually moving. A curtain
        stirred by air, or any surface that shifts by a pixel, produces a real
        difference and a real track with a speed near 1 px/s; measured live on
        this scene it kept one such box alive indefinitely in the bottom-right
        corner. This is a motion detector, so a target that is not moving is not
        a target. Default 0 keeps everything.
        """
        return [
            t for t in self.tracks
            if t.confirmed and t.misses <= coast and t.speed >= min_speed
        ]

    def centers(self, tracks):
        """The localisation point of each track, as a list of (x, y)."""
        return [t.center for t in tracks]

    def confirmed_tracks(self):
        return [t for t in self.tracks if t.confirmed]

    def pending_tracks(self):
        return [t for t in self.tracks if not t.confirmed]

    def reset(self):
        self.tracks = []
        self._next_id = 1
        self.frames = 0
        self.total_ids = 0

    def summary(self):
        active = self.active_tracks()
        return {
            "frames": self.frames,
            "live_tracks": len(self.tracks),
            "active": len(active),
            "coasting": len(self.confirmed_tracks()) - len(active),
            "confirmed": len(self.confirmed_tracks()),
            "ids_created": self.total_ids,
        }


# --------------------------------------------------------------------------
# drawing helper (kept here so callers do not re-implement it)
# --------------------------------------------------------------------------

def draw_trails(frame, paths, color=(0, 200, 255), fade_steps=4,
                thickness=2):
    """Draw every trail, fading towards its older end.

    Drawn separately from the boxes because a trail outlives its track for a
    short while, and because a uniform colour over several seconds of history
    reads as a static squiggle: the fade is what shows which way the target was
    going.
    """
    import cv2

    if not paths:
        return frame

    for points in paths.values():
        if not points or len(points) < 2:
            continue
        count = len(points)
        steps = max(1, min(fade_steps, count))
        for step in range(steps):
            lo = count * step // steps
            hi = count * (step + 1) // steps
            # Overlap by one point so the chunks join without a visible gap.
            chunk = points[max(0, lo - 1 if step else 0):hi]
            if len(chunk) < 2:
                continue
            # Oldest chunk dimmest; the newest is the full colour.
            weight = (step + 1) / float(steps)
            shade = tuple(int(c * (0.35 + 0.65 * weight)) for c in color)
            pts = np.array(
                [[int(px), int(py)] for px, py in chunk], dtype=np.int32
            ).reshape(-1, 1, 2)
            cv2.polylines(frame, [pts], False, shade, thickness, cv2.LINE_AA)
    return frame


def draw_tracks(
    frame,
    tracks,
    show_id=True,
    show_speed=True,
    show_direction=True,
    show_trail=True,
    trail_color=(0, 200, 255),
    box_color=(0, 255, 0),
    label_color=(0, 255, 0),
    paths=None,
):
    """Draw track boxes, ids, velocity and trails onto a BGR frame in place.

    ``paths`` maps a track id to a pre-built dense polyline. When supplied it is
    drawn instead of the raw history, which is the whole point: the history is a
    staircase of per-frame jitter, while the supplied path has already been
    through the dead band and a spline fit. This function does no smoothing of
    its own, so nothing here can delay the frame.

    Trails belonging to tracks that are no longer in ``tracks`` are not drawn
    here -- see :func:`draw_trails`, which the caller uses for those.
    """
    import cv2

    for track in tracks:
        # Draw the smoothed box and the fitted point, not the raw detection:
        # the raw box changes size every frame and its centre visibly shakes.
        sx, sy, sw, sh = track.display_box
        x, y, w, h = int(round(sx)), int(round(sy)), int(round(sw)), int(round(sh))
        cv2.rectangle(frame, (x, y), (x + w, y + h), box_color, 2)

        parts = []
        if show_id:
            parts.append(f"#{track.id}")
        if show_direction:
            parts.append(track.direction)
        if show_speed:
            parts.append(f"{track.speed:.0f}px/s")

        label = " ".join(parts)
        if label:
            ty = y - 6 if y >= 16 else y + h + 16
            cv2.putText(
                frame, label, (x, ty),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, label_color, 1, cv2.LINE_AA,
            )

        if show_trail:
            points = paths.get(track.id) if paths is not None else None
            if points is None and len(track.history) > 1:
                points = [(px, py) for _, px, py in track.history]
            if points and len(points) > 1:
                pts = np.array(
                    [[int(px), int(py)] for px, py in points],
                    dtype=np.int32,
                ).reshape(-1, 1, 2)
                cv2.polylines(frame, [pts], False, trail_color, 2,
                              cv2.LINE_AA)

        if show_direction:
            vx, vy, speed, _ = track.motion()
            if speed >= 3.0:
                cx, cy = track.center
                scale = 0.25
                ex, ey = int(cx + vx * scale), int(cy + vy * scale)
                cv2.arrowedLine(
                    frame, (int(cx), int(cy)), (ex, ey),
                    (0, 255, 255), 2, cv2.LINE_AA, tipLength=0.3,
                )
                # A crosshair on the tracked point itself, so it is obvious
                # where the localisation is rather than where the box edge is.
                cv2.drawMarker(
                    frame, (int(cx), int(cy)), (0, 255, 255),
                    cv2.MARKER_CROSS, 9, 1, cv2.LINE_AA,
                )
    return frame
