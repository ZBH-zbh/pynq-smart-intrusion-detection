#!/usr/bin/env python3
"""Tracking demo for the PYNQ-Z2 motion detection project.

What it adds over ``mjpeg_server.py``
-------------------------------------
``mjpeg_server.py`` draws independent boxes per frame: nothing links a box to
the box seen a frame earlier, so the system cannot say "that is the same
person", and therefore cannot report direction, speed or a stable count. This
server puts the tracker in the loop and shows the result.

It also enables the edge-strip merge by default. Frame differencing reports
changed pixels, so a moving solid-coloured object comes back as two thin strips
(its leading and trailing edge). Measured on the project's synthetic sequence,
raw boxes reach recall 0.027 / precision 0.018 against the real objects, while
merging the strips first reaches recall 0.746 / precision 0.952. Counting and
direction are only meaningful after that merge.

Two sources
-----------
``--source board``      real camera + PL pipeline (needs pynq)
``--source synthetic``  generated frames, no hardware -- lets the whole server
                        be exercised on a PC before deploying

Endpoints
---------
  /            HTML page: live image + track table
  /snapshot    single JPEG
  /status      JSON summary
  /tracks      JSON list of live tracks
  /stream      MJPEG (kept for diagnostics; unreliable on this link)
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

# Board layout first, then this file's directory, so the same script runs both
# on the board and from the repository on a PC.
sys.path.insert(0, "/home/xilinx/intrusion_demo")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "pynq_deploy")))

from motion_common import (  # noqa: E402
    ALARM_ZONE,
    HEIGHT,
    WIDTH,
    check_alarm,
    detect_targets,
    process_mask,
)
from bg_detector import BackgroundDetector  # noqa: E402
from line_crossing import LineCrossingCounter  # noqa: E402
from grouping import StripGrouper  # noqa: E402
from localisation import region_centers  # noqa: E402
from trail import TrailStore  # noqa: E402
from strip_merge import (  # noqa: E402
    merge_centers,
    merge_edge_strips,
    merge_fragments_2d,
)
from target_tracker import (  # noqa: E402
    TargetTracker,
    draw_tracks,
    draw_trails,
)


# --------------------------------------------------------------------------
# pipeline (pure OpenCV + numpy, no hardware)
# --------------------------------------------------------------------------

#: Default tripwire: a vertical line down the middle, which splits the view into
#: a left and a right half. Adjustable from the page like the alarm zone.
DEFAULT_LINE = (WIDTH // 2, 40, WIDTH // 2, HEIGHT - 40)


class TrackerPipeline:
    """Merge detections -> track them -> render an annotated frame."""

    def __init__(self, merge_gap=62.0, coast=0, show_raw=False,
                 alarm_on="tracks", detector="pl", max_box_fraction=0.5,
                 min_speed=0.0, prediction_weight=0.3,
                 bg_merge_gap=25.0, bg_merge_scale=0.3, zone=None, line=None,
                 crossing=True, trail=None,
                 confirm_distance=12.0, confirm_seconds=1.0,
                 group_strips=True):
        self.merge_gap = merge_gap
        self.coast = coast
        self.show_raw = show_raw
        self.alarm_on = alarm_on
        self.detector_mode = detector
        self.max_box_area = max_box_fraction * WIDTH * HEIGHT
        self.min_speed = min_speed
        self.bg_merge_gap = bg_merge_gap
        self.bg_merge_scale = bg_merge_scale
        #: Alarm zone, adjustable at run time from the web page. The shipped
        #: constant is only the starting value.
        self.zone = tuple(zone) if zone else ALARM_ZONE
        #: Tripwire for counting. None disables counting entirely.
        #: Pairs the leading and trailing edge of one object, so the counter
        #: does not count both. It only advises the counter -- the boxes drawn
        #: on screen are still the detector's own, unchanged.
        self.grouper = StripGrouper() if group_strips else None
        self.counter = (LineCrossingCounter(
                            tuple(line) if line else DEFAULT_LINE,
                            confirm_distance=confirm_distance,
                            confirm_seconds=confirm_seconds,
                            grouping=self.grouper)
                        if crossing else None)
        #: Display trails. Recorded through a dead band and drawn as a spline,
        #: kept separate from the tracker's own history so velocity, direction
        #: and the crossing counter keep using the unsmoothed positions.
        self.trails = TrailStore(**(trail or {}))
        self.tracker = TargetTracker(prediction_weight=prediction_weight)

    def merge(self, boxes, centers=None):
        """Fuse the pieces of one object into a single box.

        The two detectors fragment differently, so they need different rules.
        Frame differencing splits an object into a leading and a trailing strip
        at the same height; a background model splits it wherever its colour
        happens to match the background, leaving a hole that can be anywhere.
        Morphological closing cannot repair the second case -- bridging the
        19 px hole in the test scene would need a 21x21 kernel -- so the boxes
        are grouped instead.

        Returns ``(boxes, centers)``; the centres follow the grouping so the
        localisation point stays on the object's mass.
        """
        original = list(boxes)
        centers = list(centers) if centers is not None else [
            ((b[0] + b[2] / 2.0), (b[1] + b[3] / 2.0)) for b in original
        ]

        # The two detectors fragment differently and are repaired by different
        # rules, so their switches are independent.
        #
        # They were NOT independent: the bg rule used to sit inside
        # ``if self.merge_gap > 0``. Since --merge-gap defaults to 0 -- the
        # "teammate's boxes verbatim" decision -- the bg fragment merge was
        # unreachable, and --bg-merge-gap was dead configuration. The result was
        # a background model reporting one object as up to 14 boxes with nothing
        # joining them up. Frame differencing keeps its own switch so the frozen
        # detector's output is still passed through untouched by default.
        if self.detector_mode == "bg":
            if self.bg_merge_gap > 0:
                boxes = merge_fragments_2d(
                    original, gap_scale=self.bg_merge_scale,
                    max_gap=self.bg_merge_gap,
                )
            else:
                boxes = original
        elif self.merge_gap > 0:
            boxes = merge_edge_strips(original, max_gap=self.merge_gap)
        else:
            boxes = original

        # Grouping can produce a box larger than any of its inputs, so the
        # detector's own size limit has to be applied again here. Without this
        # a 283x162 box (60% of the frame) reached the tracker even though
        # every input box was inside the limit.
        kept = [
            (index, b) for index, b in enumerate(boxes)
            if float(b[2] * b[3]) <= self.max_box_area
        ]
        boxes = [b for _, b in kept]
        if centers and (boxes is not original):
            merged = merge_centers(original, centers, boxes)
        else:
            merged = centers
        return boxes, merged

    def merge_settings(self):
        return {
            "bg_merge_gap": self.bg_merge_gap,
            "bg_merge_scale": self.bg_merge_scale,
            "detector": self.detector_mode,
        }

    def configure_merge(self, bg_merge_gap=None, bg_merge_scale=None):
        """Change how aggressively fragments are joined, at run time.

        Each assignment is atomic in CPython, so the detection loop sees either
        the old value or the new one and never a half-applied pair.
        """
        if bg_merge_gap is not None:
            self.bg_merge_gap = float(bg_merge_gap)
        if bg_merge_scale is not None:
            self.bg_merge_scale = float(bg_merge_scale)
        return self.merge_settings()

    def step(self, frame_bgr, raw_boxes, timestamp, fps, detector_info=None,
             centers=None):
        """One frame through the feature chain. Returns (vis, info, alarm)."""
        detector_info = detector_info or {}
        boxes, centers = self.merge(raw_boxes, centers)
        tracks = self.tracker.update(
            boxes, timestamp=timestamp, coast=self.coast,
            min_speed=self.min_speed, centers=centers,
        )
        # The alarm follows confirmed tracks, not raw detections. A single
        # sensor glitch used to set the buzzer off immediately and the user
        # reported it "ringing non-stop"; a track has to survive min_hits
        # consecutive frames before it can raise the alarm. This also makes the
        # buzzer agree with the green boxes actually drawn on screen.
        if self.alarm_on == "tracks":
            alarm = check_alarm([t.box for t in tracks], zone=self.zone)
        else:
            alarm = check_alarm(boxes, zone=self.zone)

        # Counting uses the same localisation point the tracker reports, so a
        # jittering box edge cannot wander across the line on its own.
        if self.counter is not None:
            # Observed before the counter looks, so a pair is judged on this
            # frame's positions rather than last frame's.
            if self.grouper is not None:
                self.grouper.observe(tracks, timestamp)
            self.counter.update(
                [(t.id, t.center) for t in tracks], timestamp=timestamp
            )

        # Trail recording. Only a recorded point triggers a rebuild, and the
        # renderer below reads the cached path, so the frame never waits on the
        # spline fit. Trails outlive their track for a while, so expiry is by
        # time rather than by the track list.
        for track in tracks:
            self.trails.add(track.id, track.center, timestamp=timestamp)
        self.trails.prune(timestamp)

        vis = self.render(frame_bgr, raw_boxes, boxes, tracks, fps, alarm,
                          detector_info)
        info = {
            "fps": round(fps, 2),
            "raw_targets": len(raw_boxes),
            "targets": len(boxes),
            "tracks": len(tracks),
            "alarm": bool(alarm),
            "ids_created": self.tracker.total_ids,
            "zone": list(self.zone),
        }
        if self.counter is not None:
            info["crossing"] = self.counter.info()
        if self.grouper is not None:
            info["grouping"] = self.grouper.info()
        info["trail"] = self.trails.stats()
        # Surface the detector's own state so the page can show what it has
        # adapted to, rather than leaving it invisible.
        info.update(detector_info)
        return vis, info, alarm

    def render(self, frame_bgr, raw_boxes, boxes, tracks, fps, alarm,
               detector_info=None):
        detector_info = detector_info or {}
        vis = frame_bgr.copy()

        zone_x, zone_y, zone_w, zone_h = self.zone
        cv2.rectangle(
            vis,
            (zone_x, zone_y),
            (zone_x + zone_w, zone_y + zone_h),
            (0, 0, 255) if alarm else (255, 0, 0),
            2,
        )

        # Tell the viewer what the detector is doing: the adapted threshold,
        # and whether it has decided the scene itself has changed.
        status = []
        if detector_info.get("threshold") is not None:
            status.append(f"thr {detector_info['threshold']:.0f}")
        if detector_info.get("relearning"):
            status.append("RELEARN")

        self._draw_crossing_counts(vis)
        if status:
            cv2.putText(vis, " ".join(status), (170, 52),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

        # Raw detections, when asked for: shows the two edge strips the frame
        # difference actually produces, before merging.
        if self.show_raw:
            for x, y, w, h in raw_boxes:
                cv2.rectangle(vis, (x, y), (x + w, y + h), (128, 128, 128), 1)

        draw_tracks(
            vis,
            tracks,
            show_id=True,
            show_speed=True,
            show_direction=True,
            show_trail=False,
        )
        # Trails are drawn separately so that a trail whose track has just
        # disappeared keeps being shown until it expires.
        draw_trails(vis, self.trails.paths())

        track_count = len(tracks)
        cv2.putText(vis, f"Tracks: {track_count}", (10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
        cv2.putText(vis, f"FPS: {fps:.1f}", (10, 44),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
        cv2.putText(vis, "ALARM!" if alarm else "Normal", (10, 66),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 0, 255) if alarm else (0, 255, 0), 2)
        return vis

    def _draw_crossing_counts(self, vis):
        """Tripwire and its running totals, in the top-right corner.

        Right-aligned so the numbers stay put as they grow from 9 to 10, which
        a left-aligned block would not do.
        """
        counter = self.counter
        if counter is None:
            return

        x1, y1, x2, y2 = (int(round(v)) for v in counter.line)
        cv2.line(vis, (x1, y1), (x2, y2), (255, 255, 0), 2, cv2.LINE_AA)
        # Small cross at each end, so it is obvious the wire has a finite
        # extent and that crossings past the tips do not count.
        for ex, ey in ((x1, y1), (x2, y2)):
            cv2.drawMarker(vis, (ex, ey), (255, 255, 0), cv2.MARKER_TILTED_CROSS,
                           9, 2, cv2.LINE_AA)

        first, second = counter.labels()
        rows = (
            (f"{first} {counter.negative_to_positive}", (0, 255, 255)),
            (f"{second} {counter.positive_to_negative}", (0, 255, 255)),
        )
        scale, thickness = 0.5, 1
        y = 18
        for text, colour in rows:
            (width, _), _ = cv2.getTextSize(
                text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness
            )
            cv2.putText(vis, text, (WIDTH - width - 6, y),
                        cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thickness,
                        cv2.LINE_AA)
            y += 18


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------

class SyntheticSource:
    """Generated frames, paced to roughly the board's 15 FPS. No hardware."""

    name = "synthetic"
    fps_hint = 15.0

    def __init__(self, count=600, detector="pl"):
        from benchmark_motion import generate_synthetic_frames
        from opencv_software_motion import SoftwareMotionDetector

        self.frames = generate_synthetic_frames(count)
        self.detector_mode = detector
        self.detector = SoftwareMotionDetector()
        self.bg = BackgroundDetector() if detector == "bg" else None
        self._centers = None
        self.index = 0

    def open(self):
        pass

    def read(self):
        """Returns (frame_bgr, raw_boxes) or None when finished."""
        if self.index >= len(self.frames):
            return None
        frame = self.frames[self.index]
        self.index += 1
        if self.bg is not None:
            _, boxes = self.bg.process(frame)
            return frame, boxes

        mask, boxes, _ = self.detector.process(frame)
        # Same localisation feature as the board path: read a point out of the
        # mask without changing which boxes the shipped chain produced.
        self._centers = region_centers(mask, boxes)
        return frame, boxes

    def reset_detector(self):
        self.detector.reset()
        if self.bg is not None:
            self.bg.reset()

    @property
    def last_centers(self):
        if self.bg is not None:
            return self.bg.last_centers
        return self._centers

    def close(self):
        pass


