"""
Syncs games and trainings from myice.hockey into a Google Calendar.

Uses Google Calendar's events.import() method, which matches on iCalUID.
This means Google itself handles "insert if new / update if it already
exists" - no separate database is needed to track what's been synced.

Deletion of events that disappeared from myice is handled by tagging every
event we create with a private extendedProperty, then diffing the set of
UIDs currently on the calendar against the UIDs currently in myice.

Each myice record's health_status decides what happens to its calendar
event: synced normally, synced as a tentative "request" awaiting a
response in the app, or removed entirely. See STATUS_ACTIONS and
classify() below.
"""

import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import boto3
from google.oauth2 import service_account
from googleapiclient.discovery import build

import sync_state
from calendar_sync import (
    DEFAULT_TIMEZONE,
    SOURCE_TAG,
    purge_feed,
)

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
MYICE_CREDENTIALS_PARAM_DEFAULT = "/myice-sync/myice-credentials"

# health_status -> (action, Google status).
#   sync    - normal event
#   request - awaiting your response in the app; styled differently
#   remove  - you are out; the calendar entry and everything derived from it goes
# Codes come from the app's own #health_status select, plus "3", which the
# system assigns rather than offering as a choice.
STATUS_ACTIONS = {
    "1": ("sync", "confirmed"),     # Gesund
    "3": ("request", "tentative"),  # Temporär - pending your response
    "6": ("remove", ""),            # Entschuldigt
    "8": ("remove", ""),            # Krank
    "9": ("remove", ""),            # Verletzt
}
PLACEHOLDER_MEETING_TIMES = ("", "00:00", "00:00:00")


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


def classify(record: dict) -> tuple[str, str]:
    """Decide what to do with a record based on its health_status."""
    raw = record.get("health_status")
    key = str(raw) if raw is not None else ""
    if key in STATUS_ACTIONS:
        return STATUS_ACTIONS[key]
    if key:
        # Log rather than guess silently: a new status should be visible in
        # CloudWatch, not quietly treated as normal forever.
        print(f"WARNING: unknown health_status {key!r}; syncing as confirmed")
    return ("sync", "confirmed")


def event_details(record: dict) -> str:
    """The event's full detail text, as it goes into the description."""
    parts = []
    if record.get("notes"):
        parts.append(str(record["notes"]))
    if record.get("health_notes"):
        parts.append(f"Note: {record['health_notes']}")
    meeting = str(record.get("meeting") or "")
    if meeting not in PLACEHOLDER_MEETING_TIMES:
        parts.append(f"Meeting time: {meeting}")
    if record.get("health_status_label"):
        parts.append(f"Status: {record['health_status_label']}")
    return "\n".join(parts)


def record_uid(record: dict, uid_prefix: str) -> str:
    return f"{uid_prefix}{record.get('id_game')}"


