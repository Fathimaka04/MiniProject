"""
Tests for phrase_board/confirm.py (arm with dwell, confirm with a long blink).

Run:
    python -m unittest test_selection_confirm -v
"""
import unittest

from blink.classifier import BlinkType
from phrase_board.confirm import SelectionConfirmer, SelectionState

# Home page grid (3 x 2):   0 FOOD   1 WATER  2 BATHROOM
#                           3 HELP   4 PAIN   5 MORE
TILES = {0: "food", 1: "water", 2: "bathroom", 3: "help", 4: "nav_pain", 5: "nav_more"}
FRAME = 1 / 30  # 30 fps


class ConfirmerTests(unittest.TestCase):
    def setUp(self):
        self.confirmed = []
        self.c = SelectionConfirmer(dwell_time_ms=1200, confirm_timeout_s=3.0,
                                    on_confirm=self.confirmed.append)
        self.t = 0.0

    def look(self, zone, seconds):
        """Feed gaze frames on one zone for `seconds`."""
        end = self.t + seconds
        while self.t < end:
            self.c.update_gaze(zone, TILES.get(zone), self.t)
            self.t += FRAME

    def eyes_closed(self, seconds):
        """No gaze frames while the eyes are closed (main.py skips them)."""
        self.t += seconds

    def long_blink_ends(self):
        self.c.on_blink(BlinkType.LONG_DELIBERATE, self.t)

    # ------------------------------------------------------------------

    def test_dwell_then_long_blink_confirms(self):
        self.look(1, 1.3)                      # WATER, top row
        self.assertEqual(self.c.get_state(), (SelectionState.ARMED, "water"))
        self.eyes_closed(0.8)
        self.long_blink_ends()
        self.assertEqual(self.confirmed, ["water"])

    def test_top_row_survives_gaze_drift_while_eyelids_close(self):
        """The reported bug: drift to the tile below during the blink cancelled the arm."""
        self.look(1, 1.3)                      # arm WATER
        self.look(4, 0.35)                     # half-closed lids: gaze 'drops' to PAIN below
        self.eyes_closed(0.8)                  # fully closed: no gaze frames
        self.long_blink_ends()
        self.assertEqual(self.confirmed, ["water"])

    def test_drift_and_return_keeps_arm(self):
        self.look(0, 1.3)                      # arm FOOD
        self.look(3, 0.3)                      # brief slide to HELP
        self.look(0, 0.3)                      # back to FOOD
        self.assertEqual(self.c.get_state(), (SelectionState.ARMED, "food"))

    def test_really_looking_away_still_cancels(self):
        self.look(0, 1.3)                      # arm FOOD
        self.look(2, 0.7)                      # clearly looking at BATHROOM
        self.assertEqual(self.c.get_state()[0], SelectionState.IDLE)
        self.long_blink_ends()
        self.assertEqual(self.confirmed, [])   # FOOD is NOT selected by mistake

    def test_moving_to_another_tile_arms_that_tile(self):
        self.look(0, 1.3)                      # arm FOOD
        self.look(2, 1.5)                      # dwell on BATHROOM
        self.assertEqual(self.c.get_state(), (SelectionState.ARMED, "bathroom"))
        self.eyes_closed(0.8)
        self.long_blink_ends()
        self.assertEqual(self.confirmed, ["bathroom"])

    def test_timeout_still_cancels(self):
        cancels = []
        self.c.set_on_cancel(lambda: cancels.append(self.t))
        self.look(1, 1.3)
        self.look(1, 3.1)                      # armed but no blink for > 3 s
        # The arm times out (and, if the patient keeps looking, the same
        # tile re-arms right away — that is the existing behaviour).
        self.assertEqual(len(cancels), 1)

    def test_short_blink_does_not_confirm(self):
        self.look(1, 1.3)
        self.c.on_blink(BlinkType.SHORT_DELIBERATE, self.t)
        self.c.on_blink(BlinkType.NATURAL, self.t)
        self.assertEqual(self.confirmed, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
