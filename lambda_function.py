"""
Syncs events from a public ical feed into a Google Calendar.

Uses Google Calendar's events.import() method, which matches on iCalUID.
This means Google itself handles "insert if new / update if it already
exists" - no separate database is needed to track what's been synced.

Deletion of events that disappeared from the source feed is handled by
tagging every event we create with a private extendedProperty, then
diffing the set of UIDs currently on the calendar against the UIDs
currently in the feed.

Each entry in sync_configs.py's CONFIGS list supports:
  ical_url         (required)
  calendar_id      (required)
  uid_prefix       (default: "ical-")
  summary_format   (default: "{summary}") - use {summary} as a placeholder,
                    e.g. "Work {summary}" or "{summary} (away)"
  color_id         (optional) - Google Calendar event color, 1-11. See
                    COLOR_REFERENCE below for the mapping.
"""

import json
import os
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
from google.oauth2 import service_account
from googleapiclient.discovery import build
from icalendar import Calendar

try:
    from sync_configs import CONFIGS as PYTHON_CONFIGS
except ImportError:
    PYTHON_CONFIGS = None

# Deployed on Lambda this is set by deploy.sh; the default matches the SSM
# parameter that deploy.sh creates, so local runs work without it being set.
SSM_PARAM_NAME = os.environ.get("SERVICE_ACCOUNT_PARAM", "/ical-sync/google-service-account")
# For local test runs: a service-account JSON key on disk is used if present,
# so no AWS access is needed. Defaults to a gitignored file in the project root.
SERVICE_ACCOUNT_FILE = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    str(Path(__file__).parent / ".google-service-account.json"),
)
SOURCE_TAG = "aws-ical-sync"
DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "Europe/Zurich")
# When true (default), events that have already ended are neither imported nor
# deleted - they're just left alone. Set SKIP_PAST_EVENTS=false to sync everything.
SKIP_PAST_EVENTS = os.environ.get("SKIP_PAST_EVENTS", "true").lower() != "false"

# Google Calendar's built-in event colorId values, for reference.
COLOR_REFERENCE = {
    "1": "Lavender", "2": "Sage", "3": "Grape", "4": "Flamingo",
    "5": "Banana", "6": "Tangerine", "7": "Peacock", "8": "Graphite",
    "9": "Blueberry", "10": "Basil", "11": "Tomato",
}


def get_service_account_info():
    # Prefer a local key file (local runs); fall back to SSM (how the deployed
    # Lambda reads it - no key file is present there).
    path = Path(SERVICE_ACCOUNT_FILE)
    if path.is_file():
        return json.loads(path.read_text())
    ssm = boto3.client("ssm")
    resp = ssm.get_parameter(Name=SSM_PARAM_NAME, WithDecryption=True)
    return json.loads(resp["Parameter"]["Value"])