def lock_camera(cam, settle_frames=40, settle_reads=7, verbose=True):
    """Freeze auto-exposure and auto-white-balance at a value that fits the room.

    Why this is not optional
    ------------------------
    Measured on the board with the scene completely still, the camera's auto
    exposure hunts in the dark: the frame mean swung over 26 grey levels with a
    period of several seconds (29 29 29 ... 42 55 51 40 32 29), and the
    foreground mask pixel count tracked it exactly. A global swing that crosses
    the detector's threshold of 30 turns a motionless scene into detections --
    measured at 0.28 boxes/frame with 16.5% of frames reporting something.

    With exposure and white balance frozen the same scene measured a brightness
    span of 0.2 grey levels and 0.00 boxes/frame, and a live run reported
    nothing at all for the first 80 seconds.

    Auto-exposure is switched back ON first
    ---------------------------------------
    It has to be, or a manual value left behind by a previous run is what gets
    frozen. That happened during development: the service read 420 from the
    driver, locked it, and produced a badly underexposed picture (frame mean 29)
    because 420 was a leftover rather than a value auto-exposure had chosen.
    """
    # Force auto-exposure on so the value we read is one it actually picked.
    cam.set(cv2.CAP_PROP_AUTO_WB, 1)
    cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)      # 3 = auto
    for _ in range(settle_frames):
        cam.read()

    samples = []
    for _ in range(settle_reads):
        samples.append(cam.get(cv2.CAP_PROP_EXPOSURE))
        cam.read()
    exposure = float(np.median(samples))

    brightness_samples = []
    for _ in range(3):
        ok, frame = cam.read()
        if ok:
            brightness_samples.append(
                float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
            )
    brightness = float(np.mean(brightness_samples)) if brightness_samples else 0.0

    # AWB first: switching it off shifts the frame, and the exposure value
    # should be frozen against the settled picture, not the shifting one.
    cam.set(cv2.CAP_PROP_AUTO_WB, 0)
    cam.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)      # 1 = manual
    cam.set(cv2.CAP_PROP_EXPOSURE, exposure)
    for _ in range(5):
        cam.read()

    result = {
        "exposure": cam.get(cv2.CAP_PROP_EXPOSURE),
        "auto_exposure": cam.get(cv2.CAP_PROP_AUTO_EXPOSURE),
        "auto_wb": cam.get(cv2.CAP_PROP_AUTO_WB),
        "brightness": float(brightness),
    }
    if verbose:
        print(f"[detect] camera locked: exposure={result['exposure']} "
              f"auto_exposure={result['auto_exposure']} "
              f"auto_wb={result['auto_wb']} "
              f"brightness={result['brightness']:.1f}", flush=True)
    return result


