"""Tests for classifying myice records and mapping them to Google bodies."""
import unittest

import lambda_function as lf


def record(**overrides):
    base = {
        "id_game": "5001",
        "date": "2099-03-14",
        "time_start": "19:30:00",
        "time_end": "21:00:00",
        "agegroup": "U13",
        "name": "vs Eisbären",
        "place": "Eishalle Nord",
        "health_status": "1",
        "health_status_label": "Gesund",
        "meeting": "00:00:00",
        "notes": "",
        "health_notes": "",
    }
    base.update(overrides)
    return base


def config(**overrides):
    base = {"calendar_id": "cal@example.com", "uid_prefix": "myice-"}
    base.update(overrides)
    return base


class Classify(unittest.TestCase):
    def test_gesund_syncs_confirmed(self):
        self.assertEqual(lf.classify(record(health_status="1")), ("sync", "confirmed"))

    def test_temporaer_is_a_request_and_tentative(self):
        self.assertEqual(lf.classify(record(health_status="3")), ("request", "tentative"))

    def test_entschuldigt_is_removed(self):
        self.assertEqual(lf.classify(record(health_status="6"))[0], "remove")

    def test_krank_is_removed(self):
        self.assertEqual(lf.classify(record(health_status="8"))[0], "remove")

    def test_verletzt_is_removed(self):
        self.assertEqual(lf.classify(record(health_status="9"))[0], "remove")

    def test_unknown_status_syncs_confirmed(self):
        self.assertEqual(lf.classify(record(health_status="99")), ("sync", "confirmed"))

    def test_integer_status_is_handled(self):
        self.assertEqual(lf.classify(record(health_status=3)), ("request", "tentative"))

    def test_missing_status_syncs_confirmed(self):
        r = record()
        del r["health_status"]
        self.assertEqual(lf.classify(r), ("sync", "confirmed"))


class RecordToBody(unittest.TestCase):
    def test_summary_combines_agegroup_and_name(self):
        body = lf.record_to_google_body(record(), config(), "sync", "confirmed")
        self.assertEqual(body["summary"], "U13 vs Eisbären")

    def test_summary_format_is_applied(self):
        body = lf.record_to_google_body(
            record(), config(summary_format="🏒 {summary}"), "sync", "confirmed")
        self.assertEqual(body["summary"], "🏒 U13 vs Eisbären")

    def test_request_uses_the_request_template_and_colour(self):
        body = lf.record_to_google_body(
            record(health_status="3"),
            config(request_summary_format="❓ {summary}", request_color_id="5"),
            "request", "tentative",
        )
        self.assertEqual(body["summary"], "❓ U13 vs Eisbären")
        self.assertEqual(body["colorId"], "5")
        self.assertEqual(body["status"], "tentative")

    def test_request_falls_back_to_default_template(self):
        body = lf.record_to_google_body(
            record(health_status="3"), config(), "request", "tentative")
        self.assertEqual(body["summary"], "❓ U13 vs Eisbären")

    def test_invalid_summary_format_falls_back_to_raw(self):
        body = lf.record_to_google_body(
            record(), config(summary_format="{nope}"), "sync", "confirmed")
        self.assertEqual(body["summary"], "U13 vs Eisbären")

    def test_missing_name_and_agegroup_gets_a_placeholder(self):
        body = lf.record_to_google_body(
            record(agegroup="", name=""), config(), "sync", "confirmed")
        self.assertEqual(body["summary"], "myice.hockey Event")

    def test_start_and_end_use_the_feed_timezone(self):
        body = lf.record_to_google_body(
            record(), config(timezone="Europe/Zurich"), "sync", "confirmed")
        self.assertEqual(body["start"]["dateTime"], "2099-03-14T19:30:00+01:00")
        self.assertEqual(body["end"]["dateTime"], "2099-03-14T21:00:00+01:00")
        self.assertEqual(body["start"]["timeZone"], "Europe/Zurich")

    def test_place_becomes_location(self):
        body = lf.record_to_google_body(record(), config(), "sync", "confirmed")
        self.assertEqual(body["location"], "Eishalle Nord")

    def test_source_tag_is_set(self):
        body = lf.record_to_google_body(record(), config(), "sync", "confirmed")
        self.assertEqual(
            body["extendedProperties"]["private"]["source"], "myice-calendar-sync")

    def test_always_returns_a_body_even_for_past_events(self):
        # Past-event policy lives in plan_sync, not the mapper.
        body = lf.record_to_google_body(
            record(date="2001-01-01"), config(), "sync", "confirmed")
        self.assertIsNotNone(body)


class EventDetails(unittest.TestCase):
    def test_notes_are_included_verbatim(self):
        blob = "Speaker: John Doe\nZeit: Fam. Brown"
        self.assertIn(blob, lf.event_details(record(notes=blob)))

    def test_health_notes_are_included(self):
        self.assertIn("Zurück ab Montag",
                      lf.event_details(record(health_notes="Zurück ab Montag")))

    def test_real_meeting_time_is_included(self):
        self.assertIn("18:45", lf.event_details(record(meeting="18:45:00")))

    def test_placeholder_meeting_times_are_suppressed(self):
        self.assertNotIn("Meeting", lf.event_details(record(meeting="00:00:00")))
        self.assertNotIn("Meeting", lf.event_details(record(meeting="00:00")))

    def test_status_label_is_included(self):
        self.assertIn("Gesund", lf.event_details(record()))

    def test_empty_record_yields_empty_details(self):
        r = record(notes="", health_notes="", meeting="00:00:00",
                   health_status_label="")
        self.assertEqual(lf.event_details(r), "")


class RecordUid(unittest.TestCase):
    def test_uid_is_prefixed_game_id(self):
        self.assertEqual(lf.record_uid(record(), "myice-"), "myice-5001")

    def test_uid_is_stable(self):
        self.assertEqual(lf.record_uid(record(), "myice-"),
                         lf.record_uid(record(), "myice-"))


if __name__ == "__main__":
    unittest.main()
