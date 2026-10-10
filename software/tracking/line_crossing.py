"""Count targets crossing a virtual tripwire.

Why a separate module
---------------------
Counting is easy to get almost right and hard to get right. The failure modes
are all about the target sitting on the line:

* A target that stops on the line jitters across it and counts repeatedly.
* A target that crosses on the infinite extension of the line -- outside the
  drawn tripwire -- counts even though it never touched it.
* A track that is briefly lost and re-acquired restarts with no side, and the
  re-acquisition can look like a crossing.

So the module keeps a committed side per track, refuses to change it while the
target is inside a dead band around the line, only counts when the path segment
actually intersects the drawn extent, and derives the side from the tracked
localisation point (the region centroid) rather than the bounding box centre.

It is deliberately independent of the tracker: it takes plain
``(track_id, (x, y))`` pairs, so it can be tested without running a detector.
"""

import math


class Crossing:
    """One recorded crossing."""

    __slots__ = ("track_id", "direction", "point", "timestamp")

    def __init__(self, track_id, direction, point, timestamp):
        self.track_id = track_id
        self.direction = direction      # 0 = negative->positive, 1 = the reverse
        self.point = point
        self.timestamp = timestamp

    def __repr__(self):
        return (f"Crossing(id={self.track_id}, dir={self.direction}, "
                f"point=({self.point[0]:.0f},{self.point[1]:.0f}))")


