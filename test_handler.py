"""Tests for feed assembly, config validation, and club selection."""
import contextlib
import importlib
import io
import os
import re
import unittest
from pathlib import Path
from unittest import mock

import lambda_function as lf
from test_myice import config, record

DEPLOY_SH = Path(__file__).parent / "deploy.sh"


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

    def test_a_record_with_no_id_game_is_skipped_not_collapsed(self):
        # record_uid/prep_uid/duty_uid all key off id_game, so every record
        # missing it would otherwise map to the same "...-None" UID, silently
        # merging unrelated events into one. Confirm no such UID is produced,
        # and that a second, well-formed record in the same feed still syncs.
        bad = record()
        del bad["id_game"]
        good = record(id_game="5002")
        uids, bodies = lf.build_feed([bad, good], config())
        self.assertEqual(uids, {"myice-5002"})
        self.assertEqual(set(bodies), {"myice-5002"})
        self.assertNotIn("myice-None", uids)

    def test_a_record_with_no_id_game_logs_a_warning(self):
        buf = io.StringIO()
        bad = record()
        del bad["id_game"]
        with contextlib.redirect_stdout(buf):
            lf.build_feed([bad], config())
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("id_game", buf.getvalue())

    def test_a_record_with_blank_id_game_is_also_skipped(self):
        bad = record(id_game="")
        uids, bodies = lf.build_feed([bad], config())
        self.assertEqual(uids, set())
        self.assertEqual(bodies, {})


class RemovalReachesThePlan(unittest.TestCase):
    """
    build_feed() contributing nothing for a removed record is only half the
    guarantee: plan_sync must then actually plan to delete whatever was
    previously synced for it - the event, its prep block and its duty
    entries - since all three simply vanish from feed_uids/feed_bodies.

    This is the seam between build_feed and plan_sync that per-function tests
    (BuildFeed above, and plan_sync's own tests in test_sync.py) cannot see:
    BuildFeed never looks at `existing` or calls plan_sync, and test_sync.py's
    feed-removal tests never build a feed through build_feed/duty parsing.
    """

    def test_health_status_change_to_removed_deletes_event_prep_and_duty(self):
        cfg = config(prep_minutes=60, duty_names=["John Doe"])
        rec = record(notes="Speaker: John Doe")

        # Previous run: healthy record produced event + prep + duty, now on
        # the calendar (as events.list() would return them).
        _uids, bodies = lf.build_feed([rec], cfg)
        self.assertEqual(len(bodies), 3, "expected event + prep + duty")
        event_uid = "myice-5001"
        prep_uid = "myice-prep-5001"
        duty_uid = next(u for u in bodies if "-duty-" in u)
        existing = {uid: {**body, "id": f"gid-{uid}"} for uid, body in bodies.items()}

        # This run: the player is now sick - the record's health_status flips
        # to "removed" (Krank = 8). The record is still present in myice's
        # feed; it is the *status* that changed, not its disappearance.
        removed_rec = record(health_status="8", notes="Speaker: John Doe")
        feed_uids, feed_bodies = lf.build_feed([removed_rec], cfg)
        self.assertEqual(feed_uids, set())
        self.assertEqual(feed_bodies, {})

        plan = lf.plan_sync(
            feed_uids, feed_bodies, existing,
            state={"synced": {}, "tombstones": {}},
            respect_deletes=False, uid_prefix="myice-",
        )

        deleted_uids = {uid for uid, _event_id in plan["delete"]}
        self.assertEqual(deleted_uids, {event_uid, prep_uid, duty_uid})


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

    def test_unreplaced_calendar_placeholder_raises(self):
        """Otherwise this surfaces as a bare 404 from Google."""
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([self.full(calendar_id="your.email@gmail.com")])
        self.assertIn("calendar_id", str(ctx.exception))
        self.assertIn("placeholder", str(ctx.exception).lower())

    def test_unreplaced_todo_placeholder_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([self.full(myice_club="TODO_club_a_id")])
        self.assertIn("myice_club", str(ctx.exception))

    def test_placeholder_inside_a_list_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([self.full(duty_names=["Smith", "TODO_your_surname"])])
        self.assertIn("duty_names", str(ctx.exception))

    def test_placeholder_inside_a_dict_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs(
                [self.full(myice_login_extra_fields={"token": "TODO_capture"})])
        self.assertIn("myice_login_extra_fields", str(ctx.exception))

    def test_the_example_template_is_rejected_until_filled_in(self):
        import sync_configs_example
        with self.assertRaises(RuntimeError):
            lf.validate_configs(sync_configs_example.CONFIGS)

    def test_real_values_that_merely_resemble_placeholders_pass(self):
        # "Todo" as a surname, and a calendar whose name contains "your".
        lf.validate_configs([self.full(
            calendar_id="youry.team@group.calendar.google.com",
            duty_names=["Todorov"],
        )])

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

    def test_overlapping_prefixes_on_the_same_calendar_raise(self):
        # "myice-" (the default) and "myice-p-" overlap: list_existing_synced_
        # events(cal, "myice-") would also match the "myice-p-" feed's events,
        # so plan_sync would plan to delete them as no-longer-in-the-other-feed.
        a, b = self.full(uid_prefix="myice-"), self.full(uid_prefix="myice-p-")
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([a, b])
        message = str(ctx.exception)
        self.assertIn("0", message)
        self.assertIn("1", message)
        self.assertIn("myice-", message)
        self.assertIn("myice-p-", message)

    def test_overlapping_prefixes_the_other_way_round_also_raise(self):
        # The longer prefix can come first too.
        a, b = self.full(uid_prefix="myice-p-"), self.full(uid_prefix="myice-")
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([a, b])
        self.assertIn("myice-p-", str(ctx.exception))

    def test_overlapping_prefixes_on_different_calendars_is_fine(self):
        lf.validate_configs([
            self.full(calendar_id="one@x", uid_prefix="myice-"),
            self.full(calendar_id="two@x", uid_prefix="myice-p-"),
        ])

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


class ServiceAccountParamDefault(unittest.TestCase):
    """Regression test: the SSM_PARAM_NAME fallback must point at the SSM
    parameter *this* project's deploy.sh actually provisions, not some other
    project's. A mismatch is invisible on Lambda (deploy.sh always sets
    SERVICE_ACCOUNT_PARAM there) but means a local run without the env var
    silently reads the wrong parameter in the same AWS account.
    """

    def test_default_matches_deploy_sh_ssm_param_name(self):
        deploy_sh_text = DEPLOY_SH.read_text()
        match = re.search(
            r'^SSM_PARAM_NAME="([^"]+)"', deploy_sh_text, re.MULTILINE)
        self.assertIsNotNone(
            match, "could not find SSM_PARAM_NAME=\"...\" in deploy.sh")
        self.assertEqual(lf.SSM_PARAM_NAME, match.group(1))

    def test_env_var_still_takes_precedence_over_default(self):
        with mock.patch.dict(
                os.environ, {"SERVICE_ACCOUNT_PARAM": "/some/other/param"}):
            reloaded = importlib.reload(lf)
            try:
                self.assertEqual(reloaded.SSM_PARAM_NAME, "/some/other/param")
            finally:
                importlib.reload(lf)  # restore module state for later tests


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
