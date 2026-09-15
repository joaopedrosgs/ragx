import unittest

from ragx.formats import str as strfmt


class StrRotationTests(unittest.TestCase):
    # A tall quad whose top edge is 100 canvas px above the layer position
    # (x0..x3, y0..y3 as TL, TR, BR, BL; the canvas is y-down).
    XY = (-10.0, 10.0, 10.0, -10.0, -100.0, -100.0, 0.0, 0.0)

    def _top(self, angle):
        tl, tr, _, _ = strfmt._corners(self.XY, angle, strfmt.EFFECT_ORIGIN)
        return ((tl[0] + tr[0]) / 2.0, (tl[1] + tr[1]) / 2.0)

    def test_zero_angle_keeps_the_layer_upright(self):
        top = self._top(0.0)
        self.assertAlmostEqual(top[0], 0.0, places=4)
        self.assertAlmostEqual(top[1], -100.0, places=4)

    def test_positive_angle_turns_clockwise_on_screen(self):
        top = self._top(90.0)
        self.assertAlmostEqual(top[0], 100.0, places=4)
        self.assertAlmostEqual(top[1], 0.0, places=4)

    def test_meteor_tail_trails_a_fall_to_the_lower_left(self):
        # meteor1.str layer 2: authored at 30 degrees, moving by (-204, +322).
        self.assertGreater(self._top(30.0)[0], 0.0)


if __name__ == "__main__":
    unittest.main()
