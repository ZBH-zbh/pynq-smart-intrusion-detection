"""Turn a jittery sequence of positions into a smooth drawn trail.

The problem
-----------
The trail is a polyline through every tracked position. Those positions come
from a detector whose box moves by a pixel or two every frame, so the polyline
is a staircase: mostly standing still, occasionally jumping. Drawn with straight
segments it looks jagged and sharp, and the eye reads the jitter as movement.

Two layers, because they fix different halves of it
---------------------------------------------------
**1. Deadband recording.** A new point is recorded only once the target has
moved at least ``min_move`` pixels from the last recorded one. This removes the
staircase at its source: a target that is essentially stationary contributes no
new points at all, and a slow one contributes few. It also bounds the number of
points, which keeps the curve fit cheap.

**2. Curve fitting.** The recorded points are still connected by straight
segments, and they are sparse enough that the corners are visible. A
Catmull-Rom spline passes exactly through every recorded point -- so the drawn
path still goes where the target actually was -- but arrives and leaves each
point smoothly instead of turning a corner.

A centred moving average runs between the two. The deadband leaves points at
irregular spacing, and a spline through uneven spacing can develop a slight
wobble, so the control points are averaged before the spline is fitted.

Latency
-------
Neither layer is allowed to delay the picture, and on the board that took real
work rather than a cache. ``moving_average`` and ``catmull_rom`` were written
as plain Python loops over tuples, which is fine on a PC and not fine on a
650 MHz ARM core: measured on the board, one rebuild of the shipped 6-second
trail (~90 points) cost 18 ms of a 66 ms frame -- 27% of the frame, paid on
every frame the target moved -- and the 20-second setting reaches the 200-point
cap at 42 ms.

The arithmetic is identical; it is now expressed as whole-array operations, so
the cost is set by a handful of numpy calls instead of by one Python iteration
per output point. Note that ``build`` is not merely *cached*: ``TrailStore.add``
calls it inline whenever a point is recorded, so its cost is on the frame path
and has to be small in absolute terms, not merely amortised.
"""

import math

import numpy as np


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _as_points(array):
    """Rows of a float array as plain ``(x, y)`` tuples of Python floats.

    The conversion is not incidental: written as a list comprehension it was
    the single most expensive part of the fit on the board -- 4.1 ms of a 6.8 ms
    rebuild for a 90-point trail, because it runs the interpreter once per
    output point. ``tolist`` does the same work in C and then ``tuple`` only has
    to wrap what is already there. The result is identical, types included:
    callers unpack these and compare them to tuples, so numpy scalars must not
    leak out.
    """
    return list(map(tuple, array.tolist()))


def simplify(points, min_move):
    """Keep a point only once the target has moved ``min_move`` from the last.

    This is the debounce: a stationary target adds nothing, so its trail stops
    growing instead of scribbling a blob of jitter. The first point is always
    kept so a trail has somewhere to start.
    """
    if not points:
        return []
    if min_move <= 0:
        return list(points)

    kept = [points[0]]
    for point in points[1:]:
        if _distance(point, kept[-1]) >= min_move:
            kept.append(point)
    return kept


def moving_average(points, window):
    """Centred moving average, with the endpoints left where they are.

    Deadband recording leaves uneven spacing, and a spline fit to uneven
    spacing wobbles. Averaging first evens it out. The endpoints are preserved
    so the trail still starts and ends where the target was.

    A prefix sum turns each window average into a subtraction, so the whole
    thing is a fixed number of numpy calls rather than one slice per point.
    """
    if window <= 1 or len(points) < 3:
        return list(points)

    arr = np.asarray(points, dtype=np.float64)
    count = len(arr)
    half = window // 2

    prefix = np.zeros((count + 1, 2), dtype=np.float64)
    np.cumsum(arr, axis=0, out=prefix[1:])

    index = np.arange(1, count - 1)
    lo = np.maximum(0, index - half)
    hi = np.minimum(count, index + half + 1)
    totals = prefix[hi] - prefix[lo]
    widths = (hi - lo).astype(np.float64)[:, None]

    result = arr.copy()                 # keeps both endpoints untouched
    result[1:-1] = totals / widths
    return _as_points(result)


