"""Tests for the sync planning logic (create/update/delete/tombstone)."""
import unittest
from datetime import date

from icalendar import Calendar, Event

import lambda_function
from lambda_function import plan_sync, sync_feed


def body(uid, summary="Event", day="2099-01-01"):
    """A minimal Google event body as produced for the feed."""
    return {
        "iCalUID": uid,
        "summary": summary,
        "start": {"date": day},
        "end": {"date": day},
    }


def existing(uid, summary="Event", day="2099-01-01", event_id=None):
    """A tagged event as returned by the calendar's events.list()."""
    e = body(uid, summary, day)
    e["id"] = event_id or f"gid-{uid}"
    return e


def empty_state():
    return {"synced": {}, "tombstones": {}}


class PlanSyncDefaultMode(unittest.TestCase):
    """respect_deletes=False: today's behavior, state untouched."""

    def test_new_event_is_created(self):
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a")},
            existing={}, state=empty_state(), respect_deletes=False,
        )
        self.assertEqual([u for u, _ in plan["create"]], ["a"])

    def test_changed_event_is_updated(self):
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a", summary="New")},
            existing={"a": existing("a", summary="Old")},
            state=empty_state(), respect_deletes=False,
        )
        self.assertEqual([u for u, _, _ in plan["update"]], ["a"])

    def test_unchanged_event_is_left_alone(self):
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a", summary="Same")},
            existing={"a": existing("a", summary="Same")},
            state=empty_state(), respect_deletes=False,
        )
        self.assertEqual(plan["unchanged"], ["a"])
        self.assertEqual(plan["create"], [])
        self.assertEqual(plan["update"], [])

    def test_event_removed_from_feed_is_deleted(self):
        plan = plan_sync(
            feed_uids=set(), feed_bodies={},
            existing={"a": existing("a")},
            state=empty_state(), respect_deletes=False,
        )
        self.assertEqual([u for u, _ in plan["delete"]], ["a"])

    def test_manually_deleted_event_is_recreated(self):
        # In feed, previously synced, now missing from calendar: default mode
        # treats it as new and recreates it (no state consulted).
        state = {"synced": {"a": {"date": "2099-01-01", "summary": "Event"}}, "tombstones": {}}
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a")},
            existing={}, state=state, respect_deletes=False,
        )
        self.assertEqual([u for u, _ in plan["create"]], ["a"])
        self.assertEqual(plan["tombstone"], [])

    def test_state_is_not_mutated_in_default_mode(self):
        state = empty_state()
        plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a")},
            existing={}, state=state, respect_deletes=False,
        )
        self.assertEqual(state, empty_state())


class PlanSyncRespectMode(unittest.TestCase):
    """respect_deletes=True: manual deletions stick via tombstones."""

    def test_new_event_is_created_and_recorded(self):
        state = empty_state()
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a")},
            existing={}, state=state, respect_deletes=True,
        )
        self.assertEqual([u for u, _ in plan["create"]], ["a"])
        self.assertIn("a", state["synced"])

    def test_previously_synced_now_missing_is_tombstoned_not_recreated(self):
        state = {"synced": {"a": {"date": "2099-01-01", "summary": "Event"}}, "tombstones": {}}
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a")},
            existing={}, state=state, respect_deletes=True,
        )
        self.assertEqual(plan["create"], [])
        self.assertEqual(plan["tombstone"], ["a"])
        self.assertIn("a", state["tombstones"])
        self.assertNotIn("a", state["synced"])

    def test_tombstoned_event_is_skipped(self):
        state = {"synced": {}, "tombstones": {"a": {"date": "2099-01-01", "summary": "Event"}}}
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a")},
            existing={}, state=state, respect_deletes=True,
        )
        self.assertEqual(plan["create"], [])
        self.assertEqual(plan["skip_tombstoned"], ["a"])
        self.assertIn("a", state["tombstones"])

    def test_reappearing_on_calendar_clears_tombstone(self):
        # User un-deleted (event back on calendar) -> honor it, drop tombstone.
        state = {"synced": {}, "tombstones": {"a": {"date": "2099-01-01", "summary": "Event"}}}
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a", summary="Same")},
            existing={"a": existing("a", summary="Same")},
            state=state, respect_deletes=True,
        )
        self.assertEqual(plan["unchanged"], ["a"])
        self.assertNotIn("a", state["tombstones"])
        self.assertIn("a", state["synced"])

    def test_feed_removal_deletes_and_drops_state(self):
        state = {
            "synced": {"a": {"date": "2099-01-01", "summary": "Event"}},
            "tombstones": {"b": {"date": "2099-01-01", "summary": "Old"}},
        }
        plan = plan_sync(
            feed_uids=set(), feed_bodies={},
            existing={"a": existing("a")},
            state=state, respect_deletes=True,
        )
        self.assertEqual([u for u, _ in plan["delete"]], ["a"])
        self.assertNotIn("a", state["synced"])
        # A tombstone whose event is no longer in the feed is no longer relevant.
        self.assertNotIn("b", state["tombstones"])


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


