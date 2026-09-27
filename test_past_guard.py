"""A live sync never touches an event that has already ended."""
import unittest

from calendar_sync import plan_sync

PAST = "2001-01-01"
FUTURE = "2099-01-01"


def body(uid, day):
    return {"iCalUID": uid, "summary": "Event",
            "start": {"date": day}, "end": {"date": day}}


def existing(uid, day):
    e = body(uid, day)
    e["id"] = f"gid-{uid}"
    return e


def empty_state():
    return {"synced": {}, "tombstones": {}}


class LiveSyncIgnoresThePast(unittest.TestCase):
    def test_past_event_is_not_created(self):
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", PAST)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, allow_past=False)
        self.assertEqual(plan["create"], [])

    def test_past_event_is_not_updated(self):
        changed = body("a", PAST)
        changed["summary"] = "Renamed"
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": changed},
                         existing={"a": existing("a", PAST)}, state=empty_state(),
                         respect_deletes=False, allow_past=False)
        self.assertEqual(plan["update"], [])

    def test_past_event_removed_from_feed_is_not_deleted(self):
        plan = plan_sync(feed_uids=set(), feed_bodies={},
                         existing={"a": existing("a", PAST)}, state=empty_state(),
                         respect_deletes=False, allow_past=False)
        self.assertEqual(plan["delete"], [])

    def test_future_event_removed_from_feed_is_still_deleted(self):
        plan = plan_sync(feed_uids=set(), feed_bodies={},
                         existing={"a": existing("a", FUTURE)}, state=empty_state(),
                         respect_deletes=False, allow_past=False)
        self.assertEqual([uid for uid, _ in plan["delete"]], ["a"])

    def test_future_events_are_unaffected_by_the_guard(self):
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", FUTURE)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, allow_past=False)
        self.assertEqual([uid for uid, _ in plan["create"]], ["a"])


class DryRunMayReachIntoThePast(unittest.TestCase):
    def test_past_event_is_planned_when_allowed(self):
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", PAST)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, allow_past=True)
        self.assertEqual([uid for uid, _ in plan["create"]], ["a"])

    def test_past_deletion_is_planned_when_allowed(self):
        plan = plan_sync(feed_uids=set(), feed_bodies={},
                         existing={"a": existing("a", PAST)}, state=empty_state(),
                         respect_deletes=False, allow_past=True)
        self.assertEqual([uid for uid, _ in plan["delete"]], ["a"])


class GuardWithRespectDeletes(unittest.TestCase):
    def test_past_event_is_not_tombstoned(self):
        state = {"synced": {"a": {"date": PAST, "summary": "Event"}}, "tombstones": {}}
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", PAST)},
                         existing={}, state=state,
                         respect_deletes=True, allow_past=False)
        self.assertEqual(plan["tombstone"], [])

    def test_future_event_is_still_tombstoned(self):
        state = {"synced": {"a": {"date": FUTURE, "summary": "Event"}}, "tombstones": {}}
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", FUTURE)},
                         existing={}, state=state,
                         respect_deletes=True, allow_past=False)
        self.assertEqual(plan["tombstone"], ["a"])


if __name__ == "__main__":
    unittest.main()
