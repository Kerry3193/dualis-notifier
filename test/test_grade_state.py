import unittest
from pathlib import Path

import pandas as pd

from dualis_notifier import MISSING_SINCE_COLUMN, reconcile_grades
from grade_state import extract_data_from_html, prepare_grades


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

    def test_parser_selects_grade_table_and_preserves_decimal_comma(self):
        html = (Path(__file__).parent / "fixtures" / "grades.html").read_text()
        parsed = extract_data_from_html(html)
        self.assertEqual(parsed.loc[0, "Endnote"], "1,7")
        self.assertEqual(parsed.loc[1, "Endnote"], "")
        self.assertEqual(len(parsed), 2)
        self.assertNotIn("Unnamed: 5", parsed.columns)

    def test_parser_rejects_login_missing_grade_column_and_ambiguous_tables(self):
        valid = "<table><tr><th>Name</th><th>Endnote</th></tr><tr><td>A</td><td>1,7</td></tr></table>"
        for html in ("<html>Login</html>", "<table><tr><th>Name</th></tr><tr><td>A</td></tr></table>", valid + valid):
            with self.subTest(html=html), self.assertRaises(ValueError):
                extract_data_from_html(html)

    def test_omitted_optional_column_retains_value_without_events(self):
        state, _ = reconcile_grades(self.empty_cache, self.initial, "t1")
        without_credits, events = reconcile_grades(state, self.initial.drop(columns="Credits"), "t2")
        self.assertEqual(without_credits.loc[0, "Credits"], "5")
        self.assertEqual(events, [])
        _, restored_events = reconcile_grades(without_credits, self.initial, "t3")
        self.assertEqual(restored_events, [])

    def test_missing_required_column_is_rejected(self):
        with self.assertRaises(ValueError):
            reconcile_grades(self.empty_cache, self.initial.drop(columns="Endnote"), "t1")

    def test_numeric_format_change_does_not_notify(self):
        state, _ = reconcile_grades(self.empty_cache, self.initial, "t1")
        _, events = reconcile_grades(state, self.initial.assign(Endnote="1.70", Credits="5.0"), "t2")
        self.assertEqual(events, [])

    def test_missing_number_and_return_do_not_duplicate_module(self):
        state, _ = reconcile_grades(self.empty_cache, self.initial, "t1")
        fallback, events = reconcile_grades(state, self.initial.assign(**{"Nr.": ""}), "t2")
        self.assertEqual(events, [])
        self.assertEqual(fallback.loc[0, "Nr."], "M-101")
        restored, events = reconcile_grades(fallback, self.initial, "t3")
        self.assertEqual(len(restored), 1)
        self.assertEqual(events, [])

    def test_legacy_name_identity_can_acquire_module_number_silently(self):
        old = self.initial.drop(columns="Nr.")
        migrated, events = reconcile_grades(old, self.initial, "t2")
        self.assertEqual(len(migrated), 1)
        self.assertEqual(migrated.loc[0, "Nr."], "M-101")
        self.assertEqual(events, [])

    def test_duplicate_numbers_and_empty_names_are_rejected(self):
        for invalid in (grades(("M-101", "A", "1,7", "5", "bestanden"),
                               ("M-101", "B", "1,3", "5", "bestanden")),
                        grades(("M-101", "", "1,7", "5", "bestanden"))):
            with self.subTest(invalid=invalid.to_dict()), self.assertRaises(ValueError):
                prepare_grades(invalid)

    def test_same_name_with_different_module_numbers_remains_distinct(self):
        state, _ = reconcile_grades(self.empty_cache, self.initial, "t1")
        added = grades(("M-101", "Mathematik", "1,7", "5", "bestanden"),
                       ("M-202", "Mathematik", "2,0", "5", "bestanden"))
        state, events = reconcile_grades(state, added, "t2")
        self.assertEqual(len(state), 2)
        self.assertEqual(events, [{"type": "new", "name": "Mathematik"}])

    def test_ambiguous_identity_transition_cannot_produce_invalid_state(self):
        previous = grades(("M-101", "C", "1,7", "5", "bestanden"),
                          ("", "B", "1,3", "5", "bestanden"))
        current = grades(("M-101", "B", "1,7", "5", "bestanden"),
                         ("", "C", "1,3", "5", "bestanden"))
        with self.assertRaises(ValueError):
            reconcile_grades(previous, current, "t2")


if __name__ == "__main__":
    unittest.main()
