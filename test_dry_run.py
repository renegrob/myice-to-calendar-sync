"""Tests for the dry-run report."""
import unittest

import dry_run


def section():
    return {
        "index": 0,
        "config": {"calendar_id": "c@example.com", "uid_prefix": "myice-",
                   "myice_event_type": "g", "myice_club": "3"},
        "plan": {
            "create": [("myice-1", {"summary": "U13 vs A",
                                    "start": {"dateTime": "2099-03-14T19:30:00+01:00"}})],
            "update": [("myice-2", {"summary": "U13 vs B",
                                    "start": {"dateTime": "2099-03-15T19:30:00+01:00"}},
                        "gid-2")],
            "delete": [("myice-3", "gid-3")],
            "unchanged": ["myice-4"],
            "tombstone": ["myice-5"],
            "skip_tombstoned": [],
        },
        "counts": {"created": 1, "updated": 1, "deleted": 1, "unchanged": 1,
                   "tombstoned": 1, "skipped_tombstoned": 0, "total_in_feed": 5},
    }


class RenderReport(unittest.TestCase):
    def setUp(self):
        self.text = dry_run.render_report([section()])

    def test_lists_creates(self):
        self.assertIn("U13 vs A", self.text)
        self.assertIn("CREATE", self.text)

    def test_lists_updates_and_deletes(self):
        self.assertIn("UPDATE", self.text)
        self.assertIn("DELETE", self.text)

    def test_identifies_the_club_feed(self):
        self.assertIn("c@example.com", self.text)
        self.assertIn("myice-", self.text)

    def test_includes_a_count_summary(self):
        self.assertIn("1 created", self.text)
        self.assertIn("1 deleted", self.text)

    def test_marks_derived_entries(self):
        s = section()
        s["plan"]["create"].append(
            ("myice-prep-1", {"summary": "Warm-up: U13 vs A",
                              "start": {"dateTime": "2099-03-14T18:30:00+01:00"}}))
        s["plan"]["create"].append(
            ("myice-duty-1-abcd1234", {"summary": "Speaker: John Doe",
                                       "start": {"dateTime": "2099-03-14T19:30:00+01:00"}}))
        text = dry_run.render_report([s])
        self.assertIn("[prep]", text)
        self.assertIn("[duty]", text)

    def test_an_empty_plan_still_renders(self):
        empty = section()
        empty["plan"] = {k: [] for k in empty["plan"]}
        empty["counts"] = {k: 0 for k in empty["counts"]}
        self.assertIn("c@example.com", dry_run.render_report([empty]))


if __name__ == "__main__":
    unittest.main()
