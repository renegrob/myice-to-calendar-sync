"""Tests for the preparation (warm-up) entry derived from each event."""
import contextlib
import io
import unittest

import lambda_function as lf
from test_myice import config, record


def parent(**cfg_overrides):
    cfg = config(**cfg_overrides)
    return cfg, lf.record_to_google_body(record(), cfg, "sync", "confirmed")


class PrepEnabled(unittest.TestCase):
    def test_no_prep_minutes_means_no_entry(self):
        cfg, body = parent()
        self.assertIsNone(lf.prep_body(record(), cfg, "confirmed"))

    def test_prep_minutes_creates_an_entry(self):
        cfg, body = parent(prep_minutes=60)
        self.assertIsNotNone(lf.prep_body(record(), cfg, "confirmed"))

    def test_zero_prep_minutes_means_no_entry_without_a_meeting_time(self):
        # Zero means "no fallback offset", so with nothing but a placeholder
        # meeting time there is no block to reserve.
        cfg, body = parent(prep_minutes=0)
        self.assertIsNone(lf.prep_body(record(), cfg, "confirmed"))

    def test_zero_prep_minutes_still_honours_a_real_meeting_time(self):
        # The club's own stated meeting time is real data; it does not need a
        # fallback offset configured to be worth putting on the calendar.
        cfg, body = parent(prep_minutes=0)
        self.assertIsNotNone(
            lf.prep_body(record(meeting="18:45:00"), cfg, "confirmed"))

    def test_no_prep_minutes_at_all_still_honours_a_real_meeting_time(self):
        cfg, body = parent()
        self.assertIsNotNone(
            lf.prep_body(record(meeting="18:45:00"), cfg, "confirmed"))

    def test_negative_prep_minutes_means_no_entry(self):
        # A config typo must not produce an inverted event (start after end).
        cfg, body = parent(prep_minutes=-30)
        self.assertIsNone(lf.prep_body(record(), cfg, "confirmed"))

    def test_negative_prep_minutes_logs_a_warning(self):
        cfg, body = parent(prep_minutes=-30)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            lf.prep_body(record(), cfg, "confirmed")
        out = buf.getvalue()
        self.assertIn("WARNING", out)
        self.assertIn("-30", out)

    def test_zero_prep_minutes_is_silent(self):
        cfg, body = parent(prep_minutes=0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            lf.prep_body(record(), cfg, "confirmed")
        self.assertEqual(buf.getvalue(), "")


class PrepTiming(unittest.TestCase):
    def test_offset_is_used_when_no_meeting_time(self):
        cfg, body = parent(prep_minutes=60, timezone="Europe/Zurich")
        prep = lf.prep_body(record(meeting="00:00:00"), cfg, "confirmed")
        self.assertEqual(prep["start"]["dateTime"], "2099-03-14T18:30:00+01:00")
        self.assertEqual(prep["end"]["dateTime"], "2099-03-14T19:30:00+01:00")

    def test_meeting_time_wins_over_the_offset(self):
        cfg, body = parent(prep_minutes=60, timezone="Europe/Zurich")
        prep = lf.prep_body(record(meeting="18:45:00"), cfg, "confirmed")
        self.assertEqual(prep["start"]["dateTime"], "2099-03-14T18:45:00+01:00")
        self.assertEqual(prep["end"]["dateTime"], "2099-03-14T19:30:00+01:00")

    def test_placeholder_meeting_times_are_treated_as_absent(self):
        cfg, body = parent(prep_minutes=30, timezone="Europe/Zurich")
        for placeholder in ("00:00", "00:00:00", ""):
            prep = lf.prep_body(record(meeting=placeholder), cfg, "confirmed")
            self.assertEqual(prep["start"]["dateTime"], "2099-03-14T19:00:00+01:00",
                             f"meeting={placeholder!r}")

    def test_meeting_at_or_after_start_falls_back_to_the_offset(self):
        cfg, body = parent(prep_minutes=60, timezone="Europe/Zurich")
        for bad in ("19:30:00", "20:00:00"):
            prep = lf.prep_body(record(meeting=bad), cfg, "confirmed")
            self.assertEqual(prep["start"]["dateTime"], "2099-03-14T18:30:00+01:00",
                             f"meeting={bad!r}")

    def test_meeting_time_alone_spans_meeting_to_event_start(self):
        cfg, body = parent(prep_minutes=0, timezone="Europe/Zurich")
        prep = lf.prep_body(record(meeting="18:45:00"), cfg, "confirmed")
        self.assertEqual(prep["start"]["dateTime"], "2099-03-14T18:45:00+01:00")
        self.assertEqual(prep["end"]["dateTime"], "2099-03-14T19:30:00+01:00")

    def test_meeting_at_or_after_start_without_an_offset_means_no_entry(self):
        # The offset fallback would put the block at the event start, i.e. a
        # zero-length entry. Nothing to reserve, so nothing is created.
        cfg, body = parent(prep_minutes=0, timezone="Europe/Zurich")
        for bad in ("19:30:00", "20:00:00"):
            with contextlib.redirect_stdout(io.StringIO()):
                prep = lf.prep_body(record(meeting=bad), cfg, "confirmed")
            self.assertIsNone(prep, f"meeting={bad!r}")

    def test_prep_always_ends_at_the_event_start(self):
        cfg, body = parent(prep_minutes=90, timezone="Europe/Zurich")
        prep = lf.prep_body(record(), cfg, "confirmed")
        self.assertEqual(prep["end"]["dateTime"], body["start"]["dateTime"])


class PrepContent(unittest.TestCase):
    def test_default_summary_template(self):
        cfg, body = parent(prep_minutes=60)
        prep = lf.prep_body(record(), cfg, "confirmed")
        self.assertEqual(prep["summary"], "Warm-up: U13 vs Eisbären")

    def test_custom_summary_template(self):
        cfg, body = parent(prep_minutes=60, prep_summary_format="🔥 {summary}")
        prep = lf.prep_body(record(), cfg, "confirmed")
        self.assertEqual(prep["summary"], "🔥 U13 vs Eisbären")

    def test_prep_colour_overrides_the_club_colour(self):
        cfg, body = parent(prep_minutes=60, color_id="7", prep_color_id="2")
        prep = lf.prep_body(record(), cfg, "confirmed")
        self.assertEqual(prep["colorId"], "2")

    def test_falls_back_to_the_club_colour(self):
        cfg, body = parent(prep_minutes=60, color_id="7")
        prep = lf.prep_body(record(), cfg, "confirmed")
        self.assertEqual(prep["colorId"], "7")

    def test_inherits_location_and_details(self):
        blob = "Speaker: John Doe"
        cfg, body = parent(prep_minutes=60)
        prep = lf.prep_body(record(notes=blob), cfg, "confirmed")
        self.assertEqual(prep["location"], "Eishalle Nord")
        self.assertIn(blob, prep["description"])

    def test_location_and_details_come_from_the_record(self):
        # One source of truth: prep_body takes no parent_body parameter at
        # all, so there is no way for a caller to leak stale parent text in -
        # location and description can only ever come from `record`.
        cfg, _body = parent(prep_minutes=60)
        prep = lf.prep_body(record(place="Eishalle Süd", notes="Bring sticks"),
                            cfg, "confirmed")
        self.assertEqual(prep["location"], "Eishalle Süd")
        self.assertIn("Bring sticks", prep["description"])

    def test_record_without_place_has_no_location(self):
        cfg, body = parent(prep_minutes=60)
        prep = lf.prep_body(record(place=""), cfg, "confirmed")
        self.assertNotIn("location", prep)

    def test_request_status_is_inherited(self):
        cfg, body = parent(prep_minutes=60)
        prep = lf.prep_body(record(health_status="3"), cfg, "tentative")
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
