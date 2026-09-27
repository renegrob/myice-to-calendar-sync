"""Tests for feed assembly, config validation, and club selection."""
import unittest

import lambda_function as lf
from test_myice import config, record


class BuildFeed(unittest.TestCase):
    def test_a_healthy_record_yields_one_event(self):
        uids, bodies = lf.build_feed([record()], config())
        self.assertEqual(uids, {"myice-5001"})
        self.assertEqual(set(bodies), {"myice-5001"})

    def test_prep_and_duty_entries_are_included(self):
        cfg = config(prep_minutes=60, duty_names=["John Doe"])
        uids, bodies = lf.build_feed([record(notes="Speaker: John Doe")], cfg)
        self.assertIn("myice-5001", bodies)
        self.assertIn("myice-prep-5001", bodies)
        self.assertEqual(len([u for u in bodies if "-duty-" in u]), 1)
        self.assertEqual(uids, set(bodies))

    def test_removed_status_contributes_nothing(self):
        cfg = config(prep_minutes=60, duty_names=["John Doe"])
        for status in ("6", "8", "9"):
            uids, bodies = lf.build_feed(
                [record(health_status=status, notes="Speaker: John Doe")], cfg)
            self.assertEqual(uids, set(), f"status={status}")
            self.assertEqual(bodies, {}, f"status={status}")

    def test_request_status_marks_everything_tentative(self):
        cfg = config(prep_minutes=60, duty_names=["John Doe"])
        _uids, bodies = lf.build_feed(
            [record(health_status="3", notes="Speaker: John Doe")], cfg)
        self.assertTrue(all(b["status"] == "tentative" for b in bodies.values()))

    def test_every_body_carries_its_uid_as_icaluid(self):
        cfg = config(prep_minutes=60, duty_names=["John Doe"])
        _uids, bodies = lf.build_feed([record(notes="Speaker: John Doe")], cfg)
        for uid, body in bodies.items():
            self.assertEqual(body["iCalUID"], uid)


class ValidateConfigs(unittest.TestCase):
    def full(self, **overrides):
        cfg = {
            "calendar_id": "c@example.com",
            "myice_login_url": "https://x/login",
            "myice_username_field": "email",
            "myice_password_field": "password",
            "myice_credentials_param": "/myice-sync/myice-credentials",
            "myice_filter_url": "https://x/filter",
            "myice_player_id": "1", "myice_event_type": "g",
            "myice_season": "1", "myice_club": "1",
            "myice_min_date": "2026-04-01", "myice_max_date": "2027-04-30",
        }
        cfg.update(overrides)
        return cfg

    def test_a_complete_config_passes(self):
        lf.validate_configs([self.full()])

    def test_a_missing_required_field_raises(self):
        cfg = self.full()
        del cfg["myice_player_id"]
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([cfg])
        self.assertIn("myice_player_id", str(ctx.exception))

    def test_duplicate_calendar_and_prefix_raises(self):
        a, b = self.full(uid_prefix="myice-"), self.full(uid_prefix="myice-")
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([a, b])
        self.assertIn("uid_prefix", str(ctx.exception))

    def test_same_calendar_with_different_prefixes_is_fine(self):
        lf.validate_configs([self.full(uid_prefix="a-"), self.full(uid_prefix="b-")])

    def test_different_calendars_with_the_same_prefix_is_fine(self):
        lf.validate_configs([self.full(calendar_id="one@x"),
                             self.full(calendar_id="two@x")])

    def test_bad_event_type_raises(self):
        with self.assertRaises(RuntimeError):
            lf.validate_configs([self.full(myice_event_type="x")])

    def test_validation_reports_the_config_index(self):
        cfg = self.full()
        del cfg["calendar_id"]
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([self.full(), cfg])
        self.assertIn("1", str(ctx.exception))

    def test_non_numeric_prep_minutes_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([self.full(prep_minutes="abc")])
        self.assertIn("prep_minutes", str(ctx.exception))
        self.assertIn("abc", str(ctx.exception))

    def test_negative_prep_minutes_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([self.full(prep_minutes=-5)])
        self.assertIn("prep_minutes", str(ctx.exception))
        self.assertIn("-5", str(ctx.exception))


class SelectConfigs(unittest.TestCase):
    def setUp(self):
        self.configs = [{"myice_event_type": "g"}, {"myice_event_type": "p"},
                        {"myice_event_type": "g"}]

    def test_none_returns_everything(self):
        self.assertEqual(len(lf.select_configs(self.configs, None)), 3)

    def test_games_only(self):
        picked = lf.select_configs(self.configs, "g")
        self.assertTrue(all(c["myice_event_type"] == "g" for c in picked))
        self.assertEqual(len(picked), 2)

    def test_trainings_only(self):
        self.assertEqual(len(lf.select_configs(self.configs, "p")), 1)


if __name__ == "__main__":
    unittest.main()
