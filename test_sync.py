"""Tests for the sync planning logic (create/update/delete/tombstone)."""
import unittest

from calendar_sync import plan_sync


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

    def test_past_event_removed_from_feed_is_left_alone(self):
        """Changed from the iCal original: history is never rewritten."""
        plan = plan_sync(
            feed_uids=set(), feed_bodies={},
            existing={"a": existing("a", day="2001-01-01")},
            state=empty_state(), respect_deletes=False,
        )
        self.assertEqual(plan["delete"], [])


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


if __name__ == "__main__":
    unittest.main()
