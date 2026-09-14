import unittest

import pandas as pd

from dualis_notifier import MISSING_SINCE_COLUMN, reconcile_grades


def grades(*rows):
    return pd.DataFrame(rows, columns=["Nr.", "Name", "Endnote", "Credits", "Status"])


class GradeStateTest(unittest.TestCase):
    def setUp(self):
        self.initial = grades(("M-101", "Mathematik", "1,7", "5", "bestanden"))
        self.empty_cache = pd.DataFrame()

    def test_new_module_creates_new_event(self):
        _, events = reconcile_grades(self.empty_cache, self.initial, "2026-09-14T06:00:00+00:00")

        self.assertEqual(events, [{"type": "new", "name": "Mathematik"}])

    def test_changed_grade_creates_changed_event(self):
        state, _ = reconcile_grades(self.empty_cache, self.initial, "2026-09-14T06:00:00+00:00")
        updated = grades(("M-101", "Mathematik", "1,3", "5", "bestanden"))

        _, events = reconcile_grades(state, updated, "2026-09-14T06:15:00+00:00")

        self.assertEqual(events, [{"type": "changed", "name": "Mathematik"}])

    def test_missing_module_is_marked_without_notification(self):
        state, _ = reconcile_grades(self.empty_cache, self.initial, "2026-09-14T06:00:00+00:00")

        missing_state, events = reconcile_grades(
            state, grades(), "2026-09-14T06:15:00+00:00"
        )

        self.assertEqual(events, [])
        self.assertEqual(
            missing_state.loc[0, MISSING_SINCE_COLUMN], "2026-09-14T06:15:00+00:00"
        )

    def test_unchanged_module_reappearing_after_absence_is_silent(self):
        state, _ = reconcile_grades(self.empty_cache, self.initial, "2026-09-14T06:00:00+00:00")
        missing_state, _ = reconcile_grades(state, grades(), "2026-09-14T06:15:00+00:00")

        restored_state, events = reconcile_grades(
            missing_state, self.initial, "2026-09-14T06:30:00+00:00"
        )

        self.assertEqual(events, [])
        self.assertEqual(restored_state.loc[0, MISSING_SINCE_COLUMN], "")


if __name__ == "__main__":
    unittest.main()
