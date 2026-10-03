"""A live sync never touches an event that has already ended."""
import unittest
from datetime import datetime, timedelta, timezone

from calendar_sync import body_has_ended, plan_sync

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
                         respect_deletes=False, uid_prefix="myice-", allow_past=False)
        self.assertEqual(plan["create"], [])

    def test_past_event_is_not_updated(self):
        changed = body("a", PAST)
        changed["summary"] = "Renamed"
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": changed},
                         existing={"a": existing("a", PAST)}, state=empty_state(),
                         respect_deletes=False, uid_prefix="myice-", allow_past=False)
        self.assertEqual(plan["update"], [])

    def test_past_event_removed_from_feed_is_not_deleted(self):
        plan = plan_sync(feed_uids=set(), feed_bodies={},
                         existing={"a": existing("a", PAST)}, state=empty_state(),
                         respect_deletes=False, uid_prefix="myice-", allow_past=False)
        self.assertEqual(plan["delete"], [])

    def test_future_event_removed_from_feed_is_still_deleted(self):
        plan = plan_sync(feed_uids=set(), feed_bodies={},
                         existing={"a": existing("a", FUTURE)}, state=empty_state(),
                         respect_deletes=False, uid_prefix="myice-", allow_past=False)
        self.assertEqual([uid for uid, _ in plan["delete"]], ["a"])

    def test_future_events_are_unaffected_by_the_guard(self):
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", FUTURE)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, uid_prefix="myice-", allow_past=False)
        self.assertEqual([uid for uid, _ in plan["create"]], ["a"])


class DryRunMayReachIntoThePast(unittest.TestCase):
    def test_past_event_is_planned_when_allowed(self):
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", PAST)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, uid_prefix="myice-", allow_past=True)
        self.assertEqual([uid for uid, _ in plan["create"]], ["a"])

    def test_past_deletion_is_planned_when_allowed(self):
        plan = plan_sync(feed_uids=set(), feed_bodies={},
                         existing={"a": existing("a", PAST)}, state=empty_state(),
                         respect_deletes=False, uid_prefix="myice-", allow_past=True)
        self.assertEqual([uid for uid, _ in plan["delete"]], ["a"])


class GuardWithRespectDeletes(unittest.TestCase):
    def test_past_event_is_not_tombstoned(self):
        state = {"synced": {"a": {"date": PAST, "summary": "Event"}}, "tombstones": {}}
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", PAST)},
                         existing={}, state=state,
                         respect_deletes=True, uid_prefix="myice-", allow_past=False)
        self.assertEqual(plan["tombstone"], [])

    def test_future_event_is_still_tombstoned(self):
        state = {"synced": {"a": {"date": FUTURE, "summary": "Event"}}, "tombstones": {}}
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", FUTURE)},
                         existing={}, state=state,
                         respect_deletes=True, uid_prefix="myice-", allow_past=False)
        self.assertEqual(plan["tombstone"], ["a"])


def all_day(end_day):
    """An all-day body using Google's real, exclusive end.date."""
    return {"end": {"date": end_day.isoformat()}}


class AllDayEndDateIsExclusive(unittest.TestCase):
    """
    Google's end.date on an all-day event is the day AFTER the last day it
    occupies, so the event has ended once that date has arrived.

    `now` is passed explicitly so these pin the boundary deterministically
    rather than depending on when the suite happens to run.
    """

    NOW = datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc)

    def test_event_that_ended_yesterday_has_ended(self):
        # Occupied the 14th -> end.date is the 15th, which is today.
        today = self.NOW.date()
        self.assertTrue(body_has_ended(all_day(today), now=self.NOW))

    def test_event_occupying_today_has_not_ended(self):
        # Occupies the 15th -> end.date is the 16th, which is tomorrow.
        tomorrow = self.NOW.date() + timedelta(days=1)
        self.assertFalse(body_has_ended(all_day(tomorrow), now=self.NOW))

    def test_event_that_ended_two_days_ago_has_ended(self):
        # Occupied the 13th -> end.date is the 14th, which is yesterday.
        yesterday = self.NOW.date() - timedelta(days=1)
        self.assertTrue(body_has_ended(all_day(yesterday), now=self.NOW))


class MixedTimezoneAwareness(unittest.TestCase):
    """Comparing a naive body time to an aware `now` must not raise."""

    AWARE_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    NAIVE_NOW = datetime(2026, 3, 15, 12, 0)

    def test_aware_now_with_naive_past_body_time(self):
        body = {"end": {"dateTime": "2026-03-15T10:00:00"}}
        self.assertTrue(body_has_ended(body, now=self.AWARE_NOW))

    def test_aware_now_with_naive_future_body_time(self):
        body = {"end": {"dateTime": "2026-03-15T14:00:00"}}
        self.assertFalse(body_has_ended(body, now=self.AWARE_NOW))

    def test_naive_now_with_aware_past_body_time(self):
        body = {"end": {"dateTime": "2026-03-15T10:00:00+00:00"}}
        self.assertTrue(body_has_ended(body, now=self.NAIVE_NOW))

    def test_naive_now_with_aware_future_body_time(self):
        body = {"end": {"dateTime": "2026-03-15T14:00:00+00:00"}}
        self.assertFalse(body_has_ended(body, now=self.NAIVE_NOW))


class MalformedBodies(unittest.TestCase):
    def test_body_without_an_end_is_not_ended(self):
        self.assertFalse(body_has_ended({"summary": "No end"}))

    def test_empty_end_is_not_ended(self):
        self.assertFalse(body_has_ended({"end": {}}))


if __name__ == "__main__":
    unittest.main()


class SkippedPastIsReported(unittest.TestCase):
    """The guard must say what it dropped, not drop it silently."""

    def test_past_feed_event_is_recorded_as_skipped(self):
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", PAST)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, uid_prefix="", allow_past=False)
        self.assertEqual([uid for uid, _ in plan["skipped_past"]], ["a"])
        self.assertEqual(plan["create"], [])

    def test_future_event_is_not_recorded_as_skipped(self):
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", FUTURE)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, uid_prefix="", allow_past=False)
        self.assertEqual(plan["skipped_past"], [])

    def test_nothing_is_skipped_when_past_is_allowed(self):
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", PAST)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, uid_prefix="", allow_past=True)
        self.assertEqual(plan["skipped_past"], [])
        self.assertEqual([uid for uid, _ in plan["create"]], ["a"])

    def test_counts_include_the_skipped_past_total(self):
        from calendar_sync import plan_counts
        plan = plan_sync(feed_uids={"a", "b"},
                         feed_bodies={"a": body("a", PAST), "b": body("b", FUTURE)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, uid_prefix="", allow_past=False)
        self.assertEqual(plan_counts(plan)["skipped_past"], 1)

    def test_report_names_the_skipped_event(self):
        import dry_run
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", PAST)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, uid_prefix="", allow_past=False)
        from calendar_sync import plan_counts
        text = dry_run.render_report([{
            "index": 0, "config": {"calendar_id": "c", "uid_prefix": "",
                                   "myice_event_type": "g", "myice_club": "1"},
            "plan": plan, "counts": plan_counts(plan), "existing": {},
        }])
        self.assertIn("SKIPPED", text)
        self.assertIn("already ended", text)
        self.assertIn("1 past", text)
