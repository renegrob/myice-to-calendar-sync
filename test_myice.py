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


# A real notes blob from app.myice.hockey, names changed. myice stores these as
# HTML: <br /> line breaks, usually followed by a real newline, plus stray tabs
# and escaped entities.
REAL_NOTES = (
    "Coach: Andi R\u00fcegg Tel. 079 664 63 21<br />\r\n"
    "Betreuer: Ralph Vollenweider Tel. 079 458 92 26<br />\r\n"
    "<br />\r\n"
    "PLO<br />\r\n"
    "Speaker: Ren\u00e9 Grob\t<br />\r\n"
    "Zeit: Fam. Sturm\t<br />\r\n"
    "Kuchenbuffet: H&auml;rri &amp; Kalmbach<br />\r\n"
)


class HtmlToText(unittest.TestCase):
    def test_br_becomes_a_single_newline_not_two(self):
        out = lf.html_to_text("A<br />\r\nB")
        self.assertEqual(out, "A\nB")

    def test_br_without_a_newline_still_breaks(self):
        self.assertEqual(lf.html_to_text("A<br />B"), "A\nB")

    def test_consecutive_brs_keep_a_blank_line(self):
        """The blank line separates sections such as the PLO block."""
        self.assertIn("\n\nPLO", lf.html_to_text(REAL_NOTES))

    def test_no_markup_survives(self):
        out = lf.html_to_text(REAL_NOTES)
        self.assertNotIn("<br", out)
        self.assertNotIn("<", out)

    def test_entities_are_unescaped(self):
        self.assertIn("Härri & Kalmbach", lf.html_to_text(REAL_NOTES))

    def test_tabs_are_stripped_from_line_ends(self):
        self.assertIn("Speaker: René Grob\n", lf.html_to_text(REAL_NOTES) + "\n")
        self.assertNotIn("\t", lf.html_to_text(REAL_NOTES))

    def test_plain_text_is_unchanged(self):
        self.assertEqual(lf.html_to_text("Speaker: John Doe"), "Speaker: John Doe")


class DutyLinesFromRealNotes(unittest.TestCase):
    def test_a_duty_summary_carries_no_markup(self):
        """Without the HTML pass this came out as 'Speaker: René Grob\t<br />'."""
        import duty_parser
        lines = duty_parser.find_duty_lines(
            lf.event_details(record(notes=REAL_NOTES)), ["Grob"])
        self.assertEqual(lines, ["Speaker: René Grob"])

    def test_surname_matches_across_differently_named_duties(self):
        import duty_parser
        lines = duty_parser.find_duty_lines(
            lf.event_details(record(notes=REAL_NOTES)), ["Sturm", "Kalmbach"])
        self.assertEqual(lines, ["Zeit: Fam. Sturm", "Kuchenbuffet: Härri & Kalmbach"])


# A real practice record from app.myice.hockey. Note it has NO id_game, and
# "agegroup": null - both of which broke trainings entirely.
PRACTICE = {
    "id_practice": "860598", "id_group": "15660", "name": "U14 (ICE ALL)",
    "type": "Eistraining", "place": "", "date": "2026-10-14", "weekday": "Mi",
    "time_start": "17:00:00", "time_end": "17:55:00", "duration": 55,
    "health_status": "1", "health_status_label": "Gesund",
    "health_notes": "", "notes": "", "color": "#1184ea",
}


class PracticeRecords(unittest.TestCase):
    def test_id_comes_from_id_practice(self):
        """Practices have no id_game; keying off it skipped every training."""
        self.assertEqual(lf.record_id(PRACTICE), "860598")
        self.assertEqual(lf.record_uid(PRACTICE, "myice-t-"), "myice-t-860598")

    def test_game_id_still_comes_from_id_game(self):
        self.assertEqual(lf.record_id({"id_game": "178071"}), "178071")

    def test_a_record_with_neither_id_has_none(self):
        self.assertIsNone(lf.record_id({"name": "x"}))

    def test_null_agegroup_does_not_leak_the_string_None(self):
        """record.get("agegroup", "") returns None when the key holds null."""
        self.assertNotIn("None", lf.raw_summary(PRACTICE))

    def test_type_is_used_as_the_prefix_when_agegroup_is_absent(self):
        self.assertEqual(lf.raw_summary(PRACTICE), "Eistraining U14 (ICE ALL)")

    def test_agegroup_wins_over_type_for_games(self):
        self.assertEqual(
            lf.raw_summary({"agegroup": "U14 (A)", "name": "HC Eisbaeren",
                            "type": "Saison"}),
            "U14 (A) HC Eisbaeren")

    def test_falls_back_to_a_placeholder_when_everything_is_empty(self):
        self.assertEqual(lf.raw_summary({"agegroup": None, "name": None, "type": None}),
                         "myice.hockey Event")

    def test_a_practice_builds_a_full_body(self):
        body = lf.record_to_google_body(PRACTICE, config(timezone="Europe/Zurich"),
                                        "sync", "confirmed")
        self.assertEqual(body["summary"], "Eistraining U14 (ICE ALL)")
        self.assertEqual(body["start"]["dateTime"], "2026-10-14T17:00:00+02:00")
        self.assertEqual(body["end"]["dateTime"], "2026-10-14T17:55:00+02:00")
        self.assertNotIn("location", body)  # place is empty for this one

    def test_a_practice_survives_build_feed(self):
        uids, bodies = lf.build_feed([PRACTICE], config(uid_prefix="myice-t-"))
        self.assertEqual(uids, {"myice-t-860598"})
        self.assertEqual(len(bodies), 1)


if __name__ == "__main__":
    unittest.main()
