"""
Orchestration tests for sync_club() and handler().

Restores the coverage lost when the iCal-era SyncFeedOrchestration and
HandlerStateWiring tests (and their FakeService harness) were removed. The
FakeService shape below is the one that already worked in this repo.

Nothing here touches the network, AWS or Google: myice_client.login,
myice_client.fetch_records, boto3 (for the SSM credential read) and the
calendar service are all stubbed.
"""
import contextlib
import io
import json
import unittest
from unittest import mock

import lambda_function as lf
from test_myice import record


class _FakeRequest:
    def __init__(self, result):
        self._result = result

    def execute(self, num_retries=0):
        return self._result


class _FakeEvents:
    def __init__(self, service):
        self._s = service

    def list(self, **kwargs):
        # Single page of the events we seeded; no pagination.
        return _FakeRequest({"items": self._s.existing_items, "nextPageToken": None})

    def import_(self, calendarId, body):
        self._s.imported.append(body)
        return _FakeRequest({})

    def delete(self, calendarId, eventId):
        self._s.deleted.append(eventId)
        return _FakeRequest({})


class FakeService:
    """Minimal stand-in for a Google Calendar service."""

    def __init__(self, existing_items=None):
        self.existing_items = existing_items or []
        self.imported = []
        self.deleted = []

    def events(self):
        return _FakeEvents(self)


class _ReadOnlyEvents(_FakeEvents):
    def import_(self, calendarId, body):
        raise AssertionError("import_ was called; this must be a read-only run")

    def delete(self, calendarId, eventId):
        raise AssertionError("delete was called; this must be a read-only run")


class ReadOnlyService(FakeService):
    """Reads are fine; any mutating call is a test failure."""

    def events(self):
        return _ReadOnlyEvents(self)


class UnprintableError(Exception):
    """An exception whose __str__ raises - the Finding 2 hazard."""

    def __str__(self):
        raise ValueError("this exception refuses to render")