class BoardSource:
    """Real camera plus either detector.

    ``detector="pl"``  the shipped PL pipeline: rgb2gray -> frame_diff ->
                       threshold -> morphology, over AXI DMA. This is the
                       project's original behaviour; it reports changed pixels,
                       so one object comes back as two edge strips.

    ``detector="bg"``  running-average background model in the PS
                       (:mod:`bg_detector`). Reports whole silhouettes, which
                       is what the two reported problems need. The overlay is
                       still downloaded because the buzzer is a PL AXI GPIO.
    """

    name = "board"
    fps_hint = 15.0

    def __init__(self, detector="pl", bg_options=None, lock=True):
        self.detector_mode = detector
        self.bg_options = bg_options or {}
        self.lock = lock
        self.cam = None
        self.inbuf = None
        self.outbuf = None
        self.buzzer = None
        self.demo = None
        self.bg = None
        self._in_bytes = None
        self._out_bytes = None
        self._raw_mask = None
        self._pl_centers = None
        self._mask = None

    def open(self):
        # PYNQ's Overlay/PL server uses asyncio; a worker thread has no event
        # loop by default and Python 3.10 raises instead of creating one.
        import asyncio

        asyncio.set_event_loop(asyncio.new_event_loop())

        from pynq import MMIO, Overlay, allocate

        import pl_motion_detection_optimized as demo

        self.demo = demo
        overlay = Overlay(demo.BITSTREAM, download=False)
        overlay.download()

        if self.detector_mode == "pl":
            self.dma = demo.SimpleAxiDMA(
                demo.DMA_BASE_ADDR,
                poll_sleep_seconds=demo.DMA_POLL_SLEEP_SECONDS,
            )
            self.ips = [
                MMIO(demo.RGB2GRAY_BASE, 0x10000),
                MMIO(demo.FRAME_DIFF_BASE, 0x10000),
                MMIO(demo.THRESHOLD_BASE, 0x10000),
                MMIO(demo.MORPHOLOGY_BASE, 0x10000),
            ]
            demo.configure_ip(self.ips, self.ips[2])

            pixel_count = WIDTH * HEIGHT
            self.nbytes = pixel_count * np.dtype(np.uint32).itemsize
            self.inbuf = allocate(shape=(pixel_count,), dtype=np.uint32)
            self.outbuf = allocate(
                shape=(pixel_count,), dtype=np.uint32, cacheable=True
            )
            self._in_bytes = self.inbuf.view(np.uint8).reshape(HEIGHT, WIDTH, 4)
            self._in_bytes[:, :, 3] = 0
            self._out_bytes = self.outbuf.view(np.uint8).reshape(
                HEIGHT, WIDTH, 4
            )
            self._raw_mask = np.empty((HEIGHT, WIDTH), dtype=np.uint8)
        else:
            self.bg = BackgroundDetector(**self.bg_options)
            # Moving the camera to a different room invalidates more than the
            # background: the exposure and white balance were frozen for the OLD
            # room, so a darker or brighter place stays wrongly exposed forever
            # and detection degrades no matter how well the background adapts.
            # A detected scene change is the right moment to re-lock them for
            # the new environment.
            if self.lock:
                self.bg.on_relearn = self._relock_camera

        self.buzzer = demo.BuzzerGPIO(demo.BUZZER_BASE, enabled=True)

        self.cam = cv2.VideoCapture(0)
        self.cam.set(cv2.CAP_PROP_FRAME_WIDTH, WIDTH)
        self.cam.set(cv2.CAP_PROP_FRAME_HEIGHT, HEIGHT)
        self.cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cam.isOpened():
            raise RuntimeError("cannot open camera 0")
        for _ in range(3):      # flush whatever the driver already buffered
            self.cam.read()

        if self.lock:
            lock_camera(self.cam)
        else:
            print("[detect] camera lock DISABLED: auto-exposure will hunt and "
                  "a still scene will report false targets", flush=True)

    def read(self):
        ok, frame = self.cam.read()
        if not ok:
            return None
        if frame.shape[:2] != (HEIGHT, WIDTH):
            frame = cv2.resize(frame, (WIDTH, HEIGHT))

        if self.detector_mode == "bg":
            _, boxes = self.bg.process(frame)
            return frame, boxes

        cv2.mixChannels([frame], [self._in_bytes], [0, 0, 1, 1, 2, 2])
        for ip in self.ips:
            ip.write(self.demo.AP_CTRL, 0x01)
        self.dma.transfer(self.inbuf, self.outbuf, self.nbytes)
        cv2.mixChannels([self._out_bytes], [self._raw_mask], [0, 0])

        # The shipped chain decides the boxes and is left exactly as it is.
        mask = process_mask(self._raw_mask)
        # Kept for the session recorder, which has to save the exact mask the
        # boxes came from. Recomputing it there would add several milliseconds
        # per frame and drag the recording below the real frame rate.
        self._mask = mask
        boxes = detect_targets(mask)
        # The localisation point is a new feature and reads the same mask
        # without influencing which boxes were produced.
        self._pl_centers = region_centers(mask, boxes)
        return frame, boxes

    def set_alarm(self, alarm):
        if self.buzzer is not None:
            self.buzzer.update(alarm)

    def detector_info(self):
        """Live detector state, so the page can show what it is adapting to."""
        return self.bg.info() if self.bg is not None else {}

    @property
    def last_centers(self):
        """Localisation points for the boxes just returned by ``read``."""
        if self.bg is not None:
            return self.bg.last_centers
        return self._pl_centers

    def _relock_camera(self, fraction, overlap, frames):
        """Re-run the exposure lock for a scene that has changed underneath us."""
        print(f"[detect] scene change at frame {frames} "
              f"(foreground {100 * fraction:.0f}%, overlap {overlap:.2f}); "
              f"re-locking exposure for the new environment", flush=True)
        try:
            lock_camera(self.cam, verbose=True)
        except Exception as exc:
            print(f"[detect] re-lock failed: {type(exc).__name__}: {exc}",
                  flush=True)

    def close(self):
        for fn in (
            lambda: self.buzzer.off() if self.buzzer is not None else None,
            lambda: self.cam.release() if self.cam is not None else None,
            lambda: self.inbuf.freebuffer() if self.inbuf is not None else None,
            lambda: self.outbuf.freebuffer() if self.outbuf is not None else None,
        ):
            try:
                fn()
            except Exception:
                pass


def make_source(name, config):
    if name == "board":
        return BoardSource(
            detector=config["detector"],
            lock=config["lock_camera"],
            bg_options={
                "alpha": config["bg_alpha"],
                "alpha_foreground": config["bg_alpha_fg"],
                "min_area": config["area_floor"],
                "erosion": config["erosion"],
                "close_ksize": config["close_ksize"],
                "adaptive_threshold": not config["fixed_threshold"],
                "threshold": config["threshold"],
                "target_foreground_fraction":
                    config["target_foreground_fraction"],
                "min_threshold": config["min_threshold"],
                "max_threshold": config["max_threshold"],
            },
        )
    if name == "synthetic":
        return SyntheticSource(detector=config["detector"])
    raise ValueError(f"unknown source: {name}")


# --------------------------------------------------------------------------
# shared state
# --------------------------------------------------------------------------

_lock = threading.Lock()
_state = {
    "jpeg": None,
    "seq": 0,
    "fps": 0.0,
    "targets": 0,
    "raw_targets": 0,
    "tracks": 0,
    "track_list": [],
    "alarm": False,
    "frame": 0,
    "ids_created": 0,
    "source": "",
    "detector": "",
    "merge_gap": 0.0,
    "threshold": None,
    "relearning": False,
    "relearn_events": 0,
    "foreground_fraction": 0.0,
    "zone": list(ALARM_ZONE),
    "trail": {},
    "error": None,
    "started": time.time(),
}
_stop = threading.Event()


MIN_ZONE_SIZE = 8


def normalise_zone(values):
    """Clamp a proposed alarm zone to something usable, or reject it.

    The zone arrives from a browser drag, so it can be inverted (the user
    dragged right-to-left), partly off-screen, or a degenerate sliver. Clamping
    here means the rest of the pipeline can trust it.
    """
    if values is None or len(values) != 4:
        raise ValueError("zone must be [x, y, w, h]")

    x, y, w, h = (int(round(float(v))) for v in values)

    # A drag that went right-to-left or bottom-to-top arrives with a negative
    # extent; normalise it rather than rejecting the user's intent.
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h

    x = max(0, min(x, WIDTH - MIN_ZONE_SIZE))
    y = max(0, min(y, HEIGHT - MIN_ZONE_SIZE))
    w = max(MIN_ZONE_SIZE, min(w, WIDTH - x))
    h = max(MIN_ZONE_SIZE, min(h, HEIGHT - y))
    return (x, y, w, h)


def normalise_line(values):
    """Clamp a proposed tripwire to the frame, or reject it.

    Like the alarm zone this arrives from a browser drag, so it can be reversed,
    run off the edge, or collapse to a point (which has no sides at all).
    """
    if values is None or len(values) != 4:
        raise ValueError("line must be [x1, y1, x2, y2]")

    x1, y1, x2, y2 = (int(round(float(v))) for v in values)
    x1 = max(0, min(x1, WIDTH))
    x2 = max(0, min(x2, WIDTH))
    y1 = max(0, min(y1, HEIGHT))
    y2 = max(0, min(y2, HEIGHT))

    if (x1, y1) == (x2, y2):
        raise ValueError("line has zero length")

    # Snap a near-axis line exactly onto the axis: a two-pixel slope looks
    # deliberate on screen but reads as a mistake.
    if abs(x1 - x2) <= 2:
        x1 = x2 = (x1 + x2) // 2
    if abs(y1 - y2) <= 2:
        y1 = y2 = (y1 + y2) // 2
    return (x1, y1, x2, y2)


#: Defaults for the trail tuning, and the ranges the page is allowed to offer.
#: Kept next to the endpoint so the page cannot propose something that would
#: then be rejected.
TRAIL_DEFAULTS = {
    "min_move": 3.0,
    "smooth_window": 7,
    "seconds": 6.0,
    "linger": 1.0,
}
TRAIL_LIMITS = {
    "min_move": (0.0, 20.0),
    "smooth_window": (1, 15),
    "seconds": (1.0, 20.0),
    "linger": (0.0, 5.0),
}