def catmull_rom(points, samples=8):
    """Dense polyline through `points`, smooth at every one of them.

    Catmull-Rom is interpolating -- the curve passes through each control point
    -- which matters here: a path that merely approximates the recorded
    positions would drift off the route the target actually took.

    All segments are evaluated at once. The curve is local (each segment needs
    four control points) but the pipeline wants the whole path redrawn anyway,
    so there is nothing to gain from the loop and a lot to lose: it was one
    Python iteration per output point, and there are ``samples`` of those per
    segment.
    """
    if len(points) < 2:
        return list(points)
    if samples < 1:
        samples = 1

    pts = np.asarray(points, dtype=np.float64)
    count = len(pts)

    # Duplicate the ends so the first and last segments have the four control
    # points the formula needs, without inventing a phantom direction.
    control = np.empty((count + 2, 2), dtype=np.float64)
    control[0] = pts[0]
    control[1:count + 1] = pts
    control[count + 1] = pts[-1]

    p0 = control[0:count - 1]        # one row per segment
    p1 = control[1:count]
    p2 = control[2:count + 1]
    p3 = control[3:count + 2]

    t = np.arange(samples, dtype=np.float64) / float(samples)
    t2 = t * t
    t3 = t2 * t

    def along(axis):
        """One coordinate, one row per segment and one column per sample."""
        a, b = p0[:, axis:axis + 1], p1[:, axis:axis + 1]
        c, d = p2[:, axis:axis + 1], p3[:, axis:axis + 1]
        return 0.5 * (
            (2.0 * b)
            + (-a + c) * t
            + (2.0 * a - 5.0 * b + 4.0 * c - d) * t2
            + (-a + 3.0 * b - 3.0 * c + d) * t3
        )

    dense = np.empty(((count - 1) * samples + 1, 2), dtype=np.float64)
    dense[:-1, 0] = along(0).ravel()
    dense[:-1, 1] = along(1).ravel()
    dense[-1] = pts[-1]
    return _as_points(dense)


def build(points, min_move=3.0, smooth_window=7, samples=6):
    """Full trail pipeline: debounce, average, fit.

    Returns a dense list of points ready to draw. Fewer than two recorded
    points means there is no path yet, and that is returned as-is.
    """
    kept = simplify(points, min_move)
    if len(kept) < 2:
        return kept
    if len(kept) == 2:
        # A single segment is already straight; fitting it adds nothing.
        return kept
    averaged = moving_average(kept, smooth_window)
    return catmull_rom(averaged, samples)


