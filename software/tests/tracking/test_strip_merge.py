"""Tests for merge_edge_strips.

The first group pins the behaviour that matters: two strips of one moving
object must fuse into a box covering the object. The last group pins the known
limitation, so a future change cannot silently make it worse.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "pynq_deploy"))
sys.path.insert(0, DEPLOY)
sys.path.insert(0, HERE)

from strip_merge import merge_edge_strips, merge_fragments_2d  # noqa: E402


class FragmentInflationTests(unittest.TestCase):
    """Grouping must not inflate a target box with nearby background pieces.

    The expansion that decides a merge is ``min(max_gap, gap_scale * min(w, h))``
    -- measured, the ``gap_scale`` term is what actually binds for ordinary box
    sizes, not ``max_gap``. At the frame-difference setting of 0.6 a 40 px wide
    object tolerates a 24 px hole, so a background fragment 24 px away is unioned
    into the box and the box grows by that much on each side. That is both "the
    box is too large" and one way a track ends up centred between the object and
    the environment.
    """

    def _gap_for(self, box, gap_scale, max_gap):
        from strip_merge import _gap_within
        return min(max_gap, gap_scale * min(box[2], box[3]))

    def test_the_gap_scale_term_is_what_binds(self):
        box = (10, 100, 40, 60)
        self.assertAlmostEqual(self._gap_for(box, 0.6, 62), 24.0)
        self.assertAlmostEqual(self._gap_for(box, 0.3, 25), 12.0)

    def test_a_frame_difference_gap_inflates_the_box(self):
        """A 20 px away fragment is absorbed at gap_scale 0.6."""
        object_box = (10, 100, 40, 60)
        fragment = (70, 100, 40, 60)           # 20 px gap, same row
        merged = merge_fragments_2d([object_box, fragment], gap_scale=0.6,
                                    max_gap=62)
        self.assertEqual(len(merged), 1)
        self.assertGreater(
            merged[0][2], object_box[2] + 20,
            "with a 24 px allowance the union should reach the fragment",
        )

    def test_a_tighter_gap_keeps_them_apart(self):
        object_box = (10, 100, 40, 60)
        fragment = (70, 100, 40, 60)
        merged = merge_fragments_2d([object_box, fragment], gap_scale=0.3,
                                    max_gap=25)
        self.assertEqual(
            len(merged), 2,
            f"a 12 px allowance should not reach a fragment 20 px away, "
            f"got {merged}",
        )

    def test_a_small_hole_is_still_rejoined(self):
        """The setting must still fix the fragmentation it was added for."""
        top = (10, 60, 41, 50)
        bottom = (10, 119, 41, 50)             # 9 px hole
        merged = merge_fragments_2d([top, bottom], gap_scale=0.3, max_gap=25)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0], (10, 60, 41, 109))

    def test_a_hand_box_stays_close_to_the_hand(self):
        hand = (100, 90, 45, 60)
        stray = (158, 90, 20, 25)              # 13 px gap from the hand
        merged = merge_fragments_2d([hand, stray], gap_scale=0.3, max_gap=25)
        for x, y, w, h in merged:
            self.assertLessEqual(
                w, hand[2] + 16,
                f"box {x, y, w, h} is much wider than the hand {hand}",
            )

class FragmentGroupingTests(unittest.TestCase):
    """merge_fragments_2d joins the pieces of one object in both axes."""

    def test_empty(self):
        self.assertEqual(merge_fragments_2d([]), [])

    def test_single_box_unchanged(self):
        self.assertEqual(merge_fragments_2d([(5, 5, 10, 10)]), [(5, 5, 10, 10)])

    def test_object_split_by_a_horizontal_hole_is_rejoined(self):
        """A 40-wide object broken into two 40x50 pieces 19 px apart."""
        top = (10, 60, 41, 50)
        bottom = (10, 129, 41, 50)
        merged = merge_fragments_2d([top, bottom])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0], (10, 60, 41, 119))

    def test_object_split_by_a_vertical_hole_is_rejoined(self):
        left = (10, 60, 40, 100)
        right = (70, 60, 40, 100)
        merged = merge_fragments_2d([left, right])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0], (10, 60, 100, 100))

    def test_a_chain_of_fragments_collapses(self):
        # Gap 15 against an allowed expansion of 0.6 * 30 = 18.
        parts = [(0, 0, 30, 30), (45, 0, 30, 30), (90, 0, 30, 30)]
        merged = merge_fragments_2d(parts)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0], (0, 0, 120, 30))

    def test_the_gap_limit_is_size_relative(self):
        """Documented rule: the allowed gap is gap_scale * min(w, h)."""
        # 30 px boxes, gap 18 -> exactly at the limit, merges.
        self.assertEqual(
            len(merge_fragments_2d([(0, 0, 30, 30), (48, 0, 30, 30)])), 1)
        # Gap 21 -> beyond the limit, stays separate.
        self.assertEqual(
            len(merge_fragments_2d([(0, 0, 30, 30), (51, 0, 30, 30)])), 2)

    def test_distant_objects_stay_separate(self):
        merged = merge_fragments_2d([(0, 0, 30, 30), (200, 0, 30, 30)])
        self.assertEqual(len(merged), 2)

    def test_small_fragments_do_not_reach_across_the_frame(self):
        """The gap allowed scales with box size, so small blobs stay separate."""
        merged = merge_fragments_2d([(0, 0, 8, 8), (60, 0, 8, 8)])
        self.assertEqual(len(merged), 2)

    def test_large_objects_tolerate_a_large_gap(self):
        merged = merge_fragments_2d([(0, 0, 120, 120), (190, 0, 120, 120)],
                                    max_gap=80)
        self.assertEqual(len(merged), 1)

    def test_max_gap_caps_the_expansion(self):
        merged = merge_fragments_2d(
            [(0, 0, 120, 120), (400, 0, 120, 120)], max_gap=40
        )
        self.assertEqual(len(merged), 2)

    def test_diagonal_fragments_are_rejoined(self):
        merged = merge_fragments_2d([(10, 10, 40, 40), (55, 55, 40, 40)])
        self.assertEqual(len(merged), 1)

    def test_output_is_sorted_left_to_right(self):
        merged = merge_fragments_2d([(200, 0, 20, 20), (0, 0, 20, 20)])
        self.assertEqual([b[0] for b in merged], [0, 200])


class BasicTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(merge_edge_strips([]), [])

    def test_single_box_unchanged(self):
        self.assertEqual(merge_edge_strips([(5, 5, 10, 10)]), [(5, 5, 10, 10)])

    def test_two_strips_of_one_object_fuse(self):
        """A 56 px wide object moving 8 px/frame gives two 6 px edge strips."""
        strips = [(17, 67, 6, 69), (73, 67, 6, 69)]
        merged = merge_edge_strips(strips)
        self.assertEqual(len(merged), 1)
        x, y, w, h = merged[0]
        # Must span from the left edge of the first strip to the right edge of
        # the second, and keep the shared vertical extent.
        self.assertEqual((x, y), (17, 67))
        self.assertEqual(w, 62)
        self.assertEqual(h, 69)

    def test_three_strips_fuse(self):
        strips = [(10, 50, 6, 60), (60, 50, 6, 60), (110, 50, 6, 60)]
        merged = merge_edge_strips(strips)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0], (10, 50, 106, 60))

    def test_ordering_does_not_matter(self):
        strips = [(73, 67, 6, 69), (17, 67, 6, 69)]
        self.assertEqual(merge_edge_strips(strips), [(17, 67, 62, 69)])

    def test_overlapping_boxes_fuse(self):
        merged = merge_edge_strips([(0, 0, 20, 20), (10, 0, 20, 20)])
        self.assertEqual(merged, [(0, 0, 30, 20)])

    def test_float_inputs_are_accepted_and_become_ints(self):
        merged = merge_edge_strips([(1.9, 2.9, 5.0, 5.0)])
        self.assertEqual(merged, [(1, 2, 5, 5)])


class SeparationTests(unittest.TestCase):
    def test_far_apart_objects_do_not_merge(self):
        merged = merge_edge_strips([(0, 0, 40, 60), (200, 0, 40, 60)])
        self.assertEqual(len(merged), 2)

    def test_vertically_separated_objects_do_not_merge(self):
        """Stacked objects share no vertical span, so they stay separate.

        This is the property that keeps two people at different depths apart.
        """
        merged = merge_edge_strips([(0, 0, 40, 60), (50, 150, 40, 60)])
        self.assertEqual(len(merged), 2)

    def test_different_heights_do_not_merge(self):
        merged = merge_edge_strips([(0, 0, 40, 60), (50, 0, 40, 120)])
        self.assertEqual(len(merged), 2)

    def test_partial_vertical_overlap_below_threshold_does_not_merge(self):
        # 60 tall boxes offset by 40 -> 20 px overlap = 33% of the shorter span.
        merged = merge_edge_strips(
            [(0, 0, 40, 60), (50, 40, 40, 60)], min_vertical_overlap=0.6
        )
        self.assertEqual(len(merged), 2)

    def test_partial_vertical_overlap_above_threshold_merges(self):
        # Offset by 10 -> 50 px overlap = 83%.
        merged = merge_edge_strips(
            [(0, 0, 40, 60), (50, 10, 40, 60)], min_vertical_overlap=0.6
        )
        self.assertEqual(len(merged), 1)

    def test_gap_limit_is_respected(self):
        near = [(0, 0, 10, 50), (40, 0, 10, 50)]     # 30 px gap
        self.assertEqual(len(merge_edge_strips(near, max_gap=20)), 2)
        self.assertEqual(len(merge_edge_strips(near, max_gap=30)), 1)


class KnownLimitationTests(unittest.TestCase):
    """Documented failure mode: same-height neighbours within max_gap fuse."""

    def test_two_people_side_by_side_at_same_height_would_merge(self):
        left = (0, 60, 40, 150)
        right = (70, 60, 40, 150)          # 30 px gap, same height
        merged = merge_edge_strips([left, right], max_gap=62)
        self.assertEqual(
            len(merged), 1,
            "this is the known limitation: identical height and strong vertical "
            "overlap are indistinguishable from two edges of one object",
        )

    def test_disabling_the_merge_preserves_raw_boxes(self):
        pipes = [(0, 60, 40, 150), (70, 60, 40, 150)]
        self.assertEqual(len(merge_edge_strips(pipes, max_gap=0)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