def normalise_trail(values):
    """Validate a proposed trail configuration.

    Each value is clamped rather than rejected, because a slider cannot produce
    anything nonsensical -- but a missing or unparsable one still raises, so a
    malformed request never silently resets the view.
    """
    if not values:
        raise ValueError("no trail settings given")

    result = {}
    for key, (low, high) in TRAIL_LIMITS.items():
        if key not in values or values[key] is None:
            continue
        try:
            number = float(values[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} is not a number")
        if key == "smooth_window":
            # An even window has no centre; step it down to an odd one.
            number = int(round(number))
            if number % 2 == 0:
                number -= 1
        result[key] = max(low, min(high, number))

    if not result:
        raise ValueError("no usable trail settings given")
    return result


#: How aggressively the background model's fragments are joined back together.
#:
#: These defaults are larger than the values they replace (25 px / 0.3). Those
#: were never actually exercised: the bg merge sat behind the PL switch, which
#: defaults to off, so nothing joined anything and one object came back as up to
#: 14 boxes. The numbers below are chosen to close real holes rather than to
#: preserve numbers that were dead on arrival -- and they are adjustable from
#: the page, because how far apart an object's fragments land depends on the
#: scene and is not something to guess at twice.
MERGE_DEFAULTS = {
    "bg_merge_gap": 40.0,
    "bg_merge_scale": 0.6,
}
MERGE_LIMITS = {
    "bg_merge_gap": (0.0, 120.0),
    "bg_merge_scale": (0.0, 2.0),
}


def normalise_merge(values):
    """Validate a proposed fragment-join configuration.

    Clamped rather than rejected, like the trail settings: a slider cannot
    produce anything nonsensical, but a malformed request must not silently
    reset the view either.
    """
    if not values:
        raise ValueError("no merge settings given")

    result = {}
    for key, (low, high) in MERGE_LIMITS.items():
        if key not in values or values[key] is None:
            continue
        try:
            number = float(values[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} is not a number")
        result[key] = max(low, min(high, number))

    if not result:
        raise ValueError("no usable merge settings given")
    return result


def _snapshot_state():
    with _lock:
        return dict(_state)


def _track_to_dict(track):
    x, y, w, h = [int(v) for v in track.box]
    return {
        "id": track.id,
        "box": [x, y, w, h],
        "speed": round(track.speed, 1),
        "direction": track.direction,
        "angle": None if track.angle is None else round(track.angle, 1),
        "velocity": [round(v, 1) for v in track.velocity],
        "hits": track.hits,
        "age": round(track.age, 2),
        "trail": [[int(px), int(py)] for px, py in track.trail],
    }


def detection_loop(source_name, config, pipeline):
    source = None
    try:
        source = make_source(source_name, config)
        print(f"[detect] opening source '{source.name}' ...", flush=True)
        source.open()
        print("[detect] source ready, entering loop", flush=True)

        params = [int(cv2.IMWRITE_JPEG_QUALITY), config["jpeg_quality"]]
        periods = []
        n = 0
        last_push = 0.0
        last_frame_start = None
        min_interval = 1.0 / config["push_hz"] if config["push_hz"] > 0 else 0.0
        frame_period = 1.0 / source.fps_hint
        jpeg_ms = 0.0

        while not _stop.is_set():
            t_frame = time.monotonic()
            result = source.read()
            if result is None:
                if isinstance(source, SyntheticSource):
                    source.index = 0
                    source.reset_detector()
                    continue
                time.sleep(0.02)
                continue
            frame, raw_boxes = result

            n += 1
            # Period between consecutive frame starts, so it covers the camera
            # read on the board and the pacing sleep in synthetic mode alike.
            if last_frame_start is not None:
                elapsed = t_frame - last_frame_start
                if elapsed > 0:
                    periods.append(elapsed)
            last_frame_start = t_frame
            if len(periods) > 30:
                periods.pop(0)
            total = sum(periods)
            # time.monotonic() can be coarser than a fast frame on Windows, so
            # every sample in the window may be 0.0; dividing would raise.
            fps = len(periods) / total if total > 0 else 0.0

            now = time.monotonic()
            if now - last_push >= min_interval:
                last_push = now
                t_draw = time.monotonic()
                vis, info, alarm = pipeline.step(
                    frame, raw_boxes, timestamp=now, fps=fps,
                    detector_info=(source.detector_info()
                                   if hasattr(source, "detector_info")
                                   else None),
                    centers=(source.last_centers
                             if hasattr(source, "last_centers") else None),
                )
                if hasattr(source, "set_alarm"):
                    source.set_alarm(alarm)

                t_jpeg = time.monotonic()
                ok, jpg = cv2.imencode(".jpg", vis, params)
                jpeg_ms = (time.monotonic() - t_jpeg) * 1000.0
                if ok:
                    with _lock:
                        _state["jpeg"] = jpg.tobytes()
                        _state["seq"] += 1
                        _state["fps"] = fps
                        _state["targets"] = info["targets"]
                        _state["raw_targets"] = info["raw_targets"]
                        _state["tracks"] = info["tracks"]
                        _state["ids_created"] = info["ids_created"]
                        _state["alarm"] = info["alarm"]
                        _state["frame"] = n
                        _state["threshold"] = info.get("threshold")
                        _state["relearning"] = bool(
                            info.get("relearning", False))
                        _state["relearn_events"] = info.get(
                            "relearn_events", 0)
                        _state["foreground_fraction"] = info.get(
                            "foreground_fraction", 0.0)
                        _state["trail"] = info.get("trail", {})
                        _state["crossing"] = info.get("crossing", {})
                        _state["grouping"] = info.get("grouping", {})
                        _state["track_list"] = [
                            _track_to_dict(t)
                            for t in pipeline.tracker.active_tracks(
                                pipeline.coast, pipeline.min_speed
                            )
                        ]
                        _state["error"] = None
                del t_draw

            # Synthetic source has no camera to pace it.
            if isinstance(source, SyntheticSource):
                slack = frame_period - (time.monotonic() - t_frame)
                if slack > 0:
                    time.sleep(slack)

    except Exception as exc:
        import traceback

        traceback.print_exc()
        with _lock:
            _state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if source is not None:
            try:
                source.close()
            except Exception:
                pass
        print("[detect] loop exited", flush=True)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>PYNQ-Z2 目标追踪</title>
<style>
  html,body{margin:0;padding:0;background:#111;color:#ddd;
            font-family:system-ui,"Microsoft YaHei",sans-serif}
  .wrap{max-width:1040px;margin:0 auto;padding:14px}
  h1{font-size:16px;font-weight:600;margin:0 0 10px}
  img{width:100%;image-rendering:pixelated;border:1px solid #333;display:block;
      background:#000;min-height:180px}
  .stage{position:relative;touch-action:none}
  #zone{position:absolute;box-sizing:border-box;border:2px solid #fbbf24;
        background:rgba(251,191,36,.10);cursor:move}
  #zone.dirty{border-color:#4ade80;background:rgba(74,222,128,.16)}
  #zone i{position:absolute;width:11px;height:11px;background:#fbbf24;
          border:1px solid #111;box-sizing:border-box}
  #zone i.nw{left:-6px;top:-6px;cursor:nwse-resize}
  #zone i.ne{right:-6px;top:-6px;cursor:nesw-resize}
  #zone i.sw{left:-6px;bottom:-6px;cursor:nesw-resize}
  #zone i.se{right:-6px;bottom:-6px;cursor:nwse-resize}
  #zoneinfo{font-family:ui-monospace,Consolas,monospace;font-size:13px;
            color:#fbbf24;margin-top:6px}
  #linesvg{position:absolute;left:0;top:0;width:100%;height:100%;
           pointer-events:none;z-index:2;overflow:visible}
  #wire{stroke:#22d3ee;stroke-width:2;stroke-dasharray:6 4;
        pointer-events:stroke;cursor:move}
  #linesvg circle{fill:#22d3ee;stroke:#111;stroke-width:1;
                  pointer-events:all;cursor:grab}
  #lineinfo{font-family:ui-monospace,Consolas,monospace;font-size:13px;
            color:#22d3ee;margin-top:4px}
  #crossinfo{font-family:ui-monospace,Consolas,monospace;font-size:14px;
             color:#22d3ee;margin-top:2px}
  .panel{margin-top:10px;padding:10px 12px;border:1px solid #2a2a2a;
         border-radius:6px;background:#161616}
  .panel h2{font-size:13px;font-weight:600;margin:0 0 8px;color:#bbb}
  .row{display:flex;align-items:center;gap:10px;margin-bottom:6px}
  .row label{width:120px;color:#999;font-size:13px}
  .row input[type=range]{flex:1;accent-color:#22d3ee}
  .row output{width:66px;text-align:right;
              font-family:ui-monospace,Consolas,monospace;font-size:13px;
              color:#22d3ee}
  button{background:#262626;color:#ddd;border:1px solid #444;border-radius:4px;
         padding:4px 10px;font-size:13px;cursor:pointer;margin-right:6px}
  button:hover{background:#333}
  #st{margin-top:10px;font-family:ui-monospace,Consolas,monospace;font-size:14px}
  table{width:100%;border-collapse:collapse;margin-top:10px;font-size:14px;
        font-family:ui-monospace,Consolas,monospace}
  th,td{text-align:left;padding:4px 8px;border-bottom:1px solid #262626}
  th{color:#888;font-weight:500}
  .n{color:#4ade80}.a{color:#f87171}.w{color:#fbbf24}
  .tip{color:#888;font-size:13px;margin-top:10px;line-height:1.7}
</style></head>
<body><div class="wrap">
  <h1>PYNQ-Z2 运动目标追踪 —— 实时画面</h1>
  <div class="stage" id="stage">
    <img id="v" alt="live" draggable="false">
    <div id="zone">
      <i class="nw"></i><i class="ne"></i><i class="sw"></i><i class="se"></i>
    </div>
    <svg id="linesvg" viewBox="0 0 320 240" preserveAspectRatio="none">
      <line id="wire" x1="160" y1="40" x2="160" y2="200"></line>
      <circle id="ep1" cx="160" cy="40" r="6"></circle>
      <circle id="ep2" cx="160" cy="200" r="6"></circle>
    </svg>
  </div>
  <div id="crossinfo">越线计数: 读取中 …</div>
  <div id="zoneinfo">警戒区: 读取中 …</div>
  <div style="margin-top:6px">
    <button id="savezone">保存警戒区</button>
    <button id="resetzone">恢复默认</button>
    <button id="resetcross">清零计数</button>
  </div>
  <div class="panel">
    <h2>最近穿越记录（最新在最上面）</h2>
    <table><thead><tr>
      <th>时间</th><th>目标</th><th>方向</th><th>穿越点</th><th>判定</th>
    </tr></thead><tbody id="evtb"><tr><td colspan="5" style="color:#666">
      暂无记录</td></tr></tbody></table>
    <div class="tip" style="margin-top:8px">
      这里记录每一次「疑似穿越」以及程序对它的判定，用来核对计数到底对不对。<br>
      <span class="n">计数</span> = 真正计入合计；
      <span class="w">重复</span> = 同一个物体的另一条边，已忽略（后面写明和哪条轨迹重复、隔了多久、离多远）；
      <span style="color:#888">线段外</span> = 越过了警戒线的延长线、但没碰到你画的那段实线，不计入。<br>
      同一个目标去一次、回一次会各算一次，这是正确的。<br>
      <span class="n">看这一栏就能判断</span>：挥手一次出现两条「计数」= 漏判重复（多算）；
      挥手一次只在一条边出现「计数」、另一条显示「重复」= 正常。
    </div>
  </div>
  <div class="panel">
    <h2>绿框合并（拖动滑块即时生效）</h2>
    <div class="row"><label>合并间距</label>
      <input type="range" id="s-bggap" min="0" max="120" step="5">
      <output id="o-bggap"></output></div>
    <div class="row"><label>合并比例</label>
      <input type="range" id="s-bgscale" min="0" max="2" step="0.1">
      <output id="o-bgscale"></output></div>
    <div style="margin-top:8px">
      <button id="resetmerge">恢复默认</button>
      <span id="mergenote" style="color:#888;font-size:12px;margin-left:8px"></span>
    </div>
    <div class="tip" style="margin-top:8px">
      背景模型会把一个物体<b>切碎</b>（衣服和墙颜色接近的地方留个洞），于是画出一堆绿框。<br>
      <span class="n">合并间距</span> = 两个碎片之间隔多远还认为是同一个物体（调大 → 框变少）。<br>
      <span class="n">合并比例</span> = 按碎片自身大小放大的倍数（调大 → 小碎片也能粘上）。<br>
      调到 <b>0</b> 就是完全不合并。这两个滑块<b>只影响 bg 检测器</b>，队友的 pl 链路不受影响。
    </div>
  </div>
  <div class="panel">
    <h2>轨迹显示（拖动滑块即时生效）</h2>
    <div class="row"><label>消抖距离</label>
      <input type="range" id="s-minmove" min="0" max="20" step="1">
      <output id="o-minmove"></output></div>
    <div class="row"><label>平滑窗口</label>
      <input type="range" id="s-smooth" min="1" max="15" step="2">
      <output id="o-smooth"></output></div>
    <div class="row"><label>历史长度</label>
      <input type="range" id="s-seconds" min="1" max="20" step="1">
      <output id="o-seconds"></output></div>
    <div class="row"><label>消失后滞留</label>
      <input type="range" id="s-linger" min="0" max="5" step="0.5">
      <output id="o-linger"></output></div>
    <div style="margin-top:8px">
      <button id="resettrail">恢复默认</button>
      <span id="trailnote" style="color:#888;font-size:12px;margin-left:8px"></span>
    </div>
  </div>
  <div id="st">连接中 ...</div>
  <table><thead><tr>
    <th>ID</th><th>方向</th><th>速度</th><th>位置</th><th>尺寸</th><th>命中</th>
  </tr></thead><tbody id="tb"><tr><td colspan="6" style="color:#666">
    暂无目标</td></tr></tbody></table>
  <div class="tip">
    绿色框 = 被追踪的目标（同一目标 ID 保持不变）；黄色箭头 = 运动方向；
    青色线 = 轨迹。<br>
    源: __SOURCE__　检测: __DETECTOR__　合并间距: __MERGE_GAP__ px　
    <span class="n">速度单位为 px/s，未做真实尺度标定</span>。<br>
    每帧一次短 HTTP 请求（不依赖长连接），失败自动重试。
  </div>
</div>
<script>
const POLL_MS = __POLL_MS__;
const img = document.getElementById('v');
const st  = document.getElementById('st');
const tb  = document.getElementById('tb');
let okCount = 0, errCount = 0, lastOk = Date.now();

function nextFetch(){ setTimeout(fetchFrame, POLL_MS); }

function fetchFrame(){
  const im = new Image();
  im.onload = () => { img.src = im.src; okCount++; lastOk = Date.now(); nextFetch(); };
  im.onerror = () => { errCount++; setTimeout(fetchFrame, 300); };
  im.src = '/snapshot?t=' + Date.now();
}
img.onerror = () => setTimeout(fetchFrame, 300);
fetchFrame();

// ---- alarm zone: drag to move, drag a corner to resize ----
const stage = document.getElementById('stage');
const zoneEl = document.getElementById('zone');
const zoneInfo = document.getElementById('zoneinfo');
let IMG = {w: 320, h: 240};
let zone = [0, 0, 1, 1];        // image pixel coordinates
let dirty = false;

function clamp(v, lo, hi){ return Math.max(lo, Math.min(hi, v)); }

function drawZone(){
  zoneEl.style.left   = (100 * zone[0] / IMG.w) + '%';
  zoneEl.style.top    = (100 * zone[1] / IMG.h) + '%';
  zoneEl.style.width  = (100 * zone[2] / IMG.w) + '%';
  zoneEl.style.height = (100 * zone[3] / IMG.h) + '%';
  zoneInfo.textContent = `警戒区: x=${zone[0]} y=${zone[1]} `
    + `w=${zone[2]} h=${zone[3]}  (图像 ${IMG.w}x${IMG.h})`
    + (dirty ? '  — 未保存' : '');
  zoneEl.classList.toggle('dirty', dirty);
}

async function loadZone(){
  try {
    const r = await fetch('/zone', {cache:'no-store'});
    const z = await r.json();
    IMG = {w: z.width, h: z.height};
    if (!dirty){ zone = z.zone.slice(); drawZone(); }
  } catch(e){}
}

async function saveZone(){
  try {
    const r = await fetch('/zone', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({zone}),
    });
    const z = await r.json();
    // The server clamps to the frame; adopt what it actually stored.
    zone = z.zone.slice(); dirty = false; drawZone();
  } catch(e){ zoneInfo.textContent = '保存失败: ' + e; }
}

zoneEl.addEventListener('pointerdown', ev => {
  const handle = ev.target.tagName === 'I' ? ev.target.className : null;
  ev.preventDefault();
  zoneEl.setPointerCapture(ev.pointerId);

  const rect = stage.getBoundingClientRect();
  const sx = IMG.w / rect.width, sy = IMG.h / rect.height;
  const start = {mx: ev.clientX, my: ev.clientY, zone: zone.slice()};

  function move(e){
    const dx = (e.clientX - start.mx) * sx;
    const dy = (e.clientY - start.my) * sy;
    const [x, y, w, h] = start.zone;
    if (handle === 'nw')      zone = [x+dx, y+dy, w-dx, h-dy];
    else if (handle === 'ne') zone = [x,    y+dy, w+dx, h-dy];
    else if (handle === 'sw') zone = [x+dx, y,    w-dx, h+dy];
    else if (handle === 'se') zone = [x,    y,    w+dx, h+dy];
    else                      zone = [x+dx, y+dy, w, h];
    // Keep it usable while dragging; the server clamps properly on save.
    zone[0] = Math.round(clamp(zone[0], 0, IMG.w - 8));
    zone[1] = Math.round(clamp(zone[1], 0, IMG.h - 8));
    zone[2] = Math.round(clamp(zone[2], 8, IMG.w - zone[0]));
    zone[3] = Math.round(clamp(zone[3], 8, IMG.h - zone[1]));
    dirty = true;
    drawZone();
  }
  function up(){
    zoneEl.removeEventListener('pointermove', move);
    zoneEl.removeEventListener('pointerup', up);
  }
  zoneEl.addEventListener('pointermove', move);
  zoneEl.addEventListener('pointerup', up);
});

document.getElementById('savezone').onclick = saveZone;
document.getElementById('resetzone').onclick = async () => {
  const r = await fetch('/zone', {cache:'no-store'});
  const z = await r.json();
  zone = z['default'].slice(); dirty = true; drawZone(); await saveZone();
};

loadZone();
setInterval(() => { if (!dirty) loadZone(); }, 5000);

// ---- tripwire: drag an endpoint, or the wire itself to move it ----
const wire = document.getElementById('wire');
const ep1 = document.getElementById('ep1');
const ep2 = document.getElementById('ep2');
const crossInfo = document.getElementById('crossinfo');
let LINE = [160, 40, 160, 200];
let lineDirty = false;
let counterEnabled = false;

function drawLine(){
  wire.setAttribute('x1', LINE[0]); wire.setAttribute('y1', LINE[1]);
  wire.setAttribute('x2', LINE[2]); wire.setAttribute('y2', LINE[3]);
  ep1.setAttribute('cx', LINE[0]); ep1.setAttribute('cy', LINE[1]);
  ep2.setAttribute('cx', LINE[2]); ep2.setAttribute('cy', LINE[3]);
}

async function loadLine(){
  try {
    const r = await fetch('/line', {cache:'no-store'});
    const z = await r.json();
    counterEnabled = !!z.enabled;
    if (counterEnabled && !lineDirty){ LINE = z.line.slice(); drawLine(); }
  } catch(e){}
}

async function saveLine(){
  try {
    const r = await fetch('/line', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({line: LINE}),
    });
    if (!r.ok){ crossInfo.textContent = '保存失败: ' + r.status; return; }
    const z = await r.json();
    LINE = z.line.slice(); lineDirty = false; drawLine();
    refreshCrossings();
  } catch(e){ crossInfo.textContent = '保存失败: ' + e; }
}

function dragHandle(target, onMove){
  target.addEventListener('pointerdown', ev => {
    ev.preventDefault(); ev.stopPropagation();
    target.setPointerCapture(ev.pointerId);
    const rect = stage.getBoundingClientRect();
    const sx = IMG.w / rect.width, sy = IMG.h / rect.height;
    const start = {mx: ev.clientX, my: ev.clientY, line: LINE.slice()};
    function move(e){
      const dx = (e.clientX - start.mx) * sx;
      const dy = (e.clientY - start.my) * sy;
      LINE = onMove(start.line, dx, dy);
      lineDirty = true;
      drawLine(); refreshCrossings();
    }
    function up(){
      target.removeEventListener('pointermove', move);
      target.removeEventListener('pointerup', up);
      saveLine();
    }
    target.addEventListener('pointermove', move);
    target.addEventListener('pointerup', up);
  });
}

const clampPt = (x, y) => [Math.round(clamp(x, 0, IMG.w)), Math.round(clamp(y, 0, IMG.h))];

dragHandle(ep1, (l, dx, dy) => {
  const [x, y] = clampPt(l[0] + dx, l[1] + dy);
  return [x, y, l[2], l[3]];
});
dragHandle(ep2, (l, dx, dy) => {
  const [x, y] = clampPt(l[2] + dx, l[3] + dy);
  return [l[0], l[1], x, y];
});
dragHandle(wire, (l, dx, dy) => {
  const [x1, y1] = clampPt(l[0] + dx, l[1] + dy);
  const [x2, y2] = clampPt(l[2] + dx, l[3] + dy);
  return [x1, y1, x2, y2];
});

const evBody = document.getElementById('evtb');
const OUTCOME_NAME = {
  counted:    ['计数',    'n'],
  duplicate:  ['重复',    'w'],
  outside:    ['线段外',  ''],
  degenerate: ['无法定位', ''],
};

function esc(s){
  return String(s).replace(/[&<>]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[ch]));
}

function ageText(now, t){
  if (t === null || t === undefined) return '—';
  const age = Math.max(0, now - t);
  if (age < 60) return age.toFixed(1) + ' 秒前';
  return Math.floor(age / 60) + ' 分 ' + Math.round(age % 60) + ' 秒前';
}

function renderEvents(c){
  const evs = (c.events || []).slice().reverse();   // newest first
  if (!evs.length){
    evBody.innerHTML = '<tr><td colspan="5" style="color:#666">'
      + '暂无记录 —— 还没有目标穿过警戒线</td></tr>';
    return;
  }
  evBody.innerHTML = evs.map(ev => {
    const pair = OUTCOME_NAME[ev.outcome] || [ev.outcome, ''];
    const pt = ev.point
      ? `(${ev.point[0].toFixed(0)}, ${ev.point[1].toFixed(0)})` : '—';
    const detail = ev.detail
      ? `<span style="color:#777">　${esc(ev.detail)}</span>` : '';
    return `<tr><td>${ageText(c.now, ev.t)}</td><td>#${ev.id}</td>`
         + `<td>${esc(ev.label)}</td><td>${pt}</td>`
         + `<td class="${pair[1]}">${pair[0]}${detail}</td></tr>`;
  }).join('');
}

async function refreshCrossings(){
  let c = null;
  try {
    const r = await fetch('/crossings', {cache:'no-store'});
    c = await r.json();
  } catch(e){ return; }
  if (!c.enabled){
    crossInfo.textContent = '越线计数: 已关闭';
    evBody.innerHTML = '<tr><td colspan="5" style="color:#666">'
      + '越线计数已关闭（启动时加了 --line none）</td></tr>';
    return;
  }
  const [a, b] = c.labels;
  crossInfo.textContent =
    `越线计数:  ${a} ${c.counts[0]}   ${b} ${c.counts[1]}   合计 ${c.total}`
    + (lineDirty ? '   — 线路未保存' : '');
  renderEvents(c);
}

document.getElementById('resetcross').onclick = async () => {
  await fetch('/crossings/reset', {method:'POST'});
  refreshCrossings();
};

loadLine();
setInterval(refreshCrossings, 1000);

// ---- trail tuning: sliders that apply as soon as they are released ----
const sliders = {
  min_move:      {el: document.getElementById('s-minmove'),
                  out: document.getElementById('o-minmove'), unit: 'px'},
  smooth_window: {el: document.getElementById('s-smooth'),
                  out: document.getElementById('o-smooth'), unit: ''},
  seconds:       {el: document.getElementById('s-seconds'),
                  out: document.getElementById('o-seconds'), unit: 's'},
  linger:        {el: document.getElementById('s-linger'),
                  out: document.getElementById('o-linger'), unit: 's'},
};
const trailNote = document.getElementById('trailnote');
let trailDefaults = null;

function showTrail(values){
  for (const [key, item] of Object.entries(sliders)){
    if (values[key] === undefined) continue;
    item.el.value = values[key];
    item.out.textContent = values[key] + item.unit;
  }
  trailNote.textContent =
    '消抖是距离：调大会让轨迹一段段跳着长；历史变长不会找回已丢弃的点';
}

async function loadTrail(){
  try {
    const r = await fetch('/trail', {cache:'no-store'});
    const t = await r.json();
    trailDefaults = t.default;
    showTrail(t.settings);
  } catch(e){}
}

function applyTrail(key, value){
  const body = {};
  body[key] = value;
  // Show the chosen value immediately; the server clamps, and the reply
  // replaces it with what was actually stored.
  sliders[key].out.textContent = value + sliders[key].unit;
  fetch('/trail', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body),
  }).then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(t => showTrail(t.settings))
    .catch(e => { trailNote.textContent = '应用失败: ' + e; });
}

for (const [key, item] of Object.entries(sliders)){
  item.el.addEventListener('input', () => {
    item.out.textContent = item.el.value + item.unit;
  });
  item.el.addEventListener('change', () => applyTrail(key, item.el.value));
}

document.getElementById('resettrail').onclick = () => {
  if (!trailDefaults) return;
  fetch('/trail', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(trailDefaults),
  }).then(r => r.json()).then(t => showTrail(t.settings));
};

loadTrail();

// ---- fragment joining: the control for "one object, too many green boxes" ----
const mergeSliders = {
  bg_merge_gap:   {el: document.getElementById('s-bggap'),
                   out: document.getElementById('o-bggap'), unit: 'px'},
  bg_merge_scale: {el: document.getElementById('s-bgscale'),
                   out: document.getElementById('o-bgscale'), unit: ''},
};
const mergeNote = document.getElementById('mergenote');
let mergeDefaults = null;

function showMerge(values){
  for (const [key, item] of Object.entries(mergeSliders)){
    if (values[key] === undefined) continue;
    item.el.value = values[key];
    item.out.textContent = values[key] + item.unit;
  }
  mergeNote.textContent = (values.detector === 'bg')
    ? '当前检测器 bg —— 这两个滑块生效'
    : `当前检测器 ${values.detector} —— 这不影响队友的 pl 链路`;
}

function applyMerge(key, value){
  const body = {};
  body[key] = value;
  mergeSliders[key].out.textContent = value + mergeSliders[key].unit;
  fetch('/merge', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body),
  }).then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(t => showMerge(t.settings))
    .catch(e => { mergeNote.textContent = '应用失败: ' + e; });
}

for (const [key, item] of Object.entries(mergeSliders)){
  item.el.addEventListener('input', () => {
    item.out.textContent = item.el.value + item.unit;
  });
  item.el.addEventListener('change', () => applyMerge(key, item.el.value));
}

document.getElementById('resetmerge').onclick = () => {
  if (!mergeDefaults) return;
  fetch('/merge', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(mergeDefaults),
  }).then(r => r.json()).then(t => showMerge(t.settings));
};

fetch('/merge', {cache:'no-store'}).then(r => r.json()).then(m => {
  mergeDefaults = m.default;
  showMerge(m.settings);
}).catch(() => {});

const DIR = {E:'右 E', W:'左 W', N:'上 N', S:'下 S',
             NE:'右上 NE', NW:'左上 NW', SE:'右下 SE', SW:'左下 SW', '?':'静止'};

setInterval(async () => {
  try {
    const r = await fetch('/status', {cache:'no-store'});
    const s = await r.json();
    const stale = (Date.now() - lastOk) / 1000;
    const al = s.alarm ? '<span class="a">ALARM</span>'
                       : '<span class="n">Normal</span>';
    let extra = `　取帧 ${okCount} 失败 ${errCount}`;
    if (stale > 3) extra += `　<span class="w">画面已停 ${stale.toFixed(0)}s</span>`;
    if (s.error) extra += `　<span class="a">服务错误: ${s.error}</span>`;
    st.innerHTML = `帧 ${s.frame}　检测FPS ${s.fps.toFixed(1)}　`
      + `追踪目标 ${s.tracks}　(原始 ${s.raw_targets})　累计ID ${s.ids_created}　`
      + `阈值 ${s.threshold === null ? '-' : s.threshold.toFixed(0)}　`
      + `${al}` + extra
      + (s.relearning
         ? '　<span class="w">正在重新学习背景…</span>' : '')
      + (s.relearn_events ? `　重学 ${s.relearn_events} 次` : '');

    const rows = s.track_list.map(t => {
      const [x,y,w,h] = t.box;
      return `<tr><td>#${t.id}</td><td>${DIR[t.direction] || t.direction}</td>`
        + `<td>${t.speed.toFixed(0)} px/s</td><td>${x},${y}</td>`
        + `<td>${w}x${h}</td><td>${t.hits}</td></tr>`;
    }).join('');
    tb.innerHTML = rows || '<tr><td colspan="6" style="color:#666">暂无目标</td></tr>';
  } catch(e) {}
}, 500);
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send_bytes(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            state = _snapshot_state()
            body = (
                PAGE.replace("__POLL_MS__", str(self.server.poll_ms))
                .replace("__SOURCE__", str(state.get("source", "")))
                .replace("__DETECTOR__", str(state.get("detector", "")))
                .replace("__MERGE_GAP__", f"{state.get('merge_gap', 0):g}")
                .encode("utf-8")
            )
            self._send_bytes(body, "text/html; charset=utf-8")
            return

        if path == "/status":
            s = _snapshot_state()
            s["uptime"] = time.time() - s.pop("started", time.time())
            s.pop("jpeg", None)
            s.pop("track_list", None)
            self._send_bytes(json.dumps(s).encode(), "application/json")
            return

        if path == "/tracks":
            s = _snapshot_state()
            payload = {
                "frame": s["frame"],
                "tracks": s.get("track_list", []),
            }
            self._send_bytes(
                json.dumps(payload).encode(), "application/json"
            )
            return

        if path == "/zone":
            s = _snapshot_state()
            self._send_bytes(json.dumps({
                "zone": s.get("zone", list(ALARM_ZONE)),
                "default": list(ALARM_ZONE),
                "width": WIDTH,
                "height": HEIGHT,
                "min_size": MIN_ZONE_SIZE,
            }).encode(), "application/json")
            return

        if path == "/line":
            counter = self.server.pipeline.counter
            if counter is None:
                self._send_bytes(json.dumps({
                    "enabled": False, "width": WIDTH, "height": HEIGHT,
                    "default": list(DEFAULT_LINE),
                }).encode(), "application/json")
                return
            self._send_bytes(json.dumps({
                "enabled": True,
                "line": list(counter.line),
                "default": list(DEFAULT_LINE),
                "width": WIDTH,
                "height": HEIGHT,
                "dedup_seconds": counter.dedup_seconds,
                "dedup_distance": counter.dedup_distance,
            }).encode(), "application/json")
            return

        if path == "/crossings":
            counter = self.server.pipeline.counter
            payload = counter.info(recent=20) if counter is not None else {
                "enabled": False, "counts": [0, 0], "total": 0,
            }
            payload["enabled"] = counter is not None
            # The counters live on the frame clock, which means nothing to a
            # reader; the page subtracts this to say "3.2 s ago".
            payload["now"] = round(time.monotonic(), 2)
            self._send_bytes(json.dumps(payload).encode(),
                             "application/json")
            return

        if path == "/merge":
            self._send_bytes(json.dumps({
                "settings": self.server.pipeline.merge_settings(),
                "default": MERGE_DEFAULTS,
                "limits": {k: list(v) for k, v in MERGE_LIMITS.items()},
            }).encode(), "application/json")
            return

        if path == "/trail":
            store = self.server.pipeline.trails
            self._send_bytes(json.dumps({
                "settings": store.settings(),
                "default": TRAIL_DEFAULTS,
                "limits": {k: list(v) for k, v in TRAIL_LIMITS.items()},
                "stats": store.stats(),
            }).encode(), "application/json")
            return

        if path == "/snapshot":
            with _lock:
                jpg = _state["jpeg"]
            if not jpg:
                self.send_error(503, "no frame yet")
                return
            self._send_bytes(jpg, "image/jpeg")
            return

        if path == "/stream":
            self._stream()
            return

        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/zone":
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 4096:
                self.send_error(400, "bad body")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode())
                zone = normalise_zone(payload.get("zone"))
            except (ValueError, TypeError, KeyError) as exc:
                self.send_error(400, f"invalid zone: {exc}")
                return

            # A tuple assignment is atomic in CPython, so the detection loop
            # always sees either the old zone or the new one, never a mix.
            self.server.pipeline.zone = zone
            with _lock:
                _state["zone"] = list(zone)
            print(f"[http] alarm zone set to {zone}", flush=True)
            self._send_bytes(
                json.dumps({"zone": list(zone)}).encode(), "application/json"
            )
            return

        if path == "/line":
            counter = self.server.pipeline.counter
            if counter is None:
                self.send_error(409, "counting is disabled")
                return
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 4096:
                self.send_error(400, "bad body")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode())
                line = normalise_line(payload.get("line"))
            except (ValueError, TypeError, KeyError) as exc:
                self.send_error(400, f"invalid line: {exc}")
                return

            counter.set_line(line)
            print(f"[http] tripwire set to {line} (counts reset)", flush=True)
            self._send_bytes(
                json.dumps({"line": list(line)}).encode(), "application/json"
            )
            return

        if path == "/crossings/reset":
            counter = self.server.pipeline.counter
            if counter is None:
                self.send_error(409, "counting is disabled")
                return
            counter.reset_counts()
            print("[http] crossing counts reset", flush=True)
            self._send_bytes(b'{"total": 0}', "application/json")
            return

        if path == "/merge":
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 4096:
                self.send_error(400, "bad body")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode())
                settings = normalise_merge(payload)
            except (ValueError, TypeError) as exc:
                self.send_error(400, f"invalid merge settings: {exc}")
                return

            pipeline = self.server.pipeline
            applied = pipeline.configure_merge(**settings)
            print(f"[http] merge settings -> {applied}", flush=True)
            self._send_bytes(
                json.dumps({"settings": applied}).encode(), "application/json")
            return

        if path == "/trail":
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 4096:
                self.send_error(400, "bad body")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode())
                settings = normalise_trail(payload)
            except (ValueError, TypeError) as exc:
                self.send_error(400, f"invalid trail settings: {exc}")
                return

            store = self.server.pipeline.trails
            store.configure(**settings)
            print(f"[http] trail settings -> {store.settings()}", flush=True)
            self._send_bytes(
                json.dumps({"settings": store.settings()}).encode(),
                "application/json",
            )
            return

        self.send_error(404)

    def _stream(self):
        self.send_response(200)
        self.send_header(
            "Content-Type", "multipart/x-mixed-replace; boundary=frame"
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.connection.settimeout(5)
        except Exception:
            pass

        last_seq = -1
        sent = 0
        try:
            while not _stop.is_set():
                with _lock:
                    seq = _state["seq"]
                    jpg = _state["jpeg"]
                if jpg is None or seq == last_seq:
                    time.sleep(0.004)
                    continue
                last_seq = seq
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                self.wfile.write(
                    b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                )
                self.wfile.write(jpg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                sent += 1
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        finally:
            print(f"[http] stream client done (sent {sent})", flush=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = True
    poll_ms = 140


def parse_args():
    parser = argparse.ArgumentParser(description="PYNQ-Z2 tracking demo")
    parser.add_argument("--source", default=os.environ.get("SOURCE", "board"),
                        choices=("board", "synthetic"))
    parser.add_argument("--detector",
                        default=os.environ.get("DETECTOR", "pl"),
                        choices=("pl", "bg"),
                        help="pl = the shipped frame-difference chain, which is "
                             "the default because its box decision was already "
                             "tuned as a compromise; bg = the background model, "
                             "kept for comparison")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", "8081")))
    parser.add_argument("--merge-gap", type=float,
                        default=float(os.environ.get("MERGE_GAP", "0")),
                        help="extra merging applied on top of the detector's "
                             "own boxes. 0 (default) leaves the shipped chain's "
                             "boxes exactly as they are; for the PL detector a "
                             "non-zero value fuses an object's two edge strips")
    parser.add_argument("--bg-merge-gap", type=float,
                        default=float(os.environ.get("BG_MERGE_GAP", "40")),
                        help="largest gap the bg detector may join; a background "
                             "model fragments on holes INSIDE an object, so this "
                             "is much smaller than the edge-strip gap. This is "
                             "what stops one object being reported as many "
                             "boxes; set 0 to switch the joining off")
    parser.add_argument("--bg-merge-scale", type=float,
                        default=float(os.environ.get("BG_MERGE_SCALE", "0.6")),
                        help="gap allowance as a fraction of the smaller box "
                             "side; this is what usually binds")
    parser.add_argument("--area-floor", type=float,
                        default=float(os.environ.get("AREA_FLOOR", "300")),
                        help="contour area floor for the bg detector")
    parser.add_argument("--bg-alpha", type=float,
                        default=float(os.environ.get("BG_ALPHA", "0.06")))
    parser.add_argument("--bg-alpha-fg", type=float,
                        default=float(os.environ.get("BG_ALPHA_FG",
                                                     "0.002")),
                        help="foreground adaptation rate; low values stop a "
                             "moving object leaving a ghost trail, at the cost "
                             "of a still object never being absorbed")
    parser.add_argument("--erosion", action="store_true",
                        default=os.environ.get("EROSION", "") not in ("", "0"),
                        help="apply the PL chain's 3x3 erosion in bg mode; "
                             "off by default (costs 2.8 ms and shrinks objects)")
    parser.add_argument("--close-ksize", type=int,
                        default=int(os.environ.get("CLOSE_KSIZE", "1")),
                        help="close kernel for bg mode; 1 disables it")
    parser.add_argument("--alarm-on", default=os.environ.get("ALARM_ON",
                                                             "tracks"),
                        choices=("tracks", "detections"),
                        help="tracks = only a confirmed track raises the alarm "
                             "(default); detections = the original behaviour of "
                             "alarming on any single frame")
    parser.add_argument("--max-box-fraction", type=float,
                        default=float(os.environ.get("MAX_BOX_FRACTION", "0.5")),
                        help="largest box, as a fraction of the frame, that may "
                             "be reported as a target (applied again after "
                             "grouping)")
    parser.add_argument("--min-speed", type=float,
                        default=float(os.environ.get("MIN_SPEED", "4")),
                        help="px/s below which a track is not reported; a "
                             "curtain stirred by air produces a real but "
                             "almost motionless target (0 disables)")
    parser.add_argument("--target-foreground-fraction", type=float,
                        default=float(os.environ.get(
                            "TARGET_FOREGROUND_FRACTION", "0.002")),
                        help="fraction of background pixels the calibrated "
                             "threshold aims to leave above it; lower = higher "
                             "threshold = less sensitive")
    parser.add_argument("--min-threshold", type=int,
                        default=int(os.environ.get("MIN_THRESHOLD", "6")))
    parser.add_argument("--max-threshold", type=int,
                        default=int(os.environ.get("MAX_THRESHOLD", "45")))
    parser.add_argument("--fixed-threshold", action="store_true",
                        default=os.environ.get("FIXED_THRESHOLD", "") not in
                        ("", "0"),
                        help="disable adaptive calibration and use --threshold")
    parser.add_argument("--threshold", type=int,
                        default=int(os.environ.get("THRESHOLD", "30")),
                        help="threshold used with --fixed-threshold")
    parser.add_argument("--prediction-weight", type=float,
                        default=float(os.environ.get("PREDICTION_WEIGHT",
                                                     "0.3")),
                        help="0 = match on the last observed box (stable but "
                             "can slide onto still background), 1 = match on "
                             "the predicted position only (resists sliding but "
                             "brittle when detections jitter)")
    parser.add_argument("--no-camera-lock", action="store_true",
                        default=os.environ.get("NO_CAMERA_LOCK", "") not in
                        ("", "0"),
                        help="do NOT freeze exposure/white balance; only for "
                             "demonstrating what auto-exposure does")
    parser.add_argument("--coast", type=int,
                        default=int(os.environ.get("COAST", "0")))
    parser.add_argument("--show-raw", action="store_true",
                        default=os.environ.get("SHOW_RAW", "") not in ("", "0"))
    parser.add_argument("--push-hz", type=float,
                        default=float(os.environ.get("PUSH_HZ", "10")))
    parser.add_argument("--jpeg-quality", type=int,
                        default=int(os.environ.get("JPEG_QUALITY", "75")))
    parser.add_argument("--poll-ms", type=int,
                        default=int(os.environ.get("POLL_MS", "140")))
    parser.add_argument("--zone", type=str,
                        default=os.environ.get("ZONE", ""),
                        help="initial alarm zone as x,y,w,h; also adjustable "
                             "from the web page by dragging")
    parser.add_argument("--trail-min-move", type=float,
                        default=float(os.environ.get("TRAIL_MIN_MOVE", "3")),
                        help="px the target must move before a trail point is "
                             "recorded. This is the debounce and it is a "
                             "DISTANCE, not a time: a large value makes the "
                             "trail grow in visible steps. 0 records every "
                             "frame")
    parser.add_argument("--trail-smooth", type=int,
                        default=int(os.environ.get("TRAIL_SMOOTH", "7")),
                        help="window of the moving average applied to the "
                             "recorded points before the curve is fitted; 1 "
                             "turns it off")
    parser.add_argument("--trail-seconds", type=float,
                        default=float(os.environ.get("TRAIL_SECONDS", "6")),
                        help="how much history the trail shows, in seconds. "
                             "Time rather than a point count, so the trail "
                             "lasts the same however fast the target moves")
    parser.add_argument("--trail-linger", type=float,
                        default=float(os.environ.get("TRAIL_LINGER", "1")),
                        help="seconds a trail keeps being drawn after its "
                             "track disappears; 0 clears it immediately")
    parser.add_argument("--line", type=str,
                        default=os.environ.get("LINE", ""),
                        help="tripwire as x1,y1,x2,y2; also adjustable from the "
                             "web page. Default is a vertical line down the "
                             "middle. Use 'none' to disable counting")
    parser.add_argument("--confirm-distance", type=float,
                        default=float(os.environ.get("CONFIRM_DISTANCE", "12")),
                        help="how far past the line a target must actually "
                             "travel before the crossing counts. Set 0 to "
                             "count on the sign change alone, as before")
    parser.add_argument("--confirm-seconds", type=float,
                        default=float(os.environ.get("CONFIRM_SECONDS", "1")),
                        help="how long a crossing may wait to be confirmed")
    parser.add_argument("--no-group-strips", action="store_true",
                        default=os.environ.get("NO_GROUP_STRIPS", "") != "",
                        help="do not pair the leading and trailing edge of one "
                             "object; then both edges count separately again")
    return parser.parse_args()


def parse_zone(text):
    if not text:
        return None
    parts = [p for p in text.replace(" ", "").split(",") if p]
    if len(parts) != 4:
        raise ValueError("--zone needs four numbers: x,y,w,h")
    return normalise_zone(parts)


def parse_line(text):
    if not text:
        return None
    if text.strip().lower() in ("none", "off", "0"):
        return "none"
    parts = [p for p in text.replace(" ", "").split(",") if p]
    if len(parts) != 4:
        raise ValueError("--line needs four numbers: x1,y1,x2,y2")
    return normalise_line(parts)


def main():
    args = parse_args()
    try:
        args.zone = parse_zone(args.zone)
        args.line = parse_line(args.line)
    except ValueError as exc:
        raise SystemExit(f"argument: {exc}")

    config = {
        "merge_gap": args.merge_gap,
        "coast": args.coast,
        "show_raw": args.show_raw,
        "jpeg_quality": args.jpeg_quality,
        "push_hz": args.push_hz,
        "detector": args.detector,
        "area_floor": args.area_floor,
        "bg_alpha": args.bg_alpha,
        "bg_alpha_fg": args.bg_alpha_fg,
        "erosion": args.erosion,
        "close_ksize": args.close_ksize,
        "lock_camera": not args.no_camera_lock,
        "alarm_on": args.alarm_on,
        "max_box_fraction": args.max_box_fraction,
        "min_speed": args.min_speed,
        "prediction_weight": args.prediction_weight,
        "bg_merge_gap": args.bg_merge_gap,
        "bg_merge_scale": args.bg_merge_scale,
        "target_foreground_fraction": args.target_foreground_fraction,
        "min_threshold": args.min_threshold,
        "max_threshold": args.max_threshold,
        "fixed_threshold": args.fixed_threshold,
        "threshold": args.threshold,
    }

    with _lock:
        _state["source"] = args.source
        _state["detector"] = args.detector
        _state["merge_gap"] = args.merge_gap

    # Built before the banner, because the banner reports what it will do.
    pipeline = TrackerPipeline(
        merge_gap=args.merge_gap,
        coast=args.coast,
        show_raw=args.show_raw,
        alarm_on=args.alarm_on,
        detector=args.detector,
        max_box_fraction=args.max_box_fraction,
        min_speed=args.min_speed,
        prediction_weight=args.prediction_weight,
        bg_merge_gap=args.bg_merge_gap,
        bg_merge_scale=args.bg_merge_scale,
        zone=args.zone,
        line=None if args.line in (None, "none") else args.line,
        crossing=args.line != "none",
        confirm_distance=args.confirm_distance,
        confirm_seconds=args.confirm_seconds,
        group_strips=not args.no_group_strips,
        trail={"min_move": args.trail_min_move,
               "smooth_window": args.trail_smooth,
               "seconds": args.trail_seconds,
               "linger": args.trail_linger},
    )
    if pipeline.counter is not None:
        _state["crossing"] = pipeline.counter.info()

    print("=" * 58)
    print("PYNQ-Z2 运动目标追踪演示")
    print(f"  数据源      : {args.source}")
    print(f"  检测方式    : {args.detector} "
          f"({'PL 帧差' if args.detector == 'pl' else 'PS 背景模型'})")
    print(f"  端口        : {args.port}")
    print(f"  合并间距    : {args.merge_gap} px (0 = 关闭)")
    if args.detector == "bg":
        print(f"  背景合并    : 间距 {args.bg_merge_gap} px，"
              f"比例 {args.bg_merge_scale}")
    if args.detector == "bg":
        print(f"  背景 alpha  : {args.bg_alpha} "
              f"(前景 {args.bg_alpha_fg})")
        print(f"  面积下限    : {args.area_floor}")
        print(f"  腐蚀 / 闭运算: {args.erosion} / k={args.close_ksize}")
        if args.fixed_threshold:
            print(f"  阈值        : 固定 {args.threshold}（关闭自适应）")
        else:
            print(f"  阈值        : 自适应 "
                  f"{args.min_threshold}~{args.max_threshold}，"
                  f"目标前景占比 {args.target_foreground_fraction}")
    print(f"  锁定曝光/AWB: {not args.no_camera_lock}")
    print(f"  报警依据    : {args.alarm_on}")
    print(f"  警戒区      : {args.zone or ALARM_ZONE} "
          f"(可在网页上拖拽修改)")
    if pipeline.counter is not None:
        print(f"  越线计数    : 线 {pipeline.counter.line}"
              f"  方向 {pipeline.counter.labels()}"
              f"  (可在网页上拖拽修改)")
        print(f"  确认距离    : {pipeline.counter.confirm_distance:.0f} px"
              f"（越线后必须真的走出这么远才算数；0 = 一越线就算）")
    else:
        print("  越线计数    : 已关闭")
    print(f"  轨迹显示    : 消抖 {pipeline.trails.min_move:.0f}px，"
          f"平滑窗口 {pipeline.trails.smooth_window}，"
          f"保留 {pipeline.trails.seconds:.0f}s，"
          f"滞留 {pipeline.trails.linger:.0f}s")
    print(f"  最小速度    : {args.min_speed} px/s "
          f"(低于此值不上报，0=关闭)")
    print(f"  预测权重    : {args.prediction_weight} "
          f"(0=看上一帧, 1=只看预测)")
    print(f"  最大框占比  : {args.max_box_fraction:.0%}")
    print(f"  轨迹保留    : coast={args.coast} 帧")
    print(f"  显示原始框  : {args.show_raw}")
    print(f"  浏览器打开  : http://<板卡IP>:{args.port}/")
    print("=" * 58, flush=True)

    with _lock:
        _state["zone"] = list(pipeline.zone)

    # The HTTP handler needs the pipeline to change the alarm zone at run time.
    Server.pipeline = pipeline

    threading.Thread(
        target=detection_loop, args=(args.source, config, pipeline),
        daemon=True,
    ).start()

    Server.poll_ms = args.poll_ms
    server = Server(("0.0.0.0", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        server.server_close()
        print("stopped")


if __name__ == "__main__":
    main()