def club(**overrides):
    cfg = {
        "calendar_id": "c@example.com",
        "uid_prefix": "myice-",
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


def empty_state():
    return {"synced": {}, "tombstones": {}}


def empty_plan():
    return {"create": [], "update": [], "unchanged": [], "delete": [],
            "tombstone": [], "skip_tombstoned": []}


class StubbedMyice(unittest.TestCase):
    """Base: stubs every outbound call made by sync_club."""

    def setUp(self):
        self.fetch_calls = []
        # player_id -> exception to raise instead of returning records.
        self.failures = {}

        ssm = mock.MagicMock()
        ssm.get_parameter.return_value = {
            "Parameter": {"Value": json.dumps({"username": "u", "password": "p"})}
        }
        boto3_stub = mock.MagicMock()
        boto3_stub.client.return_value = ssm
        self.ssm = ssm

        self.login = mock.Mock(return_value="session")
        self.fetch_records = mock.Mock(side_effect=self._fetch)

        for p in (
            mock.patch.object(lf, "boto3", boto3_stub),
            mock.patch.object(lf.myice_client, "login", self.login),
            mock.patch.object(lf.myice_client, "fetch_records", self.fetch_records),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _fetch(self, session, filter_url, **kwargs):
        self.fetch_calls.append(kwargs)
        player = kwargs.get("player_id")
        if player in self.failures:
            raise self.failures[player]
        return [record()]


class SyncClubPastGuard(StubbedMyice):
    """The live-sync past guard must actually reach plan_sync."""

    def _captured_allow_past(self, **sync_kwargs):
        captured = {}

        def fake_plan_sync(feed_uids, feed_bodies, existing, state,
                           respect_deletes, allow_past=False):
            captured["allow_past"] = allow_past
            return empty_plan()

        with mock.patch.object(lf, "plan_sync", fake_plan_sync):
            lf.sync_club(FakeService(), club(), empty_state(), **sync_kwargs)
        return captured["allow_past"]

    def test_allow_past_defaults_to_false(self):
        self.assertIs(self._captured_allow_past(), False)

    def test_allow_past_is_passed_through_when_set(self):
        self.assertIs(self._captured_allow_past(allow_past=True), True)


class SyncClubPlanOnly(StubbedMyice):
    def test_plan_only_makes_no_mutating_google_calls(self):
        service = ReadOnlyService(existing_items=[])
        res = lf.sync_club(service, club(), empty_state(), plan_only=True)
        # It still reports what it *would* do.
        self.assertEqual(res["counts"]["created"], 1)
        self.assertEqual(service.imported, [])
        self.assertEqual(service.deleted, [])

    def test_applying_does_import(self):
        service = FakeService(existing_items=[])
        res = lf.sync_club(service, club(), empty_state())
        self.assertEqual(res["counts"]["created"], 1)
        self.assertEqual(len(service.imported), 1)

    def test_counts_report_feed_and_fetch_totals(self):
        res = lf.sync_club(FakeService(), club(), empty_state(), plan_only=True)
        self.assertEqual(res["counts"]["records_fetched"], 1)
        self.assertEqual(res["counts"]["total_in_feed"], 1)


class HandlerHarness(StubbedMyice):
    """Shared handler plumbing: stubbed calendar service, configs and state I/O."""

    def setUp(self):
        super().setUp()
        self.service = FakeService(existing_items=[])
        self.load = mock.Mock(side_effect=lambda *a, **k: empty_state())
        self.save = mock.Mock()

        for p in (
            mock.patch.object(lf, "get_calendar_service", lambda: self.service),
            mock.patch.object(lf.sync_state, "load", self.load),
            mock.patch.object(lf.sync_state, "save", self.save),
        ):
            p.start()
            self.addCleanup(p.stop)

    def configs(self, *cfgs):
        return mock.patch.object(lf, "PYTHON_CONFIGS", list(cfgs))

    @staticmethod
    def _printed_summary(output: str) -> dict:
        """The last JSON object handler printed."""
        for line in reversed(output.splitlines()):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict) and "overall_success" in payload:
                return payload
        raise AssertionError(f"no summary JSON found in output:\n{output}")

    def _run(self, *cfgs, expect_raise=False):
        buf = io.StringIO()
        with self.configs(*cfgs), contextlib.redirect_stdout(buf):
            if expect_raise:
                with self.assertRaises(RuntimeError) as ctx:
                    lf.handler({}, None)
                self.raised = ctx.exception
                result = None
            else:
                result = lf.handler({}, None)
        return result, self._printed_summary(buf.getvalue())


class HandlerStateWiring(HandlerHarness):
    """State is loaded/saved only when a club opts into respect_manual_deletions."""

    def test_no_state_io_when_no_club_opts_in(self):
        # On Lambda the only persistent store is S3; a stray load/save is a
        # real cost and a real failure mode.
        self._run(club())
        self.load.assert_not_called()
        self.save.assert_not_called()

    def test_state_is_loaded_and_saved_when_a_club_opts_in(self):
        self._run(club(respect_manual_deletions=True))
        self.assertEqual(self.load.call_count, 1)
        self.assertEqual(self.save.call_count, 1)

    def test_state_is_still_saved_when_a_club_fails(self):
        # Otherwise a successful club's tombstones would be lost.
        self.failures = {"1": RuntimeError("myice is down")}
        self._run(club(respect_manual_deletions=True), expect_raise=True)
        self.assertEqual(self.save.call_count, 1)

    def test_successful_run_returns_a_summary(self):
        result, printed = self._run(club())
        self.assertTrue(result["overall_success"])
        self.assertEqual(result["results"][0]["status"], "success")
        self.assertEqual(result["results"][0]["created"], 1)
        self.assertTrue(printed["overall_success"])


class HandlerPerFeedIsolation(HandlerHarness):
    def test_one_failing_club_does_not_stop_the_others(self):
        self.failures = {"1": RuntimeError("myice is down")}
        _result, printed = self._run(
            club(myice_player_id="1", uid_prefix="a-"),
            club(myice_player_id="2", uid_prefix="b-"),
            expect_raise=True,
        )

        self.assertFalse(printed["overall_success"])
        self.assertEqual([r["status"] for r in printed["results"]],
                         ["error", "success"])
        self.assertIn("myice is down", printed["results"][0]["error"])
        # The second club really did sync.
        self.assertEqual(len(self.service.imported), 1)
        # And the run fails loudly, so the CloudWatch alarm fires.
        self.assertIn("failed to sync", str(self.raised))

    def test_both_clubs_are_attempted(self):
        self.failures = {"1": RuntimeError("myice is down")}
        self._run(club(myice_player_id="1", uid_prefix="a-"),
                  club(myice_player_id="2", uid_prefix="b-"),
                  expect_raise=True)
        self.assertEqual([c["player_id"] for c in self.fetch_calls], ["1", "2"])

    def test_an_exception_whose_str_raises_is_still_reported(self):
        self.failures = {"1": UnprintableError()}
        _result, printed = self._run(
            club(myice_player_id="1", uid_prefix="a-"),
            club(myice_player_id="2", uid_prefix="b-"),
            expect_raise=True,
        )
        self.assertEqual([r["status"] for r in printed["results"]],
                         ["error", "success"])
        self.assertIn("UnprintableError", printed["results"][0]["error"])


class DescribeException(unittest.TestCase):
    def test_normal_exception_is_type_and_message(self):
        self.assertEqual(lf.describe_exception(ValueError("nope")),
                         "ValueError: nope")

    def test_exception_whose_str_raises_falls_back_to_repr(self):
        described = lf.describe_exception(UnprintableError())
        self.assertIn("UnprintableError", described)


if __name__ == "__main__":
    unittest.main()