def make_ical(events):
    """events: list of (uid, summary, iso_date) -> a parsed Calendar."""
    cal = Calendar()
    for uid, summary, iso in events:
        ev = Event()
        ev.add("uid", uid)
        ev.add("summary", summary)
        ev.add("dtstart", date.fromisoformat(iso))
        ev.add("dtend", date.fromisoformat(iso))
        cal.add_component(ev)
    return cal


class SyncFeedOrchestration(unittest.TestCase):
    def setUp(self):
        self._orig_fetch = lambda_function.fetch_ical

    def tearDown(self):
        lambda_function.fetch_ical = self._orig_fetch

    def _feed(self, events):
        lambda_function.fetch_ical = lambda url: make_ical(events)

    def test_new_event_is_imported_default_mode(self):
        self._feed([("evt1", "Practice", "2099-01-01")])
        service = FakeService(existing_items=[])
        res = sync_feed(service, "u", "cal", "t-", "{summary}", None)
        self.assertEqual(res["created"], 1)
        self.assertEqual(len(service.imported), 1)

    def test_respect_mode_records_synced_state(self):
        self._feed([("evt1", "Practice", "2099-01-01")])
        service = FakeService(existing_items=[])
        state = {"synced": {}, "tombstones": {}}
        res = sync_feed(service, "u", "cal", "t-", "{summary}", None,
                        respect_deletes=True, state=state)
        self.assertEqual(res["created"], 1)
        self.assertIn("t-evt1", state["synced"])

    def test_respect_mode_tombstones_manual_deletion(self):
        # Previously synced, still in feed, missing from calendar -> don't recreate.
        self._feed([("evt1", "Practice", "2099-01-01")])
        service = FakeService(existing_items=[])
        state = {"synced": {"t-evt1": {"date": "2099-01-01", "summary": "Practice"}},
                 "tombstones": {}}
        res = sync_feed(service, "u", "cal", "t-", "{summary}", None,
                        respect_deletes=True, state=state)
        self.assertEqual(res["created"], 0)
        self.assertEqual(service.imported, [])
        self.assertIn("t-evt1", state["tombstones"])


class HandlerStateWiring(unittest.TestCase):
    """State is loaded/saved only when a feed opts into respect_manual_deletions."""

    def setUp(self):
        self._orig = {
            "fetch": lambda_function.fetch_ical,
            "svc": lambda_function.get_calendar_service,
            "cfg": lambda_function.PYTHON_CONFIGS,
            "load": lambda_function.sync_state.load,
            "save": lambda_function.sync_state.save,
        }
        lambda_function.fetch_ical = lambda url: make_ical([])
        lambda_function.get_calendar_service = lambda: FakeService(existing_items=[])
        self.saved = []
        self.loaded = []
        lambda_function.sync_state.load = lambda *a, **k: (
            self.loaded.append(True) or {"synced": {}, "tombstones": {}}
        )
        lambda_function.sync_state.save = lambda *a, **k: self.saved.append((a, k))

    def tearDown(self):
        lambda_function.fetch_ical = self._orig["fetch"]
        lambda_function.get_calendar_service = self._orig["svc"]
        lambda_function.PYTHON_CONFIGS = self._orig["cfg"]
        lambda_function.sync_state.load = self._orig["load"]
        lambda_function.sync_state.save = self._orig["save"]

    def test_no_state_io_when_no_feed_opts_in(self):
        lambda_function.PYTHON_CONFIGS = [
            {"ical_url": "u", "calendar_id": "c", "uid_prefix": "t-"},
        ]
        lambda_function.handler({}, None)
        self.assertEqual(self.loaded, [])
        self.assertEqual(self.saved, [])

    def test_state_saved_when_a_feed_opts_in(self):
        lambda_function.PYTHON_CONFIGS = [
            {"ical_url": "u", "calendar_id": "c", "uid_prefix": "t-",
             "respect_manual_deletions": True},
        ]
        lambda_function.handler({}, None)
        self.assertEqual(self.loaded, [True])
        self.assertEqual(len(self.saved), 1)


if __name__ == "__main__":
    unittest.main()