def event_start_end(record: dict, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    day = record["date"]
    start = datetime.strptime(f"{day} {record['time_start']}", "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(f"{day} {record['time_end']}", "%Y-%m-%d %H:%M:%S")
    return start.replace(tzinfo=tz), end.replace(tzinfo=tz)


def _format_summary(template: str, raw_summary: str) -> str:
    try:
        return template.format(summary=raw_summary)
    except (KeyError, IndexError):
        print(f"WARNING: invalid summary format {template!r}, using raw summary")
        return raw_summary


def record_to_google_body(record: dict, config: dict, action: str, google_status: str) -> dict:
    """
    Map a myice record to a Google event body.

    Always returns a body. Whether a past event is actually written is decided
    by plan_sync, so that policy lives in exactly one tested place.
    """
    tz_name = config.get("timezone", DEFAULT_TIMEZONE)
    start, end = event_start_end(record, tz_name)

    raw_summary = f"{record.get('agegroup', '')} {record.get('name', '')}".strip()
    raw_summary = raw_summary or "myice.hockey Event"

    if action == "request":
        template = config.get("request_summary_format", "❓ {summary}")
        color_id = config.get("request_color_id") or config.get("color_id")
    else:
        template = config.get("summary_format", "{summary}")
        color_id = config.get("color_id")

    body = {
        "summary": _format_summary(template, raw_summary),
        "status": google_status or "confirmed",
        "extendedProperties": {"private": {"source": SOURCE_TAG}},
        "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
        "reminders": {"useDefault": True},
    }
    if record.get("place"):
        body["location"] = str(record["place"])
    details = event_details(record)
    if details:
        body["description"] = details
    if color_id:
        body["colorId"] = str(color_id)
    return body


def prep_uid(record: dict, uid_prefix: str) -> str:
    # Not derived from the meeting time, so that a club editing the meeting time
    # updates this entry in place rather than deleting and recreating it.
    return f"{uid_prefix}prep-{record.get('id_game')}"


def prep_start(record: dict, start: datetime, tz_name: str, prep_minutes: int) -> datetime:
    """
    When the preparation block begins.

    Prefers the club's own stated meeting time for this specific event, because
    that is real data rather than a per-club average. Falls back to the
    configured offset when absent or nonsensical.
    """
    meeting = str(record.get("meeting") or "")
    if meeting in PLACEHOLDER_MEETING_TIMES:
        return start - timedelta(minutes=prep_minutes)

    try:
        parts = [int(p) for p in meeting.split(":")]
        hour, minute = parts[0], parts[1]
        second = parts[2] if len(parts) > 2 else 0
        candidate = start.replace(hour=hour, minute=minute, second=second, microsecond=0)
    except (ValueError, IndexError):
        print(f"WARNING: unparseable meeting time {meeting!r}; using prep_minutes")
        return start - timedelta(minutes=prep_minutes)

    if candidate >= start:
        print(f"WARNING: meeting time {meeting!r} is at or after the event start; "
              "using prep_minutes")
        return start - timedelta(minutes=prep_minutes)
    return candidate


def prep_body(record: dict, config: dict, parent_body: dict, google_status: str) -> dict | None:
    """
    The warm-up / gathering block before an event, or None if not configured.

    Every field is derived from `record`, the same single source
    record_to_google_body uses. `parent_body` is part of the signature for
    callers that already have the parent event to hand, and is reserved for
    genuinely parent-derived values; nothing is read from it today.
    """
    prep_minutes = int(config.get("prep_minutes") or 0)
    if prep_minutes < 0:
        # A negative offset would end the entry before it starts. Treat a config
        # typo as "off" rather than failing the feed, but say so - otherwise the
        # feature silently does nothing and nobody finds out why.
        print(f"WARNING: prep_minutes is {prep_minutes}; it must be positive. "
              "No preparation entry will be created.")
        return None
    if prep_minutes == 0:
        # Zero is a legitimate way to say "off".
        return None

    tz_name = config.get("timezone", DEFAULT_TIMEZONE)
    start, _end = event_start_end(record, tz_name)
    begins = prep_start(record, start, tz_name, prep_minutes)

    raw_summary = f"{record.get('agegroup', '')} {record.get('name', '')}".strip()
    raw_summary = raw_summary or "myice.hockey Event"
    template = config.get("prep_summary_format", "Warm-up: {summary}")

    body = {
        "summary": _format_summary(template, raw_summary),
        "status": google_status or "confirmed",
        "extendedProperties": {"private": {"source": SOURCE_TAG}},
        "start": {"dateTime": begins.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "reminders": {"useDefault": True},
    }
    # Location and description come from the record, the same single source
    # record_to_google_body uses, rather than from parent_body - so a caller
    # passing a mismatched parent_body cannot leak stale text in here.
    if record.get("place"):
        body["location"] = str(record["place"])
    details = event_details(record)
    if details:
        body["description"] = details
    color_id = config.get("prep_color_id") or config.get("color_id")
    if color_id:
        body["colorId"] = str(color_id)
    return body


def handler(event, context):  # noqa: this is rewritten by a later task
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

    # State is only needed for feeds that opt into respecting manual deletions.
    # If none do, behavior is byte-for-byte as before and we never touch storage
    # (important on Lambda, whose only writable/persistent store is the S3 URI).
    any_respect = any(c.get("respect_manual_deletions") for c in configs)
    state = sync_state.load() if any_respect else {"synced": {}, "tombstones": {}}

    results = []
    overall_success = True
    for idx, config in enumerate(configs):
        ical_url = config.get("ical_url")
        calendar_id = config.get("calendar_id")
        uid_prefix = config.get("uid_prefix", "ical-")
        summary_format = config.get("summary_format", "{summary}")
        color_id = config.get("color_id")
        respect_deletes = bool(config.get("respect_manual_deletions", False))

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
                respect_deletes=respect_deletes,
                state=state,
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

    # Persist state (tombstones/synced records) from feeds that opted in. Saved
    # even if some feed failed, so successful feeds' state isn't lost.
    if any_respect:
        sync_state.save(sync_state.DEFAULT_STATE_URI, state)

    print(json.dumps({"overall_success": overall_success, "results": results}))
    if not overall_success:
        raise RuntimeError("One or more feeds failed to sync.")
    return results
