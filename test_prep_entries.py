"""Tests for the preparation (warm-up) entry derived from each event."""
import unittest

import lambda_function as lf
from test_myice import config, record


def parent(**cfg_overrides):
    cfg = config(**cfg_overrides)
    return cfg, lf.record_to_google_body(record(), cfg, "sync", "confirmed")


class PrepEnabled(unittest.TestCase):
    def test_no_prep_minutes_means_no_entry(self):
        cfg, body = parent()
        self.assertIsNone(lf.prep_body(record(), cfg, body, "confirmed"))

    def test_prep_minutes_creates_an_entry(self):
        cfg, body = parent(prep_minutes=60)
        self.assertIsNotNone(lf.prep_body(record(), cfg, body, "confirmed"))


class PrepTiming(unittest.TestCase):
    def test_offset_is_used_when_no_meeting_time(self):
        cfg, body = parent(prep_minutes=60, timezone="Europe/Zurich")
        prep = lf.prep_body(record(meeting="00:00:00"), cfg, body, "confirmed")
        self.assertEqual(prep["start"]["dateTime"], "2099-03-14T18:30:00+01:00")
        self.assertEqual(prep["end"]["dateTime"], "2099-03-14T19:30:00+01:00")

    def test_meeting_time_wins_over_the_offset(self):
        cfg, body = parent(prep_minutes=60, timezone="Europe/Zurich")
        prep = lf.prep_body(record(meeting="18:45:00"), cfg, body, "confirmed")
        self.assertEqual(prep["start"]["dateTime"], "2099-03-14T18:45:00+01:00")
        self.assertEqual(prep["end"]["dateTime"], "2099-03-14T19:30:00+01:00")

    def test_placeholder_meeting_times_are_treated_as_absent(self):
        cfg, body = parent(prep_minutes=30, timezone="Europe/Zurich")
        for placeholder in ("00:00", "00:00:00", ""):
            prep = lf.prep_body(record(meeting=placeholder), cfg, body, "confirmed")
            self.assertEqual(prep["start"]["dateTime"], "2099-03-14T19:00:00+01:00",
                             f"meeting={placeholder!r}")

    def test_meeting_at_or_after_start_falls_back_to_the_offset(self):
        cfg, body = parent(prep_minutes=60, timezone="Europe/Zurich")
        for bad in ("19:30:00", "20:00:00"):
            prep = lf.prep_body(record(meeting=bad), cfg, body, "confirmed")
            self.assertEqual(prep["start"]["dateTime"], "2099-03-14T18:30:00+01:00",
                             f"meeting={bad!r}")

    def test_prep_always_ends_at_the_event_start(self):
        cfg, body = parent(prep_minutes=90, timezone="Europe/Zurich")
        prep = lf.prep_body(record(), cfg, body, "confirmed")
        self.assertEqual(prep["end"]["dateTime"], body["start"]["dateTime"])


class PrepContent(unittest.TestCase):
    def test_default_summary_template(self):
        cfg, body = parent(prep_minutes=60)
        prep = lf.prep_body(record(), cfg, body, "confirmed")
        self.assertEqual(prep["summary"], "Warm-up: U13 vs Eisbären")

    def test_custom_summary_template(self):
        cfg, body = parent(prep_minutes=60, prep_summary_format="🔥 {summary}")
        prep = lf.prep_body(record(), cfg, body, "confirmed")
        self.assertEqual(prep["summary"], "🔥 U13 vs Eisbären")

    def test_prep_colour_overrides_the_club_colour(self):
        cfg, body = parent(prep_minutes=60, color_id="7", prep_color_id="2")
        prep = lf.prep_body(record(), cfg, body, "confirmed")
        self.assertEqual(prep["colorId"], "2")

    def test_falls_back_to_the_club_colour(self):
        cfg, body = parent(prep_minutes=60, color_id="7")
        prep = lf.prep_body(record(), cfg, body, "confirmed")
        self.assertEqual(prep["colorId"], "7")

    def test_inherits_location_and_details(self):
        blob = "Speaker: John Doe"
        cfg, body = parent(prep_minutes=60)
        prep = lf.prep_body(record(notes=blob), cfg, body, "confirmed")
        self.assertEqual(prep["location"], "Eishalle Nord")
        self.assertIn(blob, prep["description"])

    def test_request_status_is_inherited(self):
        cfg, body = parent(prep_minutes=60)
        prep = lf.prep_body(record(health_status="3"), cfg, body, "tentative")
        self.assertEqual(prep["status"], "tentative")


class PrepUid(unittest.TestCase):
    def test_uid_shape(self):
        self.assertEqual(lf.prep_uid(record(), "myice-"), "myice-prep-5001")

    def test_uid_is_stable_across_meeting_time_changes(self):
        # A changed meeting time must update the entry, not delete and recreate it.
        a = lf.prep_uid(record(meeting="18:45:00"), "myice-")
        b = lf.prep_uid(record(meeting="19:00:00"), "myice-")
        self.assertEqual(a, b)

    def test_prep_uid_differs_from_the_event_uid(self):
        self.assertNotEqual(lf.prep_uid(record(), "myice-"),
                            lf.record_uid(record(), "myice-"))


if __name__ == "__main__":
    unittest.main()
