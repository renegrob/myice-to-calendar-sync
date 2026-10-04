"""
Tests for the run_local.py CLI (the dry-run driver).

Report *rendering* is covered in test_dry_run.py; this module covers argument
handling and the dry-run wiring: what sync_club actually receives, that a dry run
never writes, and how failures are reported.

Nothing here touches the network, AWS or the real filesystem: the myice/Google/
state boundaries are patched, and every report is written inside a temp dir.
"""

import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

import lambda_function as lf
import run_local
import sync_state


def config(**overrides) -> dict:
    """A club entry that passes the real lf.validate_configs."""
    cfg = {
        "calendar_id": "games@example.com",
        "uid_prefix": "myice-g-",
        "myice_login_url": "https://example.invalid/login",
        "myice_username_field": "user",
        "myice_password_field": "pass",
        "myice_credentials_param": "/myice-sync/myice-credentials",
        "myice_filter_url": "https://example.invalid/playersfilter",
        "myice_player_id": "42",
        "myice_event_type": "g",
        "myice_season": "2026",
        "myice_club": "3",
        "myice_min_date": "2026-01-01",
        "myice_max_date": "2026-12-31",
    }
    cfg.update(overrides)
    return cfg


def trainings_config(**overrides) -> dict:
    """A second, distinct club entry (own calendar_id/uid_prefix) of type 'p'."""
    return config(calendar_id="trainings@example.com", uid_prefix="myice-p-",
                  myice_event_type="p", **overrides)


def result() -> dict:
    """What a plan_only sync_club returns."""
    return {
        "plan": {"create": [], "update": [], "delete": [],
                 "unchanged": [], "tombstone": [], "skip_tombstoned": []},
        "existing": {},
        "counts": {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0,
                   "tombstoned": 0, "skipped_tombstoned": 0, "total_in_feed": 0},
    }


@contextlib.contextmanager
def patched(configs, side_effect=None):
    """Patch every boundary run_local touches: myice/Google, and the state store."""
    with mock.patch.object(lf, "load_configs", return_value=configs), \
         mock.patch.object(lf, "get_calendar_service",
                           return_value=mock.sentinel.service), \
         mock.patch.object(lf, "sync_club") as sync_club, \
         mock.patch.object(sync_state, "load",
                           return_value={"synced": {}, "tombstones": {}}) as load, \
         mock.patch.object(sync_state, "save") as save:
        sync_club.side_effect = (side_effect if side_effect is not None
                                else lambda *a, **k: result())
        yield types.SimpleNamespace(sync_club=sync_club, load=load, save=save)


def run(argv):
    """Invoke main() with argv, capturing output. Returns (exit_code, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", ["run_local.py", *argv]), \
         contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            run_local.main()
            code = 0
        except SystemExit as exc:
            code = 0 if exc.code is None else exc.code
    return code, out.getvalue(), err.getvalue()


class SinceValidation(unittest.TestCase):
    """--since must never reach a live sync, and must be a real date."""

    def test_since_with_apply_is_refused_non_zero(self):
        with patched([config()]) as mocks:
            code, _out, err = run(["--since", "2026-01-01", "--apply"])
        self.assertEqual(code, 2)  # argparse parser.error
        self.assertIn("--since is dry-run only", err)
        mocks.sync_club.assert_not_called()

    def test_malformed_since_is_refused_non_zero(self):
        for bad in ("2026-13-45", "not-a-date", "01-01-2026", "2026-1"):
            with self.subTest(since=bad):
                with patched([config()]):
                    code, _out, err = run(["--since", bad])
                self.assertEqual(code, 2)
                self.assertIn("--since must be YYYY-MM-DD", err)


class DryRunWiring(unittest.TestCase):
    """What sync_club actually receives, and what the dry run must never do."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out_path = os.path.join(tmp.name, "report.txt")

    def test_since_injects_min_date_override_and_allows_past(self):
        with patched([config()]) as mocks:
            code, _out, _err = run(["--since", "2026-02-01", "--out", self.out_path])
        self.assertEqual(code, 0)
        call = mocks.sync_club.call_args
        self.assertEqual(call.args[1]["_min_date_override"], "2026-02-01")
        self.assertIs(call.kwargs["allow_past"], True)

    def test_without_since_no_override_and_past_stays_excluded(self):
        with patched([config()]) as mocks:
            code, _out, _err = run(["--out", self.out_path])
        self.assertEqual(code, 0)
        call = mocks.sync_club.call_args
        self.assertNotIn("_min_date_override", call.args[1])
        self.assertIs(call.kwargs["allow_past"], False)

    def test_dry_run_plans_only_and_never_saves_state(self):
        with patched([config()]) as mocks:
            code, _out, _err = run(["--out", self.out_path])
        self.assertEqual(code, 0)
        self.assertIs(mocks.sync_club.call_args.kwargs["plan_only"], True)
        mocks.save.assert_not_called()

    def test_out_writes_the_report_to_the_given_path(self):
        with patched([config()]):
            code, out, _err = run(["--out", self.out_path])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(self.out_path))
        with open(self.out_path, encoding="utf-8") as fh:
            written = fh.read()
        self.assertIn("games@example.com", written)
        self.assertIn("DRY RUN", written)
        self.assertIn(self.out_path, out)

    def test_report_carries_the_existing_event_for_the_update_diff(self):
        # sync_club's "existing" must reach dry_run.render_report via the
        # section dict, or the field-level diff for updates has no data to
        # render from (see test_dry_run.UpdateFieldDiff for the rendering).
        updated = result()
        updated["plan"]["update"] = [("myice-2", {"summary": "New"}, "gid-2")]
        updated["existing"] = {"myice-2": {"summary": "Old"}}
        with patched([config()], side_effect=[updated]):
            code, _out, _err = run(["--out", self.out_path])
        self.assertEqual(code, 0)
        with open(self.out_path, encoding="utf-8") as fh:
            written = fh.read()
        self.assertIn("Old", written)
        self.assertIn("New", written)


