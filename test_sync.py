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
            existing={}, state=empty_state(), respect_deletes=False, uid_prefix="myice-",
        )
        self.assertEqual([u for u, _ in plan["create"]], ["a"])

    def test_changed_event_is_updated(self):
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a", summary="New")},
            existing={"a": existing("a", summary="Old")},
            state=empty_state(), respect_deletes=False, uid_prefix="myice-",
        )
        self.assertEqual([u for u, _, _ in plan["update"]], ["a"])

    def test_unchanged_event_is_left_alone(self):
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a", summary="Same")},
            existing={"a": existing("a", summary="Same")},
            state=empty_state(), respect_deletes=False, uid_prefix="myice-",
        )
        self.assertEqual(plan["unchanged"], ["a"])
        self.assertEqual(plan["create"], [])
        self.assertEqual(plan["update"], [])

    def test_event_removed_from_feed_is_deleted(self):
        plan = plan_sync(
            feed_uids=set(), feed_bodies={},
            existing={"a": existing("a")},
            state=empty_state(), respect_deletes=False, uid_prefix="myice-",
        )
        self.assertEqual([u for u, _ in plan["delete"]], ["a"])

    def test_manually_deleted_event_is_recreated(self):
        # In feed, previously synced, now missing from calendar: default mode
        # treats it as new and recreates it (no state consulted).
        state = {"synced": {"a": {"date": "2099-01-01", "summary": "Event"}}, "tombstones": {}}
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a")},
            existing={}, state=state, respect_deletes=False, uid_prefix="myice-",
        )
        self.assertEqual([u for u, _ in plan["create"]], ["a"])
        self.assertEqual(plan["tombstone"], [])

    def test_state_is_not_mutated_in_default_mode(self):
        state = empty_state()
        plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a")},
            existing={}, state=state, respect_deletes=False, uid_prefix="myice-",
        )
        self.assertEqual(state, empty_state())

    def test_past_event_removed_from_feed_is_left_alone(self):
        """Changed from the iCal original: history is never rewritten."""
        plan = plan_sync(
            feed_uids=set(), feed_bodies={},
            existing={"a": existing("a", day="2001-01-01")},
            state=empty_state(), respect_deletes=False, uid_prefix="myice-",
        )
        self.assertEqual(plan["delete"], [])


class PlanSyncRespectMode(unittest.TestCase):
    """respect_deletes=True: manual deletions stick via tombstones."""

    def test_new_event_is_created_and_recorded(self):
        state = empty_state()
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a")},
            existing={}, state=state, respect_deletes=True, uid_prefix="myice-",
        )
        self.assertEqual([u for u, _ in plan["create"]], ["a"])
        self.assertIn("a", state["synced"])

    def test_previously_synced_now_missing_is_tombstoned_not_recreated(self):
        state = {"synced": {"a": {"date": "2099-01-01", "summary": "Event"}}, "tombstones": {}}
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a")},
            existing={}, state=state, respect_deletes=True, uid_prefix="myice-",
        )
        self.assertEqual(plan["create"], [])
        self.assertEqual(plan["tombstone"], ["a"])
        self.assertIn("a", state["tombstones"])
        self.assertNotIn("a", state["synced"])

    def test_tombstoned_event_is_skipped(self):
        state = {"synced": {}, "tombstones": {"a": {"date": "2099-01-01", "summary": "Event"}}}
        plan = plan_sync(
            feed_uids={"a"}, feed_bodies={"a": body("a")},
            existing={}, state=state, respect_deletes=True, uid_prefix="myice-",
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
            state=state, respect_deletes=True, uid_prefix="myice-",
        )
        self.assertEqual(plan["unchanged"], ["a"])
        self.assertNotIn("a", state["tombstones"])
        self.assertIn("a", state["synced"])

    def test_feed_removal_deletes_and_drops_state(self):
        state = {
            "synced": {"a": {"date": "2099-01-01", "summary": "Event"}},
            "tombstones": {"myice-b": {"date": "2099-01-01", "summary": "Old"}},
        }
        plan = plan_sync(
            feed_uids=set(), feed_bodies={},
            existing={"a": existing("a")},
            state=state, respect_deletes=True, uid_prefix="myice-",
        )
        self.assertEqual([u for u, _ in plan["delete"]], ["a"])
        self.assertNotIn("a", state["synced"])
        # A tombstone whose event is no longer in the feed is no longer relevant,
        # but only within this feed's own prefix - see
        # PlanSyncCrossFeedTombstoneIsolation for the cross-feed guarantee.
        self.assertNotIn("myice-b", state["tombstones"])


class PlanSyncCrossFeedTombstoneIsolation(unittest.TestCase):
    """
    handler() builds one `state` dict and passes it to plan_sync for every club
    feed in the run. The stale-tombstone cleanup at the end of plan_sync must
    only ever touch tombstones belonging to *this* feed (matched by uid_prefix):
    otherwise feed A's run wipes feed B's tombstones outright (a tombstone
    belonging to another feed is never "in feed A's feed_uids" either), and a
    hand-deleted event in feed B gets silently recreated on the next feed B run.
    """

    def test_feed_a_does_not_wipe_feed_bs_tombstone(self):
        state = {"synced": {}, "tombstones": {}}

        # Run 1, feed B: event "myice-b-9" was synced before, then manually
        # deleted from the calendar -> plan_sync tombstones it.
        state["synced"]["myice-b-9"] = {"date": "2099-01-01", "summary": "B event"}
        plan_b1 = plan_sync(
            feed_uids={"myice-b-9"}, feed_bodies={"myice-b-9": body("myice-b-9")},
            existing={}, state=state, respect_deletes=True, uid_prefix="myice-b-",
        )
        self.assertEqual(plan_b1["tombstone"], ["myice-b-9"])
        self.assertEqual(list(state["tombstones"]), ["myice-b-9"])

        # Run 2, feed A: an unrelated feed sharing the same state dict. It has
        # nothing to do with "myice-b-9" at all - not in its feed, not in its
        # existing calendar events - but its own cleanup pass must not touch
        # another feed's tombstone.
        plan_a = plan_sync(
            feed_uids=set(), feed_bodies={},
            existing={}, state=state, respect_deletes=True, uid_prefix="myice-a-",
        )
        self.assertEqual(plan_a["delete"], [])
        self.assertIn("myice-b-9", state["tombstones"],
                       "feed A's cleanup pass wiped feed B's tombstone")

        # Run 3, feed B again: the event is still in myice's feed (the user's
        # calendar deletion doesn't change myice's data) but must stay
        # tombstoned, not be resurrected.
        plan_b2 = plan_sync(
            feed_uids={"myice-b-9"}, feed_bodies={"myice-b-9": body("myice-b-9")},
            existing={}, state=state, respect_deletes=True, uid_prefix="myice-b-",
        )
        self.assertEqual(plan_b2["create"], [],
                         "hand-deleted event was resurrected after an unrelated feed's run")
        self.assertEqual(plan_b2["skip_tombstoned"], ["myice-b-9"])


if __name__ == "__main__":
    unittest.main()