class LineCrossingCounter:
    """Tripwire counter.

    Parameters
    ----------
    line : (x1, y1, x2, y2)
        The drawn segment. Crossings on its infinite extension are ignored.
    deadband : float
        Half-width, in pixels, of the band around the line inside which the
        committed side is left alone. This is what stops a target resting on
        the line from counting over and over.
    segment_margin : float
        How far past each end of the segment a crossing still counts, as a
        fraction of the segment length. Small, so a crossing just at the tip
        is not lost to rounding.
    dedup_seconds, dedup_distance : float
        Window used to recognise that two crossings are really one object.

        The shipped detection chain reports a moving object as two separate
        targets -- its leading and its trailing edge -- because it differences
        consecutive frames. The tracker gives them different ids and both cross
        the line, so a naive count is exactly double. Two DIFFERENT tracks
        crossing in the same direction within this time and this distance are
        therefore counted once.

        The same track crossing twice is never suppressed: that is a real
        there-and-back movement. Set ``dedup_distance`` to 0 to switch the
        behaviour off.
    """

    def __init__(self, line, deadband=4.0, segment_margin=0.01,
                 dedup_seconds=0.8, dedup_distance=45.0,
                 confirm_distance=12.0, confirm_seconds=1.0, grouping=None):
        if deadband < 0:
            raise ValueError("deadband must be >= 0")
        if dedup_seconds < 0:
            raise ValueError("dedup_seconds must be >= 0")
        if dedup_distance < 0:
            raise ValueError("dedup_distance must be >= 0")
        if confirm_distance < 0:
            raise ValueError("confirm_distance must be >= 0")
        if confirm_seconds is not None and confirm_seconds < 0:
            raise ValueError("confirm_seconds must be >= 0 or None")
        self.deadband = float(deadband)
        self.segment_margin = float(segment_margin)
        self.dedup_seconds = float(dedup_seconds)
        self.dedup_distance = float(dedup_distance)
        #: How far past the line the target must actually travel before the
        #: crossing counts. See ``_advance_pending``.
        self.confirm_distance = float(confirm_distance)
        #: How long a crossing may wait to be confirmed, or None to wait as
        #: long as the track survives.
        self.confirm_seconds = (None if confirm_seconds is None
                                else float(confirm_seconds))
        #: Optional object that knows which tracks are the same physical
        #: object. Duck-typed rather than imported, so this module stays
        #: independent of the tracker: it needs only ``same_object(a, b)`` and
        #: ``definitely_distinct(a, b)``.
        self.grouping = grouping
        self.line = None
        self.set_line(line)

    # -- configuration ----------------------------------------------------

    def set_line(self, line):
        """Move the tripwire. Counts reset, because the sides no longer mean
        the same thing and keeping them would attribute old crossings to the
        new line."""
        if line is None or len(line) != 4:
            raise ValueError("line must be (x1, y1, x2, y2)")
        x1, y1, x2, y2 = (float(v) for v in line)
        if math.hypot(x2 - x1, y2 - y1) < 1e-6:
            raise ValueError("line must have non-zero length")
        self.line = (x1, y1, x2, y2)
        self.negative_to_positive = 0
        self.positive_to_negative = 0
        self.crossings = []
        #: Every potential crossing and what was decided about it, newest last.
        #: The count on its own cannot be checked against reality -- it says
        #: nothing about whether a real crossing was missed, or one crossing
        #: was counted twice. Keeping the decisions, including the ones that
        #: were thrown away, is what makes the count auditable.
        self.events = []
        self._sides = {}
        self._points = {}
        #: Crossings that have happened but are not yet confirmed, by track id.
        self._pending = {}

    def reset_counts(self):
        self.negative_to_positive = 0
        self.positive_to_negative = 0
        self.crossings = []
        self.events = []
        self._pending = {}

    #: How many decisions to keep for inspection.
    EVENT_HISTORY = 60

    def _log(self, track_id, direction, point, timestamp, outcome, detail):
        """Record one decision about one potential crossing."""
        self.events.append({
            "track_id": track_id,
            "direction": direction,
            "point": (None if point is None
                      else (round(point[0], 1), round(point[1], 1))),
            "timestamp": timestamp,
            "outcome": outcome,
            "detail": detail,
        })
        if len(self.events) > self.EVENT_HISTORY:
            del self.events[:len(self.events) - self.EVENT_HISTORY]

    # -- geometry ---------------------------------------------------------

    def signed_distance(self, point):
        """Signed distance from the line, positive on one side.

        For a mostly vertical line positive is to the right; for a mostly
        horizontal line positive is upwards, because image y grows downward.
        """
        x1, y1, x2, y2 = self.line
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        px, py = point
        # Cross product of (p - p1) with the direction, normalised.
        return ((px - x1) * dy - (py - y1) * dx) / length

    def labels(self):
        """(name for negative->positive, name for positive->negative)."""
        x1, y1, x2, y2 = self.line
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if dy >= dx:
            # Mostly vertical: the two sides are left and right.
            # Positive is to the right, so negative->positive goes rightwards.
            return ("L>R", "R>L")
        # Mostly horizontal: positive is upwards.
        return ("D>U", "U>D")

    def _within_segment(self, point):
        """Is `point` on the drawn extent, rather than its extension?"""
        x1, y1, x2, y2 = self.line
        dx, dy = x2 - x1, y2 - y1
        length_sq = dx * dx + dy * dy
        u = ((point[0] - x1) * dx + (point[1] - y1) * dy) / length_sq
        return -self.segment_margin <= u <= 1.0 + self.segment_margin

    @staticmethod
    def _intersection(p0, p1, s0, s1):
        """Where the segment p0->p1 meets the zero level of the signed field."""
        span = s0 - s1
        if abs(span) < 1e-12:
            return None
        t = s0 / span
        return (p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1]))

    def _already_counted(self, crossing):
        """The earlier crossing this one duplicates, or None.

        Only crossings by DIFFERENT tracks are candidates: one track crossing
        the line twice is a genuine there-and-back movement and must count
        both times.

        Two rules, and they answer different questions:

        * A **proven** pair from the grouper -- two edges of one rigid object --
          is a duplicate whenever it crossed, however long ago. That is what
          covers the measured 1.47 s and 3.4 s gaps, which the time window is
          too short to reach.
        * Otherwise the time-and-distance window catches the common case, but
          only for pairs the grouper has **not** shown to be separate objects,
          so two different people crossing together are not merged.
        """
        candidates = list(reversed(self.crossings[-24:]))
        for other in candidates:
            if other.direction != crossing.direction:
                continue
            if other.track_id == crossing.track_id:
                continue

            proven_same = False
            if self.grouping is not None:
                proven_same = self.grouping.same_object(
                    crossing.track_id, other.track_id)
                if proven_same:
                    return other
                if self.grouping.definitely_distinct(
                        crossing.track_id, other.track_id):
                    # Positive evidence they are two objects, so the window
                    # must not merge them.
                    continue

            if self.dedup_distance <= 0:
                continue
            age = crossing.timestamp - other.timestamp
            if age is None or age > self.dedup_seconds:
                # Crossings are appended in time order, so once one is too old
                # the rest are older still -- but only for this window rule.
                # A proven pair is handled above and never reaches here.
                break
            if age < 0:
                continue
            distance = math.hypot(crossing.point[0] - other.point[0],
                                  crossing.point[1] - other.point[1])
            if distance <= self.dedup_distance:
                return other
        return None

    # -- counting ---------------------------------------------------------

    def update(self, points, timestamp=None):
        """Feed one frame of tracked points.

        Parameters
        ----------
        points : iterable of (track_id, (x, y))
            Only confirmed, moving tracks should be passed: a target that is
            not actually moving is not crossing anything.

        Returns
        -------
        list[Crossing]
            The crossings recorded on this frame.
        """
        seen = set()
        recorded = []

        for track_id, point in points:
            seen.add(track_id)
            distance = self.signed_distance(point)
            previous = self._sides.get(track_id)
            previous_point = self._points.get(track_id, point)
            pending = self._pending.get(track_id)

            if previous is None:
                # First sighting: adopt a side, never count. The target may
                # well have appeared on either side of the line.
                self._sides[track_id] = distance
                self._points[track_id] = point
                continue

            if abs(distance) < self.deadband:
                # Inside the band: hold the committed side, so resting on the
                # line does not accumulate counts from jitter.
                continue

            if previous * distance > 0:
                # Still clearly on the same side; keep the fresher value so a
                # slow approach is measured from the last real position rather
                # than from a stale one.
                self._sides[track_id] = distance
                self._points[track_id] = point
                # A crossing waiting to be confirmed is advanced by how far the
                # target has now travelled past the line, which is the only
                # evidence that separates a crossing from a wobble.
                self._advance_pending(track_id, distance, timestamp, recorded)
                continue

            if abs(previous) < self.deadband:
                # The committed side was itself inside the band, so it is not
                # a trustworthy starting point for a crossing.
                self._sides[track_id] = distance
                self._points[track_id] = point
                continue

            # Interpolate between the last position and this one, so the
            # reported crossing point is where the target actually met the
            # line rather than wherever it happened to be sampled.
            point_of_crossing = self._intersection(
                previous_point, point, previous, distance
            )
            self._sides[track_id] = distance
            self._points[track_id] = point
            direction = 0 if previous < 0 < distance else 1
            first, second = self.labels()

            if point_of_crossing is None:
                # The two signed distances agree in sign yet the target is on
                # the far side; only reachable through a degenerate geometry.
                self._log(track_id, direction, point, timestamp, "degenerate",
                          "could not interpolate the meeting point")
                continue

            # Coming back over the line means any crossing still waiting to be
            # confirmed did not stick. This has to be resolved before the new
            # one is recorded, or the two would cancel in the wrong order.
            if pending is not None:
                del self._pending[track_id]
                # The crossing point shown for the cancelled one is the one it
                # reached when it turned round, not where it originally met.
                self._log(track_id, pending["direction"], point_of_crossing,
                          timestamp, "reverted",
                          f"came back over the line after only "
                          f"{pending['reach']:.0f} px "
                          f"(needs {self.confirm_distance:.0f} px to count)")
                pending = None

            if not self._within_segment(point_of_crossing):
                # It crossed the line, but beyond the drawn end of it, so it
                # never touched the tripwire the operator can see.
                self._log(track_id, direction, point_of_crossing, timestamp,
                          "outside",
                          "crossed past the end of the drawn segment")
                continue

            # Not counted yet. A detection burst that flips sides and comes
            # straight back is not an object crossing the line, and on a
            # recorded session of an empty room it is exactly what produced
            # three phantom crossings. Counting waits until the target has
            # actually committed to the far side.
            self._pending[track_id] = {
                "direction": direction,
                "point": point_of_crossing,
                "timestamp": timestamp,
                "reach": abs(distance),
            }
            self._log(track_id, direction, point_of_crossing, timestamp,
                      "pending",
                      f"waiting for {self.confirm_distance:.0f} px on the far "
                      f"side (at {abs(distance):.0f} px)")
            # A fast target can already be past the threshold on the very frame
            # it crosses, and never gets a later frame on the same side if it
            # turns round immediately. Advancing here counts that case and
            # leaves a slow one waiting.
            self._advance_pending(track_id, distance, timestamp, recorded)
            continue

        # Forget tracks that are gone, so a re-used id cannot inherit a side
        # and a re-acquisition is treated as a fresh sighting.
        for track_id in list(self._sides):
            if track_id not in seen:
                del self._sides[track_id]
                self._points.pop(track_id, None)
                pending = self._pending.pop(track_id, None)
                if pending is not None:
                    # The track vanished before it committed, so the crossing
                    # never completed. Reported rather than silently dropped,
                    # because "the target was lost right at the line" is
                    # otherwise invisible.
                    self._log(track_id, pending["direction"],
                              pending["point"],
                              pending["timestamp"], "abandoned",
                              f"track lost after only {pending['reach']:.0f} px "
                              f"on the far side")

        return recorded

    def _advance_pending(self, track_id, distance, timestamp, recorded):
        """Move a waiting crossing towards being counted, or drop it.

        Returns True when the caller should stop processing this track.
        """
        pending = self._pending.get(track_id)
        if pending is None:
            return False

        on_far_side = ((distance > 0) if pending["direction"] == 0
                       else (distance < 0))
        if not on_far_side:
            return False

        reach = abs(distance)
        pending["reach"] = max(pending["reach"], reach)
        if pending["reach"] < self.confirm_distance:
            if (self.confirm_seconds is not None
                    and timestamp is not None
                    and pending["timestamp"] is not None
                    and timestamp - pending["timestamp"] > self.confirm_seconds
                    and pending["reach"] < self.confirm_distance):
                del self._pending[track_id]
                self._log(track_id, pending["direction"], pending["point"],
                          timestamp, "expired",
                          f"only reached {pending['reach']:.0f} px of the "
                          f"{self.confirm_distance:.0f} px needed within "
                          f"{self.confirm_seconds:.1f} s")
            return True

        # Committed: the crossing is real.
        del self._pending[track_id]
        first, second = self.labels()
        crossing = Crossing(track_id, pending["direction"], pending["point"],
                            pending["timestamp"])
        matched = self._already_counted(crossing)
        if matched is not None:
            self._log(track_id, pending["direction"], pending["point"],
                      pending["timestamp"], "duplicate",
                      f"same object as track {matched.track_id}, "
                      f"{abs((crossing.timestamp or 0) - (matched.timestamp or 0)):.2f} s "
                      f"and "
                      f"{math.hypot(crossing.point[0] - matched.point[0], crossing.point[1] - matched.point[1]):.0f} px apart")
            return True

        if pending["direction"] == 0:
            self.negative_to_positive += 1
        else:
            self.positive_to_negative += 1
        self.crossings.append(crossing)
        recorded.append(crossing)
        self._log(track_id, pending["direction"], pending["point"],
                  timestamp, "counted",
                  f"counted as "
                  f"{first if pending['direction'] == 0 else second} "
                  f"after reaching {pending['reach']:.0f} px on the far side")
        return True

    # -- reporting --------------------------------------------------------

    def counts(self):
        first, second = self.labels()
        return {first: self.negative_to_positive,
                second: self.positive_to_negative}

    def total(self):
        return self.negative_to_positive + self.positive_to_negative

    def info(self, recent=10):
        first, second = self.labels()
        return {
            "line": list(self.line),
            "labels": [first, second],
            "counts": [self.negative_to_positive, self.positive_to_negative],
            "total": self.total(),
            # How far past the line a target must travel before it counts, so
            # the page can explain a crossing that was refused.
            "confirm_distance": self.confirm_distance,
            "confirm_seconds": self.confirm_seconds,
            "pending": len(self._pending),
            # Enough of a trail to see WHY a count happened, which is the
            # difference between debugging this and guessing at it.
            "recent": [
                {
                    "id": c.track_id,
                    "direction": c.direction,
                    "label": (first if c.direction == 0 else second),
                    "point": [round(c.point[0], 1), round(c.point[1], 1)],
                    "t": None if c.timestamp is None else round(c.timestamp, 2),
                }
                for c in self.crossings[-recent:]
            ],
            # Everything the counter decided, counted or not, so the numbers
            # can be checked against what actually happened in front of the
            # camera instead of being taken on trust.
            "events": [
                {
                    "id": event["track_id"],
                    "direction": event["direction"],
                    "label": (first if event["direction"] == 0 else second),
                    "point": (None if event["point"] is None
                              else list(event["point"])),
                    "t": (None if event["timestamp"] is None
                          else round(event["timestamp"], 2)),
                    "outcome": event["outcome"],
                    "detail": event["detail"],
                }
                for event in self.events[-recent:]
            ],
        }
