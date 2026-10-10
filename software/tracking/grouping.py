"""Decide which tracks are the two edges of one object.

The problem
-----------
The shipped detection chain differences consecutive frames, so one moving
object comes back as TWO detections: the strip of pixels it is arriving into and
the strip it is leaving. The tracker sees them as two tracks with different ids,
and both cross a tripwire, so a naive count is exactly double.

The existing fix, and where it runs out
---------------------------------------
:mod:`line_crossing` already merges two crossings that happen close together in
time and place. Measured on real footage the two edges usually cross 0.4-0.47 s
apart, which that window catches. But gaps of 1.47 s and 3.4 s were also
observed -- when one edge's track was briefly lost -- and those are counted
twice. Widening the window enough to catch them would also merge TWO DIFFERENT
people passing within a second of each other, which under-counts. Both failure
modes were measured, not guessed.

What actually identifies a pair
-------------------------------
Two edges of one object move together. The offset between them stays nearly
constant while both travel at the same velocity, because they are the same rigid
thing seen twice. Two separate people have no reason to hold a fixed offset.

So a pair is accepted only on positive evidence: observed together for enough
frames, with a stable offset and matching motion. Absence of evidence is not
treated as evidence -- an unproven pair is left alone.

What this deliberately does NOT do
----------------------------------
It never merges boxes. The green boxes are the teammate's detection output and
are left exactly as they are; this only tells the crossing counter which tracks
are the same object. Filtering the boxes themselves would change what is drawn
on screen, which is a different decision and not this module's to make.
"""

import math