class ConfigFilters(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out_path = os.path.join(tmp.name, "report.txt")

    def synced_event_types(self, argv):
        with patched([config(), trainings_config()]) as mocks:
            code, _out, _err = run([*argv, "--out", self.out_path])
        self.assertEqual(code, 0)
        return [c.args[1]["myice_event_type"] for c in mocks.sync_club.call_args_list]

    def test_games_only_syncs_only_game_feeds(self):
        self.assertEqual(self.synced_event_types(["--games-only"]), ["g"])

    def test_trainings_only_syncs_only_training_feeds(self):
        self.assertEqual(self.synced_event_types(["--trainings-only"]), ["p"])

    def test_no_filter_syncs_every_feed(self):
        self.assertEqual(self.synced_event_types([]), ["g", "p"])

    def test_games_only_and_trainings_only_are_mutually_exclusive(self):
        with patched([config()]):
            code, _out, err = run(["--games-only", "--trainings-only"])
        self.assertEqual(code, 2)
        self.assertIn("not allowed with argument", err)


class ApplyModeFilters(unittest.TestCase):
    """
    --apply must honor --games-only/--trainings-only too. It runs through
    lf.handler() rather than the dry-run loop in run_local.py itself, so the
    filter has to be threaded all the way through handler's own `only` kwarg.
    """

    def synced_event_types(self, argv):
        with patched([config(), trainings_config()]) as mocks:
            code, _out, _err = run([*argv, "--apply"])
        self.assertEqual(code, 0)
        return [c.args[1]["myice_event_type"] for c in mocks.sync_club.call_args_list]

    def test_games_only_applies_only_game_feeds(self):
        self.assertEqual(self.synced_event_types(["--games-only"]), ["g"])

    def test_trainings_only_applies_only_training_feeds(self):
        self.assertEqual(self.synced_event_types(["--trainings-only"]), ["p"])

    def test_no_filter_applies_every_feed(self):
        self.assertEqual(self.synced_event_types([]), ["g", "p"])


class FailureHandling(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmpdir = tmp.name
        self.out_path = os.path.join(self.tmpdir, "report.txt")

    def test_one_failing_club_still_reports_the_other_and_exits_non_zero(self):
        side_effect = [RuntimeError("myice login failed"), result()]
        with patched([config(), trainings_config()], side_effect=side_effect):
            code, out, err = run(["--out", self.out_path])

        self.assertEqual(code, 1)
        self.assertIn("FAILED", err)
        self.assertIn("myice login failed", err)
        # The surviving club is still reported, on stdout and in the file.
        self.assertTrue(os.path.isfile(self.out_path))
        with open(self.out_path, encoding="utf-8") as fh:
            written = fh.read()
        self.assertIn("trainings@example.com", written)
        self.assertNotIn("games@example.com", written)
        self.assertIn("trainings@example.com", out)

    def test_an_unwritable_out_path_still_prints_the_report_and_exits_non_zero(self):
        """A typo in --out must not discard a report that cost real fetches."""
        bad = os.path.join(self.tmpdir, "no-such-dir", "report.txt")
        with patched([config()]):
            code, out, err = run(["--out", bad])

        self.assertEqual(code, 1)
        self.assertIn("games@example.com", out)  # report survived on stdout
        self.assertIn("could not write the report", err)
        self.assertIn(bad, err)  # the offending path is named
        self.assertFalse(os.path.exists(bad))


class NoMatchingConfigs(unittest.TestCase):
    def test_empty_selection_reports_and_does_not_sync_or_write(self):
        with patched([config()]) as mocks:
            code, out, _err = run(["--trainings-only"])
        self.assertEqual(code, 0)
        self.assertIn("No club feeds match that filter.", out)
        mocks.sync_club.assert_not_called()
        mocks.save.assert_not_called()


class ClubSelector(unittest.TestCase):
    """--club narrows to one club's feeds, in every mode."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out_path = os.path.join(tmp.name, "report.txt")
        self.configs = [config(myice_club="113"),
                        trainings_config(myice_club="113"),
                        config(calendar_id="sihf@example.com",
                               uid_prefix="myice-s-g-", myice_club="7")]

    def synced_clubs(self, argv):
        with patched(self.configs) as mocks:
            code, _out, err = run(argv)
        self.assertEqual(code, 0, err)
        return [c.args[1]["myice_club"] for c in mocks.sync_club.call_args_list]

    def test_club_narrows_a_dry_run(self):
        self.assertEqual(
            self.synced_clubs(["--club", "113", "--out", self.out_path]),
            ["113", "113"])

    def test_club_narrows_an_apply(self):
        # --apply goes through lf.handler(), so the filter must be threaded
        # through handler's own kwarg rather than run_local's loop.
        self.assertEqual(self.synced_clubs(["--club", "113", "--apply"]),
                         ["113", "113"])

    def test_club_plus_type_isolates_a_single_feed(self):
        self.assertEqual(
            self.synced_clubs(["--club", "113", "--trainings-only",
                               "--out", self.out_path]),
            ["113"])

    def test_no_club_syncs_every_feed(self):
        self.assertEqual(self.synced_clubs(["--out", self.out_path]),
                         ["113", "113", "7"])

    def test_an_unknown_club_is_an_error_not_a_silent_no_op(self):
        # Exiting 0 having done nothing reads as success - dangerous for
        # --apply and --purge, where a typo would look like a clean run.
        with patched(self.configs) as mocks:
            code, _out, err = run(["--club", "999", "--out", self.out_path])
        self.assertEqual(code, 2)
        self.assertIn("999", err)
        self.assertIn("113", err)  # lists what is actually configured
        mocks.sync_club.assert_not_called()


@contextlib.contextmanager
def patched_purge(configs):
    """Patch the config source and purge_feed; nothing reaches Google."""
    with mock.patch.object(lf, "load_configs", return_value=configs), \
         mock.patch.object(lf, "get_calendar_service",
                           return_value=mock.sentinel.service), \
         mock.patch.object(run_local.lf, "purge_feed") as purge:
        purge.return_value = {"matched": 3, "would_delete": 3, "deleted": 0}
        yield purge


class PurgeMode(unittest.TestCase):
    """--purge is the only CLI path that deletes without regard to the feed,
    so every guard on it is load-bearing."""

    def setUp(self):
        self.configs = [config(myice_club="113"),
                        trainings_config(myice_club="113"),
                        config(calendar_id="sihf@example.com",
                               uid_prefix="myice-s-g-", myice_club="7")]

    def test_bare_purge_refuses_without_a_target(self):
        with patched_purge(self.configs) as purge:
            code, _out, err = run(["--purge"])
        self.assertEqual(code, 2)
        self.assertIn("--all-feeds", err)
        purge.assert_not_called()

    def test_all_feeds_purges_every_configured_feed(self):
        with patched_purge(self.configs) as purge:
            code, _out, err = run(["--purge", "--all-feeds"])
        self.assertEqual(code, 0, err)
        self.assertEqual(purge.call_count, 3)

    def test_club_narrows_the_purge(self):
        with patched_purge(self.configs) as purge:
            code, _out, err = run(["--purge", "--club", "113"])
        self.assertEqual(code, 0, err)
        self.assertEqual(purge.call_count, 2)

    def test_purge_targets_come_from_the_config_never_typed_by_hand(self):
        with patched_purge(self.configs) as purge:
            run(["--purge", "--club", "7"])
        kwargs = purge.call_args.kwargs
        args = purge.call_args.args
        self.assertIn("sihf@example.com", args)
        self.assertIn("myice-s-g-", args)
        self.assertEqual(kwargs["scope"], "future")

    def test_purge_defaults_to_future_scope(self):
        with patched_purge(self.configs) as purge:
            code, _out, err = run(["--purge", "--all-feeds"])
        self.assertEqual(code, 0, err)
        self.assertEqual(purge.call_count, 3)  # else the loop below is vacuous
        for call in purge.call_args_list:
            self.assertEqual(call.kwargs["scope"], "future")

    def test_purge_scope_all_is_opt_in(self):
        with patched_purge(self.configs) as purge:
            code, _out, err = run(["--purge", "--all-feeds",
                                   "--purge-scope", "all"])
        self.assertEqual(code, 0, err)
        self.assertEqual(purge.call_count, 3)
        for call in purge.call_args_list:
            self.assertEqual(call.kwargs["scope"], "all")

    def test_purge_without_confirm_is_a_dry_run(self):
        with patched_purge(self.configs) as purge:
            code, out, _err = run(["--purge", "--all-feeds"])
        self.assertEqual(code, 0)
        for call in purge.call_args_list:
            self.assertIs(call.kwargs["dry_run"], True)
        self.assertIn("confirm", out.lower())

    def test_confirm_actually_deletes(self):
        with patched_purge(self.configs) as purge:
            code, _out, err = run(["--purge", "--all-feeds", "--confirm"])
        self.assertEqual(code, 0, err)
        for call in purge.call_args_list:
            self.assertIs(call.kwargs["dry_run"], False)

    def test_purge_is_mutually_exclusive_with_apply(self):
        with patched_purge(self.configs):
            code, _out, err = run(["--purge", "--apply"])
        self.assertEqual(code, 2)
        self.assertIn("not allowed with argument", err)


class PurgeOnlyFlags(unittest.TestCase):
    """Flags that only mean something for one mode are refused elsewhere,
    following the --since precedent: a silently ignored flag is worse than
    an error."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out_path = os.path.join(tmp.name, "report.txt")

    def assert_refused(self, argv, needle):
        """Refused with OUR message, not argparse's 'unrecognized arguments'
        - which would pass before the flag even exists and prove nothing."""
        with patched([config()]) as mocks:
            code, _out, err = run(argv)
        self.assertEqual(code, 2, err)
        self.assertNotIn("unrecognized arguments", err)
        self.assertIn(needle, err)
        mocks.sync_club.assert_not_called()

    def test_confirm_outside_purge_is_refused(self):
        self.assert_refused(["--confirm", "--out", self.out_path],
                            "--confirm is only valid with --purge")

    def test_all_feeds_outside_purge_is_refused(self):
        self.assert_refused(["--all-feeds", "--out", self.out_path],
                            "--all-feeds is only valid with --purge")

    def test_purge_scope_outside_purge_is_refused(self):
        self.assert_refused(["--purge-scope", "all", "--out", self.out_path],
                            "--purge-scope is only valid with --purge")

    def test_verbose_with_purge_is_refused(self):
        self.assert_refused(["--purge", "--all-feeds", "--verbose"],
                            "--verbose is dry-run only")

    def test_since_with_purge_is_refused(self):
        self.assert_refused(["--purge", "--all-feeds", "--since", "2026-01-01"],
                            "--since is dry-run only")


class VerboseFlag(unittest.TestCase):
    """--verbose only affects the report, so it must not pretend to work with
    --apply, which never renders one."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out_path = os.path.join(tmp.name, "report.txt")

    def test_verbose_reaches_the_report(self):
        with patched([config()]), \
             mock.patch.object(run_local.dry_run, "render_report",
                               return_value="") as render:
            code, _out, _err = run(["--verbose", "--out", self.out_path])
        self.assertEqual(code, 0)
        self.assertIs(render.call_args.kwargs["verbose"], True)

    def test_without_verbose_the_report_stays_terse(self):
        with patched([config()]), \
             mock.patch.object(run_local.dry_run, "render_report",
                               return_value="") as render:
            code, _out, _err = run(["--out", self.out_path])
        self.assertEqual(code, 0)
        self.assertIs(render.call_args.kwargs["verbose"], False)

    def test_verbose_with_apply_is_refused_non_zero(self):
        with patched([config()]) as mocks:
            code, _out, err = run(["--verbose", "--apply"])
        self.assertEqual(code, 2)  # argparse parser.error
        self.assertIn("--verbose is dry-run only", err)
        mocks.sync_club.assert_not_called()


if __name__ == "__main__":
    unittest.main()