def get_calendar_service():
    info = get_service_account_info()
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/calendar"]
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def fetch_ical(ical_url: str):
    req = urllib.request.Request(ical_url, headers={"User-Agent": "ical-sync-lambda"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    return Calendar.from_ical(data)


def normalize_uid(raw_uid: str, uid_prefix: str) -> str:
    # Namespace the UID so it can't collide with anything else on the calendar,
    # and so it's safe/valid regardless of what the source feed puts in there.
    safe = "".join(c for c in raw_uid if c.isalnum() or c in "-_@.")
    return f"{uid_prefix}{safe}"


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


def event_to_google_body(
    component,
    uid: str,
    summary_format: str,
    color_id: str | None
) -> dict | None:
    raw_summary = str(component.get("summary", "iCal Event"))
    try:
        summary = summary_format.format(summary=raw_summary)
    except (KeyError, IndexError):
        # Malformed format string - fall back to the raw summary rather than crash the sync
        print(f"WARNING: invalid summary_format {summary_format!r}, using raw summary")
        summary = raw_summary

    location = component.get("location")
    description = component.get("description")

    dtstart = component.get("dtstart").dt
    dtend_field = component.get("dtend")
    dtend = dtend_field.dt if dtend_field else dtstart

    if SKIP_PAST_EVENTS and is_past_event(dtend, DEFAULT_TIMEZONE):
        return None

    body = {
        "iCalUID": uid,
        "summary": summary,
        "status": "confirmed",
        "extendedProperties": {"private": {"source": SOURCE_TAG}},
    }
    if location:
        body["location"] = str(location)
    if description:
        body["description"] = str(description)
    if color_id:
        body["colorId"] = str(color_id)

    body["reminders"] = {"useDefault": True}

    if hasattr(dtstart, "hour"):
        # 1. Determine the timezone name and object
        tz = dtstart.tzinfo
        tzname = getattr(tz, "zone", None) or getattr(tz, "key", None) if tz is not None else None
        tzname = tzname or DEFAULT_TIMEZONE

        # 2. Convert default timezone string to a ZoneInfo object
        default_tz = ZoneInfo(DEFAULT_TIMEZONE)

        # 3. If the datetimes are naive, attach the timezone info
        if dtstart.tzinfo is None:
            dtstart = dtstart.replace(tzinfo=default_tz)
        if dtend.tzinfo is None:
            dtend = dtend.replace(tzinfo=default_tz)

        body["start"] = {"dateTime": dtstart.isoformat(), "timeZone": tzname}
        body["end"] = {"dateTime": dtend.isoformat(), "timeZone": tzname}
    else:
        body["start"] = {"date": dtstart.isoformat()}
        body["end"] = {"date": dtend.isoformat()}

    return body


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


def sync_feed(
    service,
    ical_url: str,
    calendar_id: str,
    uid_prefix: str,
    summary_format: str,
    color_id: str | None
) -> dict:
    cal = fetch_ical(ical_url)
    existing = list_existing_synced_events(service, calendar_id, uid_prefix)

    current_uids = set()
    created = 0
    updated = 0
    unchanged = 0
    skipped_past = 0

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        raw_uid = str(component.get("uid"))
        uid = normalize_uid(raw_uid, uid_prefix)
        # Always track the UID, even if we skip syncing it - this stops the
        # deletion pass below from removing an already-synced past event
        # just because it's now old.
        current_uids.add(uid)

        body = event_to_google_body(
            component, uid, summary_format, color_id
        )
        if body is None:
            skipped_past += 1
            continue

        existing_event = existing.get(uid)
        if existing_event is None:
            service.events().import_(calendarId=calendar_id, body=body).execute(num_retries=3)
            created += 1
        elif event_unchanged(existing_event, body):
            unchanged += 1
        else:
            diff = {
                f: {"existing": existing_event.get(f), "new": body.get(f)}
                for f in _COMPARE_FIELDS
                if existing_event.get(f) != body.get(f)
            }
            print(f"UPDATE DIFF for {uid}: {json.dumps(diff, default=str)}")
            service.events().import_(calendarId=calendar_id, body=body).execute(num_retries=3)
            updated += 1

    deleted = 0
    for uid, existing_event in existing.items():
        if uid not in current_uids:
            service.events().delete(calendarId=calendar_id, eventId=existing_event["id"]).execute(num_retries=3)
            deleted += 1

    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "deleted": deleted,
        "skipped_past": skipped_past,
        "total_in_feed": len(current_uids),
    }


def handler(event, context):
    service = get_calendar_service()

    # Guarded manual purge mode. Only runs when explicitly invoked with a
    # payload like:
    #   {"action": "purge", "calendar_id": "...", "uid_prefix": "ehc-",
    #    "scope": "all", "confirm": true}
    # Never triggered by the daily schedule (which invokes with an empty
    # event). Defaults to a dry run - nothing is deleted unless "confirm"
    # is explicitly true in the payload, so a plain invocation always just
    # reports what *would* happen.
    if isinstance(event, dict) and event.get("action") == "purge":
        calendar_id = event.get("calendar_id")
        uid_prefix = event.get("uid_prefix")
        scope = event.get("scope", "all")
        confirm = bool(event.get("confirm", False))

        if not calendar_id or not uid_prefix:
            raise ValueError(
                "Purge requires both 'calendar_id' and 'uid_prefix' in the payload "
                "- refusing to guess, to avoid deleting the wrong events."
            )

        result = purge_feed(service, calendar_id, uid_prefix, scope=scope, dry_run=not confirm)
        print(json.dumps(result))
        if not confirm:
            print(
                f"DRY RUN - would delete {result['would_delete']} of {result['matched']} "
                f"matched event(s). Re-invoke with \"confirm\": true to actually delete."
            )
        return result

    # Config source: sync_configs.py's CONFIGS list. This is the only
    # supported source - keeping config loading to one path (a real,
    # readable Python file) avoids the shell-escaping and stale-fallback
    # bugs that came from juggling multiple config sources previously.
    if PYTHON_CONFIGS is None:
        raise ValueError(
            "No sync_configs.py found (or it failed to import). Create one with a "
            "CONFIGS list - see sync_configs_example.py for the expected shape."
        )
    configs = PYTHON_CONFIGS

    # Guard against a copy-paste mistake reusing a uid_prefix on the same
    # calendar - that would make one feed's deletion pass silently delete
    # another feed's events. Fail loudly and immediately instead. (Reusing
    # a prefix across *different* calendars is harmless and allowed.)
    keys = [(c.get("calendar_id"), c.get("uid_prefix", "ical-")) for c in configs]
    seen = set()
    duplicates = {k for k in keys if k in seen or seen.add(k)}
    if duplicates:
        raise ValueError(
            f"Duplicate (calendar_id, uid_prefix) combination(s) in sync_configs.py: "
            f"{sorted(duplicates)} - each feed sharing a calendar must have a unique "
            "uid_prefix, or feeds can silently delete each other's events. Refusing to run."
        )

    results = []
    overall_success = True
    for idx, config in enumerate(configs):
        ical_url = config.get("ical_url")
        calendar_id = config.get("calendar_id")
        uid_prefix = config.get("uid_prefix", "ical-")
        summary_format = config.get("summary_format", "{summary}")
        color_id = config.get("color_id")

        if not ical_url or not calendar_id:
            print(f"Config at index {idx} is missing ical_url or calendar_id: {config}")
            results.append({"config_index": idx, "status": "failed", "error": "Missing parameters"})
            overall_success = False
            continue

        print(
            f"Syncing feed {ical_url} -> calendar {calendar_id} "
            f"(prefix: {uid_prefix}, format: {summary_format!r}, color: {color_id})"
        )
        try:
            res = sync_feed(
                service,
                ical_url,
                calendar_id,
                uid_prefix,
                summary_format,
                color_id,
            )
            res["config_index"] = idx
            res["status"] = "success"
            print(f"Success syncing feed {ical_url}: {res}")
            results.append(res)
        except Exception as e:
            print(f"Failed to sync feed {ical_url}: {e}")
            results.append({
                "config_index": idx,
                "status": "failed",
                "error": str(e)
            })
            overall_success = False

    print(json.dumps({"overall_success": overall_success, "results": results}))
    if not overall_success:
        raise RuntimeError("One or more feeds failed to sync.")
    return results