class StripGrouper:
    """Track the offset between tracks and report which pairs are one object.

    Parameters
    ----------
    min_samples : int
        How many frames two tracks must have been seen together before any
        judgement is made about them.
    min_offset, max_offset : float
        The plausible separation range, in pixels. The shipped chain's two edges
        were measured 56-61 px apart, so the window is set wide around that to
        allow for nearer and larger objects without reaching across the frame.
    max_offset_std : float
        How much the offset may wander, in pixels, and still count as rigid.
    max_speed_difference : float
        How far apart two speeds may be, in px/s, and still count as moving
        together.
    history : int
        How many recent positions to keep per track.
    min_offset_change : float
        The offset spread above which a pair is called *different* objects
        rather than merely unproven. Kept well above ``max_offset_std`` so the
        two judgements do not overlap and a pair is never both.
    pair_memory_seconds : float
        How long a verdict survives without being reconfirmed. Verdicts are
        refreshed on every frame both tracks are visible, so this only expires
        once they stop being seen together. It exists so a brief loss of one
        edge does not throw away good evidence, and so the tables cannot grow
        without bound over a long run.
    """

    def __init__(self, min_samples=4, min_offset=20.0, max_offset=110.0,
                 max_offset_std=6.0, max_speed_difference=40.0, history=16,
                 min_offset_change=18.0, pair_memory_seconds=3.0):
        if min_samples < 2:
            raise ValueError("min_samples must be >= 2")
        if max_offset < min_offset:
            raise ValueError("max_offset must be >= min_offset")
        if min_offset_change <= max_offset_std:
            raise ValueError("min_offset_change must exceed max_offset_std, "
                             "or a pair could be both proven and refuted")
        if pair_memory_seconds <= 0:
            raise ValueError("pair_memory_seconds must be > 0")
        self.min_samples = int(min_samples)
        self.min_offset = float(min_offset)
        self.max_offset = float(max_offset)
        self.max_offset_std = float(max_offset_std)
        self.max_speed_difference = float(max_speed_difference)
        self.history = int(history)
        self.min_offset_change = float(min_offset_change)
        self.pair_memory_seconds = float(pair_memory_seconds)

        self._samples = {}          # id -> [(t, x, y), ...]
        self._motion = {}           # id -> (vx, vy)
        self._same = {}             # frozenset({a, b}) -> last confirmed
        self._distinct = {}         # frozenset({a, b}) -> last confirmed

    # -- observation ------------------------------------------------------

    def observe(self, tracks, timestamp):
        """Feed the active tracks for this frame.

        Takes the tracker's own ``Track`` objects; only ``id``, ``center`` and
        ``motion()`` are used, so a test can pass anything with those.
        """
        timestamp = float(timestamp)
        seen = set()
        for track in tracks:
            track_id = track.id
            seen.add(track_id)
            center = track.center
            samples = self._samples.setdefault(track_id, [])
            samples.append((timestamp, float(center[0]), float(center[1])))
            del samples[:-self.history]
            try:
                vx, vy, _speed, _angle = track.motion()
                self._motion[track_id] = (float(vx), float(vy))
            except (AttributeError, TypeError, ValueError):
                pass

        # A track that is gone cannot be observed again, so its samples would
        # only let a re-used id inherit them.
        for track_id in list(self._samples):
            if track_id not in seen:
                del self._samples[track_id]
                self._motion.pop(track_id, None)

        self._classify(seen, timestamp)
        self._expire(timestamp)

    def _expire(self, timestamp):
        """Forget verdicts that have not been reconfirmed recently.

        Without this the tables grow for the life of the process, and a verdict
        from an object that left the scene a minute ago could still decide a
        crossing now.
        """
        for table in (self._same, self._distinct):
            for key, confirmed in list(table.items()):
                if timestamp - confirmed > self.pair_memory_seconds:
                    del table[key]

    def _classify(self, active, timestamp):
        """Judge every currently visible pair."""
        for first in active:
            for second in active:
                if second <= first:
                    continue
                key = frozenset((first, second))
                verdict = self._judge(first, second)
                if verdict == "same":
                    self._same[key] = timestamp
                    self._distinct.pop(key, None)
                elif verdict == "different":
                    self._distinct[key] = timestamp
                    self._same.pop(key, None)

    def _judge(self, first, second):
        """Return 'same', 'different', or None when there is not enough evidence."""
        a = self._samples.get(first)
        b = self._samples.get(second)
        if not a or not b:
            return None

        pairs = self._aligned(a, b)
        if not pairs:
            return None

        dx = [p[0] for p in pairs]
        dy = [p[1] for p in pairs]
        mean_dx = sum(dx) / len(dx)
        mean_dy = sum(dy) / len(dy)

        # A separation outside the plausible range is a hard geometric
        # constraint, not a statistic, so a single frame settles it. Requiring
        # several frames here would let a stale "same" verdict survive long
        # enough to suppress a real crossing.
        if not (self.min_offset <= math.hypot(mean_dx, mean_dy)
                <= self.max_offset):
            return "different"

        # Whether they move together IS a statistic, and needs a few frames.
        if len(pairs) < self.min_samples:
            return None

        spread = self._spread(dx, mean_dx) + self._spread(dy, mean_dy)
        va = self._motion.get(first)
        vb = self._motion.get(second)
        if va is None or vb is None:
            return None
        speed_diff = math.hypot(va[0] - vb[0], va[1] - vb[1])

        if spread <= self.max_offset_std \
                and speed_diff <= self.max_speed_difference:
            return "same"
        if spread >= self.min_offset_change:
            return "different"
        return None

    def _aligned(self, a, b):
        """Offsets between the two tracks over the frames both were seen.

        Matched from the newest backwards and only where the two samples are
        close in time, so a frame where one track was coasted does not produce a
        phantom offset.
        """
        out = []
        for (ta, xa, ya), (tb, xb, yb) in zip(reversed(a), reversed(b)):
            if abs(ta - tb) > 0.35:
                continue
            out.append((xa - xb, ya - yb))
            if len(out) >= self.min_samples * 3:
                break
        return out

    @staticmethod
    def _spread(values, mean):
        if len(values) < 2:
            return 0.0
        variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        return math.sqrt(variance)

    # -- answers ----------------------------------------------------------

    def same_object(self, first, second):
        """True only when a pair has been PROVEN to be one object.

        Unproven pairs return False and are left to whatever other rule the
        caller has; this module never guesses.
        """
        return frozenset((first, second)) in self._same

    def definitely_distinct(self, first, second):
        """True only when a pair has been shown to be two separate objects."""
        return frozenset((first, second)) in self._distinct

    def group_of(self, track_id):
        """A stable label for the object a track belongs to.

        The label is the smallest track id in the proven group, so two edges of
        one object report the same label and it does not change as tracks come
        and go.
        """
        label = track_id
        for pair in self._same:
            if track_id in pair:
                label = min(label, *pair)
        return label

    def groups(self):
        """Map every known member to its group label."""
        members = set()
        for pair in self._same:
            members |= pair
        return {track_id: self.group_of(track_id) for track_id in members}

    def info(self):
        return {
            "proven_pairs": len(self._same),
            "distinct_pairs": len(self._distinct),
            "tracked": len(self._samples),
            "pairs": [sorted(pair) for pair in sorted(self._same)],
        }
