"""
Google Calendar reconciliation: what to change, and doing it.

`plan_sync` is pure - it decides create/update/delete/tombstone from the feed,
the calendar, and the sync state, and does no I/O. `execute_plan` performs the
resulting Google writes. Keeping them apart is what lets --dry-run compute a
real plan without touching anything.

Nothing here knows about myice.hockey; it deals only in Google event bodies.
"""

import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

SOURCE_TAG = "myice-calendar-sync"
DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "Europe/Zurich")

# Google Calendar's built-in event colorId values, for reference.
COLOR_REFERENCE = {
    "1": "Lavender", "2": "Sage", "3": "Grape", "4": "Flamingo",
    "5": "Banana", "6": "Tangerine", "7": "Peacock", "8": "Graphite",
    "9": "Blueberry", "10": "Basil", "11": "Tomato",
}


def is_past_event(dtend, tzname: str) -> bool:
    """True if the event's end time is already behind us."""
    tz = ZoneInfo(tzname)
    now = datetime.now(tz)
    if isinstance(dtend, datetime):
        if dtend.tzinfo is None:
            dtend = dtend.replace(tzinfo=tz)
        return dtend < now
    if isinstance(dtend, date):
        return dtend < now.date()
    return False


def list_existing_synced_events(service, calendar_id: str, uid_prefix: str) -> dict:
    """Return {iCalUID: full_event_resource} for events previously synced by us matching the prefix."""
    existing = {}
    page_token = None
    while True:
        resp = (
            service.events()
            .list(
                calendarId=calendar_id,
                privateExtendedProperty=f"source={SOURCE_TAG}",
                pageToken=page_token,
                maxResults=250,
                showDeleted=False,
            )
            .execute()
        )
        for ev in resp.get("items", []):
            uid = ev.get("iCalUID")
            if uid and uid.startswith(uid_prefix):
                existing[uid] = ev
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return existing


# Fields we actually control and want to detect changes in. Google adds many
# other fields to an event resource (etag, sequence, creator, ...) that we
# never set ourselves, so comparing the whole object would always show a diff.
_COMPARE_FIELDS = ("summary", "location", "description", "colorId", "start", "end")


def event_unchanged(existing_event: dict, new_body: dict) -> bool:
    return all(existing_event.get(f) == new_body.get(f) for f in _COMPARE_FIELDS)


def google_event_end(existing_event: dict):
    """Parse a Google event's own 'end' field back into a date/datetime we can compare."""
    end = existing_event.get("end", {})
    if "dateTime" in end:
        return datetime.fromisoformat(end["dateTime"])
    if "date" in end:
        return date.fromisoformat(end["date"])
    return None


def purge_feed(
    service, calendar_id: str, uid_prefix: str, scope: str = "all", dry_run: bool = True
) -> dict:
    """
    Delete every event on calendar_id previously synced with this uid_prefix.

    scope:
      "all"    - delete everything ever synced under this prefix, past and future.
      "future" - delete only events that haven't happened yet; leaves past
                 (already-occurred) events on the calendar as a historical record.

    dry_run (default True): when True, nothing is deleted - just reports what
    would be. Callers must pass dry_run=False explicitly to actually delete.
    """
    if scope not in ("all", "future"):
        raise ValueError(f"Invalid scope {scope!r}, must be 'all' or 'future'")

    existing = list_existing_synced_events(service, calendar_id, uid_prefix)

    to_delete = []
    for uid, existing_event in existing.items():
        if scope == "future":
            end = google_event_end(existing_event)
            if end is not None and is_past_event(end, DEFAULT_TIMEZONE):
                continue  # leave past events alone in "future" scope
        to_delete.append((uid, existing_event["id"]))

    if not dry_run:
        for uid, event_id in to_delete:
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute(num_retries=3)

    return {
        "action": "purge",
        "calendar_id": calendar_id,
        "uid_prefix": uid_prefix,
        "scope": scope,
        "dry_run": dry_run,
        "matched": len(existing),
        "deleted": len(to_delete) if not dry_run else 0,
        "would_delete": len(to_delete) if dry_run else 0,
    }


def _state_entry(new_body: dict) -> dict:
    """A compact, human-readable record of a synced event for the state blob."""
    start = new_body.get("start", {})
    day = start.get("date") or start.get("dateTime", "")
    return {"date": day[:10], "summary": new_body.get("summary", "")}


def body_has_ended(body: dict, now: datetime | None = None) -> bool:
    """True if this event's end is in the past."""
    end = body.get("end", {})
    raw = end.get("dateTime") or end.get("date")
    if not raw:
        return False
    if "T" in raw:
        when = datetime.fromisoformat(raw)
        now = now or datetime.now(when.tzinfo)
        # Only one side tz-aware would raise on comparison. Read the naive side
        # as being in the other's zone rather than crashing a whole feed.
        if when.tzinfo is None and now.tzinfo is not None:
            when = when.replace(tzinfo=now.tzinfo)
        elif when.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=when.tzinfo)
        return when < now
    # All-day: Google's end.date is exclusive - an event occupying the 14th has
    # end.date == the 15th - so it has ended once that date has arrived.
    day = date.fromisoformat(raw[:10])
    return day <= (now.date() if now else date.today())


