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


class UpdateFieldDiff(unittest.TestCase):
    """The spec requires the dry-run report to show the field-level diff for
    updates - execute_plan already prints one in apply mode; the dry run,
    where seeing the diff matters most (nothing has happened yet), must too."""

    def test_changed_summary_shows_old_and_new_values(self):
        s = section()
        s["existing"] = {
            "myice-2": {"summary": "U13 vs OLD",
                       "start": {"dateTime": "2099-03-15T19:30:00+01:00"}},
        }
        text = dry_run.render_report([s])
        self.assertIn("U13 vs OLD", text)
        self.assertIn("U13 vs B", text)

    def test_unchanged_fields_are_not_listed_in_the_diff(self):
        s = section()
        s["existing"] = {
            "myice-2": {"summary": "U13 vs OLD",
                       "start": {"dateTime": "2099-03-15T19:30:00+01:00"}},
        }
        text = dry_run.render_report([s])
        # 'start' is identical between existing and new - must not be listed.
        self.assertNotIn("start:", text)
        self.assertIn("summary:", text)

    def test_missing_existing_data_does_not_crash_and_omits_the_diff(self):
        # Older-shaped sections (or a uid with no prior existing event) must
        # still render - just without a diff for that update.
        s = section()
        text = dry_run.render_report([s])
        self.assertIn("UPDATE", text)


def detailed_section():
    """A section whose create carries the fields a reviewer needs to see."""
    s = section()
    s["plan"]["create"] = [("myice-1", {
        "summary": "U13 vs A",
        "start": {"dateTime": "2099-03-14T19:30:00+01:00"},
        "location": "Eishalle Deutweg, 8400 Winterthur ZH",
        "description": "Bring white jersey\nMeeting time: 18:30",
    })]
    return s


class EventDetails(unittest.TestCase):
    """The summary alone is not enough to review what --apply would write:
    location and description are what a human checks before the first run."""

    def test_create_shows_the_location(self):
        text = dry_run.render_report([detailed_section()])
        self.assertIn("location: Eishalle Deutweg, 8400 Winterthur ZH", text)

    def test_create_shows_the_description(self):
        text = dry_run.render_report([detailed_section()])
        self.assertIn("description: Bring white jersey", text)

    def test_continuation_lines_align_under_the_description(self):
        text = dry_run.render_report([detailed_section()])
        lines = text.splitlines()
        first = next(i for i, l in enumerate(lines) if "description:" in l)
        label_col = lines[first].index("description: ") + len("description: ")
        self.assertEqual(lines[first + 1], " " * label_col + "Meeting time: 18:30")

    def test_an_absent_location_is_reported_as_none(self):
        # Practice records often carry place="", and a silently missing line
        # is indistinguishable from the report not showing locations at all.
        s = section()
        s["plan"]["create"] = [("myice-1", {
            "summary": "Eistraining",
            "start": {"dateTime": "2099-03-14T19:30:00+01:00"},
        })]
        text = dry_run.render_report([s])
        self.assertIn("location: (none)", text)
        self.assertIn("description: (none)", text)

    def test_updates_show_details_too(self):
        s = detailed_section()
        s["plan"]["update"] = [("myice-2", {
            "summary": "U13 vs B",
            "start": {"dateTime": "2099-03-15T19:30:00+01:00"},
            "location": "Swiss Life Arena",
        }, "gid-2")]
        text = dry_run.render_report([s])
        self.assertIn("location: Swiss Life Arena", text)

    def test_delete_and_tombstone_lines_have_no_details(self):
        # Those plan entries carry a uid only - there is no body to expand.
        text = dry_run.render_report([detailed_section()])
        for line in text.splitlines():
            if line.strip().startswith(("DELETE", "TOMBSTONE")):
                self.assertNotIn("location:", line)


def past_section():
    s = section()
    s["plan"]["skipped_past"] = [("myice-9", {
        "summary": "Trockentraining",
        "start": {"dateTime": "2020-05-12T16:30:00+02:00"},
        "location": "Turnhalle Buchlern",
    })]
    return s


class VerbosePastEntries(unittest.TestCase):
    """Past-skipped entries outnumber the writes several times over, so they
    stay one-liners unless the reviewer asks for everything."""

    def test_past_entries_are_one_liners_by_default(self):
        text = dry_run.render_report([past_section()])
        self.assertIn("Trockentraining", text)
        self.assertNotIn("Turnhalle Buchlern", text)

    def test_verbose_expands_past_entries(self):
        text = dry_run.render_report([past_section()], verbose=True)
        self.assertIn("location: Turnhalle Buchlern", text)

    def test_verbose_still_expands_creates(self):
        s = past_section()
        s["plan"]["create"] = detailed_section()["plan"]["create"]
        text = dry_run.render_report([s], verbose=True)
        self.assertIn("location: Eishalle Deutweg, 8400 Winterthur ZH", text)


if __name__ == "__main__":
    unittest.main()
