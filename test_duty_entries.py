"""Tests for the extra calendar entries created from duty lines."""
import unittest

import lambda_function as lf
from test_myice import config, record

BLOB = """Coach: Anna Keller Tel. 079 111 22 33
Speaker: John Doe
Zeit: Fam. Brown
Strafbank: Smith / Green"""


def parent(**cfg_overrides):
    cfg = config(**cfg_overrides)
    rec = record(notes=BLOB)
    return rec, cfg, lf.record_to_google_body(rec, cfg, "sync", "confirmed")


class DutyBodies(unittest.TestCase):
    def test_no_duty_names_means_no_entries(self):
        rec, cfg, body = parent()
        self.assertEqual(lf.duty_bodies(rec, cfg, body, "confirmed"), {})

    def test_no_match_means_no_entries(self):
        rec, cfg, body = parent(duty_names=["Nobody"])
        self.assertEqual(lf.duty_bodies(rec, cfg, body, "confirmed"), {})

    def test_one_entry_per_matched_line(self):
        rec, cfg, body = parent(duty_names=["Brown", "John Doe"])
        self.assertEqual(len(lf.duty_bodies(rec, cfg, body, "confirmed")), 2)

    def test_summary_defaults_to_the_matched_line(self):
        rec, cfg, body = parent(duty_names=["John Doe"])
        summaries = [b["summary"] for b in lf.duty_bodies(rec, cfg, body, "confirmed").values()]
        self.assertEqual(summaries, ["Speaker: John Doe"])

    def test_custom_summary_template_can_use_line_and_summary(self):
        rec, cfg, body = parent(duty_names=["John Doe"],
                                duty_summary_format="{duty} ({summary})")
        summaries = [b["summary"] for b in lf.duty_bodies(rec, cfg, body, "confirmed").values()]
        self.assertEqual(summaries, ["Speaker: John Doe (U13 vs Eisbären)"])

    def test_description_is_the_full_blob(self):
        rec, cfg, body = parent(duty_names=["John Doe"])
        duty = next(iter(lf.duty_bodies(rec, cfg, body, "confirmed").values()))
        self.assertIn(BLOB, duty["description"])

    def test_times_match_the_parent_event(self):
        rec, cfg, body = parent(duty_names=["John Doe"])
        duty = next(iter(lf.duty_bodies(rec, cfg, body, "confirmed").values()))
        self.assertEqual(duty["start"], body["start"])
        self.assertEqual(duty["end"], body["end"])

    def test_duty_colour_overrides_the_club_colour(self):
        rec, cfg, body = parent(duty_names=["John Doe"], color_id="7", duty_color_id="11")
        duty = next(iter(lf.duty_bodies(rec, cfg, body, "confirmed").values()))
        self.assertEqual(duty["colorId"], "11")

    def test_request_status_is_inherited(self):
        rec, cfg, body = parent(duty_names=["John Doe"])
        duty = next(iter(lf.duty_bodies(rec, cfg, body, "tentative").values()))
        self.assertEqual(duty["status"], "tentative")

    def test_location_is_inherited(self):
        rec, cfg, body = parent(duty_names=["John Doe"])
        duty = next(iter(lf.duty_bodies(rec, cfg, body, "confirmed").values()))
        self.assertEqual(duty["location"], "Eishalle Nord")


class DutyUid(unittest.TestCase):
    def test_uid_is_stable_for_the_same_line(self):
        self.assertEqual(lf.duty_uid(record(), "myice-", "Speaker: John Doe"),
                         lf.duty_uid(record(), "myice-", "Speaker: John Doe"))

    def test_different_lines_get_different_uids(self):
        self.assertNotEqual(lf.duty_uid(record(), "myice-", "Speaker: John Doe"),
                            lf.duty_uid(record(), "myice-", "Zeit: Fam. Brown"))

    def test_uid_is_unaffected_by_reordering_other_lines(self):
        rec_a = record(notes="Speaker: John Doe\nZeit: Fam. Brown")
        rec_b = record(notes="Zeit: Fam. Brown\nSpeaker: John Doe")
        cfg = config(duty_names=["John Doe"])
        body_a = lf.record_to_google_body(rec_a, cfg, "sync", "confirmed")
        body_b = lf.record_to_google_body(rec_b, cfg, "sync", "confirmed")
        self.assertEqual(
            set(lf.duty_bodies(rec_a, cfg, body_a, "confirmed")),
            set(lf.duty_bodies(rec_b, cfg, body_b, "confirmed")),
        )

    def test_uid_carries_the_prefix_and_game_id(self):
        uid = lf.duty_uid(record(), "myice-", "Speaker: John Doe")
        self.assertTrue(uid.startswith("myice-duty-5001-"))

    def test_duty_uid_differs_from_event_and_prep_uids(self):
        uid = lf.duty_uid(record(), "myice-", "Speaker: John Doe")
        self.assertNotIn(uid, {lf.record_uid(record(), "myice-"),
                               lf.prep_uid(record(), "myice-")})


if __name__ == "__main__":
    unittest.main()