def plan_sync(feed_uids, feed_bodies, existing, state, respect_deletes, uid_prefix, allow_past=False):
    """
    Decide what to do with each event; returns action lists. Pure - does no I/O.

    feed_uids    - every UID currently in the feed (including past-skipped ones),
                   so the deletion pass never removes something still scheduled.
    feed_bodies  - {uid: google_body} for the events we'd actually sync (the
                   caller excludes past events).
    existing     - {uid: google_event} tagged events currently on the calendar.
    state        - {"synced": {...}, "tombstones": {...}}. Mutated in place, but
                   only when respect_deletes is True. This dict is shared across
                   every feed the caller syncs in one run, so every mutation here
                   must be scoped to uid_prefix - never touch another feed's
                   entries.
    respect_deletes - when True, an event we synced before that is still in the
                   feed but now missing from the calendar is treated as a manual
                   deletion: tombstoned and never recreated. When False (default),
                   such an event looks new and is recreated, and state is untouched.
    uid_prefix - this feed's UID prefix. Required (no default) so a caller can
                 never accidentally omit it and reintroduce cross-feed tombstone
                 wipes: state["tombstones"] is shared across every feed synced in
                 one run, so the stale-tombstone cleanup below must only ever
                 drop tombstones that belong to this feed.
    allow_past - when False (the default, and always true for a live sync), an
                 event that has already ended is never created, updated,
                 deleted or tombstoned. Only --dry-run sets this True, so it can
                 replay a past week for inspection.
    """
    plan = {"create": [], "update": [], "unchanged": [], "delete": [],
            "tombstone": [], "skip_tombstoned": [], "skipped_past": []}
    synced = state["synced"]
    tombstones = state["tombstones"]

    if not allow_past:
        # Record what the guard drops rather than discarding it silently: a
        # dry-run report that just omits past events looks identical to a feed
        # that never contained them, which makes "why is this missing?"
        # unanswerable.
        kept = {}
        for uid, body in feed_bodies.items():
            if body_has_ended(body):
                plan["skipped_past"].append((uid, body))
            else:
                kept[uid] = body
        feed_bodies = kept

    for uid, new_body in feed_bodies.items():
        existing_event = existing.get(uid)
        if not respect_deletes:
            if existing_event is None:
                plan["create"].append((uid, new_body))
            elif event_unchanged(existing_event, new_body):
                plan["unchanged"].append(uid)
            else:
                plan["update"].append((uid, new_body, existing_event["id"]))
            continue

        if existing_event is not None:
            # On the calendar: sync as normal and clear any stale tombstone.
            tombstones.pop(uid, None)
            if event_unchanged(existing_event, new_body):
                plan["unchanged"].append(uid)
            else:
                plan["update"].append((uid, new_body, existing_event["id"]))
            synced[uid] = _state_entry(new_body)
        elif uid in tombstones:
            plan["skip_tombstoned"].append(uid)
        elif uid in synced:
            # Synced before, still in the feed, now gone from the calendar -> the
            # user deleted it. Respect that: tombstone it and never recreate.
            tombstones[uid] = _state_entry(new_body)
            del synced[uid]
            plan["tombstone"].append(uid)
        else:
            plan["create"].append((uid, new_body))
            synced[uid] = _state_entry(new_body)

    # Feed-removal deletion - always, regardless of respect_deletes.
    for uid, existing_event in existing.items():
        if uid in feed_uids:
            continue
        if not allow_past and body_has_ended(existing_event):
            continue  # history stays as it was recorded
        plan["delete"].append((uid, existing_event["id"]))
        if respect_deletes:
            synced.pop(uid, None)
            tombstones.pop(uid, None)

    # A tombstone for something no longer in the feed is dead weight - drop it.
    # Scoped to this feed's uid_prefix: state["tombstones"] is shared across
    # every feed synced in one run, so without this check feed A's cleanup pass
    # would delete feed B's tombstones outright (they are never "in feed A's
    # feed_uids" either), resurrecting events feed B's user deleted by hand.
    if respect_deletes:
        for uid in list(tombstones):
            if uid.startswith(uid_prefix) and uid not in feed_uids:
                del tombstones[uid]

    return plan


def execute_plan(service, calendar_id: str, plan: dict, existing: dict) -> dict:
    """Apply a plan to the calendar. Returns per-action counts."""
    for uid, body in plan["create"]:
        service.events().import_(calendarId=calendar_id, body=body).execute(num_retries=3)
    for uid, body, event_id in plan["update"]:
        diff = {
            f: {"existing": existing[uid].get(f), "new": body.get(f)}
            for f in _COMPARE_FIELDS
            if existing[uid].get(f) != body.get(f)
        }
        print(f"UPDATE DIFF for {uid}: {json.dumps(diff, default=str)}")
        service.events().import_(calendarId=calendar_id, body=body).execute(num_retries=3)
    for uid, event_id in plan["delete"]:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute(num_retries=3)
    return plan_counts(plan)


def plan_counts(plan: dict) -> dict:
    return {
        "created": len(plan["create"]),
        "updated": len(plan["update"]),
        "unchanged": len(plan["unchanged"]),
        "deleted": len(plan["delete"]),
        "tombstoned": len(plan["tombstone"]),
        "skipped_tombstoned": len(plan["skip_tombstoned"]),
        "skipped_past": len(plan.get("skipped_past", [])),
    }