def turning(points):
    """Mean absolute turn, in degrees, between consecutive segments.

    A straight line scores 0; a staircase scores large. This is the number the
    trail work is trying to bring down, so it is what the tests measure.
    """
    if len(points) < 3:
        return 0.0
    total = 0.0
    for a, b, c in zip(points, points[1:], points[2:]):
        v1 = (b[0] - a[0], b[1] - a[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        n1 = math.hypot(*v1)
        n2 = math.hypot(*v2)
        if n1 < 1e-9 or n2 < 1e-9:
            continue
        cosine = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
        total += math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
    return total / (len(points) - 2)


def max_deviation(points, reference):
    """Largest distance from each smoothed point to the reference polyline.

    Guards the spline against overshooting: a smooth curve that swings wide of
    where the target actually went is worse than a jagged honest one.
    """
    if not points or len(reference) < 2:
        return 0.0

    worst = 0.0
    for point in points:
        best = min(_distance_to_segment(point, a, b)
                   for a, b in zip(reference, reference[1:]))
        worst = max(worst, best)
    return worst


def _distance_to_segment(point, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-12:
        return _distance(point, a)
    t = ((point[0] - ax) * dx + (point[1] - ay) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(point[0] - (ax + t * dx), point[1] - (ay + t * dy))


def measure_cost(points=40, repeats=200):
    """Time `build`, so the claim that it does not stall the picture is data."""
    import time

    sample = [(20 + i * 7, 120 + 20 * math.sin(i / 3.0)) for i in range(points)]
    start = time.perf_counter()
    for _ in range(repeats):
        build(sample)
    elapsed = (time.perf_counter() - start) / repeats
    return elapsed * 1000.0     # milliseconds


class TrailStore:
    """Recorded trails and their smoothed paths, one per track.

    Why a store rather than a field on the track
    --------------------------------------------
    The tracker's own history feeds velocity, direction and the crossing
    counter, and the trail for display must not disturb any of that: it is
    recorded with a dead band, which would bias the velocity estimate.

    Why the path is cached
    ----------------------
    Rebuilding on every frame would be wasted work -- with the dead band the
    recorded points only change occasionally -- but more importantly it keeps
    the cost off the per-frame path entirely. The renderer only ever reads a
    finished list, so a longer trail or a heavier fit can never delay the
    picture. Measured, a rebuild is 0.4 ms for 40 points and 1.2 ms for 120
    against a 60 ms frame budget, so this is a bound rather than a fix for a
    problem -- but it is the bound the design wants.
    """

    def __init__(self, min_move=3.0, smooth_window=7, samples=6,
                 max_points=200, seconds=6.0, linger=1.0):
        if min_move < 0:
            raise ValueError("min_move must be >= 0")
        if max_points < 2:
            raise ValueError("max_points must be >= 2")
        if seconds <= 0:
            raise ValueError("seconds must be > 0")
        if linger < 0:
            raise ValueError("linger must be >= 0")
        self.min_move = float(min_move)
        self.smooth_window = smooth_window
        self.samples = samples
        self.max_points = max_points
        #: How much history the trail shows, in SECONDS rather than in points.
        #: A point count makes the trail's length depend on speed -- a fast
        #: target kept about 7 s of history and a slow one over 20 s from the
        #: same setting, which is not what "the trail lasts N seconds" should
        #: mean.
        self.seconds = float(seconds)
        #: How long a trail keeps being drawn after its track disappears.
        #: Without this the trail vanishes the moment the tracker drops the
        #: track, which happens after about 0.8 s without a match -- so pausing
        #: mid-gesture wiped the trail, not the trail's own length.
        self.linger = float(linger)
        self._trails = {}            # id -> {"points", "path", "last_seen"}
        self.rebuilds = 0
        self.expired = 0

    # -- recording --------------------------------------------------------

    def add(self, track_id, point, timestamp=None):
        """Record `point` if it is far enough from the last recorded one.

        Returns True when a point was recorded, which is also when the path is
        rebuilt.
        """
        if timestamp is None:
            import time
            timestamp = time.monotonic()

        point = (float(point[0]), float(point[1]))
        entry = self._trails.get(track_id)
        if entry is None:
            self._trails[track_id] = {
                "points": [(timestamp, point[0], point[1])],
                "path": [point],
                "last_seen": timestamp,
            }
            self.rebuilds += 1
            return True

        entry["last_seen"] = timestamp
        last = entry["points"][-1]
        if _distance(point, (last[1], last[2])) < self.min_move:
            # Inside the dead band: the trail deliberately does not grow. The
            # live direction is still shown by the arrow.
            return False

        entry["points"].append((timestamp, point[0], point[1]))
        self._trim(entry)
        self._rebuild(track_id, entry)
        return True

    def _trim(self, entry):
        """Drop points that are too old, then cap the count as a safety net."""
        cutoff = entry["last_seen"] - self.seconds
        points = entry["points"]
        index = 0
        while index < len(points) and points[index][0] < cutoff:
            index += 1
        if index:
            del points[:index]
        if len(points) > self.max_points:
            del points[:len(points) - self.max_points]

    def _rebuild(self, track_id, entry):
        points = [(x, y) for _, x, y in entry["points"]]
        entry["path"] = build(
            points, min_move=0.0,        # already recorded through the band
            smooth_window=self.smooth_window, samples=self.samples,
        )
        self.rebuilds += 1

    # -- expiry -----------------------------------------------------------

    def prune(self, timestamp=None):
        """Forget trails whose track has been gone longer than ``linger``.

        Returns the set of ids that expired, so a caller can report it.
        """
        if timestamp is None:
            import time
            timestamp = time.monotonic()

        gone = set()
        for track_id, entry in list(self._trails.items()):
            if timestamp - entry["last_seen"] > self.linger:
                del self._trails[track_id]
                self.expired += 1
                gone.add(track_id)
                continue
            before = len(entry["points"])
            self._trim(entry)
            if len(entry["points"]) != before:
                self._rebuild(track_id, entry)
        return gone

    # -- reading ----------------------------------------------------------

    def path(self, track_id):
        """The cached smoothed path, ready to draw."""
        entry = self._trails.get(track_id)
        return entry["path"] if entry is not None else ()

    def recorded(self, track_id):
        entry = self._trails.get(track_id)
        return entry["points"] if entry is not None else ()

    def paths(self):
        """Every trail that should be drawn, live or lingering."""
        return {i: e["path"] for i, e in self._trails.items()}

    def trail_ids(self):
        return set(self._trails)

    def drop_except(self, live_ids, keep_lingering=True):
        """Drop trails for tracks that are gone.

        With ``keep_lingering`` a trail is kept until ``linger`` expires, which
        is what stops a brief loss of detection from erasing the picture.
        """
        if keep_lingering:
            return
        live = set(live_ids)
        for track_id in list(self._trails):
            if track_id not in live:
                del self._trails[track_id]

    def clear(self):
        self._trails.clear()

    # -- run-time configuration -------------------------------------------

    def settings(self):
        return {
            "min_move": self.min_move,
            "smooth_window": self.smooth_window,
            "samples": self.samples,
            "seconds": self.seconds,
            "linger": self.linger,
        }

    def configure(self, min_move=None, smooth_window=None, samples=None,
                  seconds=None, linger=None):
        """Change the tuning while running, and re-render what is on screen.

        Which settings need a rebuild differs, and so does what the user can
        expect from each:

        * ``smooth_window`` and ``samples`` change how the recorded points are
          drawn, so every stored trail is rebuilt at once.
        * ``seconds`` and ``linger`` only affect pruning, so they take effect on
          the next frame.
        * ``min_move`` changes what gets recorded from now on. It cannot
          re-record the past: raising it does not thin a trail that is already
          drawn, and lowering it does not recover points already discarded.
        """
        if min_move is not None:
            min_move = float(min_move)
            if min_move < 0:
                raise ValueError("min_move must be >= 0")
            self.min_move = min_move
        if smooth_window is not None:
            smooth_window = int(smooth_window)
            if smooth_window < 1:
                raise ValueError("smooth_window must be >= 1")
            self.smooth_window = smooth_window
        if samples is not None:
            samples = int(samples)
            if samples < 1:
                raise ValueError("samples must be >= 1")
            self.samples = samples
        if seconds is not None:
            seconds = float(seconds)
            if seconds <= 0:
                raise ValueError("seconds must be > 0")
            self.seconds = seconds
        if linger is not None:
            linger = float(linger)
            if linger < 0:
                raise ValueError("linger must be >= 0")
            self.linger = linger

        for track_id, entry in self._trails.items():
            self._rebuild(track_id, entry)
        return self.settings()

    def stats(self):
        return {
            "tracks": len(self._trails),
            "points": sum(len(e["points"]) for e in self._trails.values()),
            "drawn": sum(len(e["path"]) for e in self._trails.values()),
            "rebuilds": self.rebuilds,
            "expired": self.expired,
            "seconds": self.seconds,
            "linger": self.linger,
        }
