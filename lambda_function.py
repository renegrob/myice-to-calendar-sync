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

import hashlib
import html
import json
import os
import re
import string
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import boto3
from google.oauth2 import service_account
from googleapiclient.discovery import build

import duty_parser
import myice_client
import sync_state
import calendar_sync
from calendar_sync import (
    DEFAULT_TIMEZONE,
    SOURCE_TAG,
    execute_plan,
    list_existing_synced_events,
    plan_sync,
    purge_feed,
)

try:
    from sync_configs import CONFIGS as PYTHON_CONFIGS
except ImportError:
    PYTHON_CONFIGS = None

# Deployed on Lambda this is set by deploy.sh; the default matches the SSM
# parameter that deploy.sh creates, so local runs work without it being set.
SSM_PARAM_NAME = os.environ.get("SERVICE_ACCOUNT_PARAM", "/myice-sync/google-service-account")
# For local test runs: a service-account JSON key on disk is used if present,
# so no AWS access is needed. Defaults to a gitignored file in the project root.
SERVICE_ACCOUNT_FILE = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    str(Path(__file__).parent / ".google-service-account.json"),
)

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
DEFAULT_UID_PREFIX = "myice-"


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


# `<br />` is almost always followed by a real newline too, so consume one if
# present - otherwise every line comes out double-spaced. A `<br />` with no
# newline still yields a break, and two in a row still yield a blank line.
_BR_RE = re.compile(r"<br\s*/?>\n?", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(raw) -> str:
    """
    Turn a myice detail blob into plain text.

    myice stores these as HTML: lines separated by `<br />` (usually followed by
    a real newline too), with entities escaped and stray tabs. Left as-is,
    `<br />` shows up verbatim in calendar descriptions, and a duty entry's
    summary comes out as "Speaker: Rene Grob\t<br />".

    `<br />` is treated as the line separator rather than just stripped,
    because a blob that uses it *without* a newline would otherwise collapse
    several duties onto one line and yield a single merged duty entry.
    """
    # Normalise line endings first, so the <br />-plus-newline collapse below
    # sees a plain \n rather than \r\n.
    text = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    # Blank lines are kept: they separate sections (e.g. the "PLO" block).
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def event_details(record: dict) -> str:
    """The event's full detail text, as it goes into the description."""
    parts = []
    if record.get("notes"):
        parts.append(html_to_text(record["notes"]))
    if record.get("health_notes"):
        parts.append(f"Note: {html_to_text(record['health_notes'])}")
    meeting = str(record.get("meeting") or "")
    if meeting not in PLACEHOLDER_MEETING_TIMES:
        parts.append(f"Meeting time: {meeting}")
    if record.get("health_status_label"):
        parts.append(f"Status: {record['health_status_label']}")
    return "\n".join(parts)


def record_id(record: dict):
    """
    The record's own id. Games carry `id_game`, practices carry `id_practice`.

    Returns None when neither is present, which build_feed treats as a record
    it cannot key and skips - better than silently collapsing every such
    record onto one UID.
    """
    return record.get("id_game") or record.get("id_practice")


def raw_summary(record: dict) -> str:
    """
    The event's title before any summary_format is applied.

    Note `.get(key, "")` is NOT enough here: practice records contain
    "agegroup": null, and a default only applies when the key is *absent*, so
    that produced summaries reading "None U14 (ICE ALL)".

    Practices have no agegroup but do have a useful `type` ("Eistraining"),
    while a game's `type` is just "Saison" - so prefer agegroup and fall back
    to type.
    """
    prefix = (record.get("agegroup") or record.get("type") or "").strip()
    name = (record.get("name") or "").strip()
    return " ".join(p for p in (prefix, name) if p) or "myice.hockey Event"


def record_uid(record: dict, uid_prefix: str) -> str:
    return f"{uid_prefix}{record_id(record)}"


def event_start_end(record: dict, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    day = record["date"]
    start = datetime.strptime(f"{day} {record['time_start']}", "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(f"{day} {record['time_end']}", "%Y-%m-%d %H:%M:%S")
    return start.replace(tzinfo=tz), end.replace(tzinfo=tz)


def summary_values(record: dict) -> dict:
    """
    The placeholders available to summary_format, request_summary_format,
    prep_summary_format and duty_summary_format.

    Every value is stringified with None -> "", because myice returns null for
    fields that do not apply - a practice has no agegroup, an unplayed game has
    no result - and the literal "None" must never reach a calendar entry.

    Times are trimmed to HH:MM; myice sends HH:MM:SS, and on some game records
    time_end is a recorded timestamp like "11:58:04" rather than a round time.
    """
    def s(key: str) -> str:
        return str(record.get(key) or "").strip()

    return {
        # The composed default: agegroup (or type, for practices) plus name.
        "summary": raw_summary(record),
        "name": s("name"),
        "type": s("type"),
        "agegroup": s("agegroup"),
        "place": s("place"),
        "weekday": s("weekday"),
        "date": s("date"),
        "time_start": s("time_start")[:5],
        "time_end": s("time_end")[:5],
        "duration": s("duration"),
        "status": s("health_status_label"),
        "result": s("result"),
    }


_WS_RE = re.compile(r"\s+")


class _ConditionalFormatter(string.Formatter):
    """
    Adds a conditional-segment spec on top of normal str.format:

        {variable:?prefix%suffix}

    The whole segment renders only when the value is non-empty; when it is
    empty, prefix and suffix vanish with it.

    This exists because which fields myice populates depends on the record:
    practices have no agegroup, place or result, games have no duration, and an
    unplayed game has no result. Collapsing whitespace is not enough to tidy up
    after an empty field - a literal separator still dangles, so
    "{name} @ {place}" on a practice gives "U14 (ICE ALL) @". Written as
    "{name}{place:? @ %}" it gives just "U14 (ICE ALL)".

    Ordinary specs still work: {duration:>4} formats as it always did.
    """

    def format_field(self, value, format_spec):
        if format_spec.startswith("?"):
            text = str(value)
            if not text.strip():
                return ""
            prefix, _, suffix = format_spec[1:].partition("%")
            return f"{prefix}{text}{suffix}"
        return super().format_field(value, format_spec)


_FORMATTER = _ConditionalFormatter()


def _format_summary(template: str, values: dict) -> str:
    try:
        text = _FORMATTER.vformat(template, (), values)
    except (KeyError, IndexError) as exc:
        print(f"WARNING: invalid summary format {template!r} ({exc}); "
              "using the default summary")
        return values["summary"]
    # A template referencing a field that is empty for this record would
    # otherwise leave a double space or a stray leading/trailing one - e.g.
    # "{agegroup} {name}" against a practice, which has no agegroup.
    return _WS_RE.sub(" ", text).strip()


def record_to_google_body(record: dict, config: dict, action: str, google_status: str) -> dict:
    """
    Map a myice record to a Google event body.

    Always returns a body. Whether a past event is actually written is decided
    by plan_sync, so that policy lives in exactly one tested place.
    """
    tz_name = config.get("timezone", DEFAULT_TIMEZONE)
    start, end = event_start_end(record, tz_name)

    values = summary_values(record)

    if action == "request":
        template = config.get("request_summary_format", "❓ {summary}")
        color_id = config.get("request_color_id") or config.get("color_id")
    else:
        template = config.get("summary_format", "{summary}")
        color_id = config.get("color_id")

    body = {
        "summary": _format_summary(template, values),
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
    return f"{uid_prefix}prep-{record_id(record)}"


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


def prep_body(record: dict, config: dict, google_status: str) -> dict | None:
    """
    The warm-up / gathering block before an event, or None if not configured.

    Every field is derived from `record`, the same single source
    record_to_google_body uses - there is no parent_body parameter here (unlike
    duty_bodies, which genuinely needs the parent event's start/end), precisely
    so a caller can never pass a mismatched parent event and leak stale text in.
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

    values = summary_values(record)
    template = config.get("prep_summary_format", "Warm-up: {summary}")

    body = {
        "summary": _format_summary(template, values),
        "status": google_status or "confirmed",
        "extendedProperties": {"private": {"source": SOURCE_TAG}},
        "start": {"dateTime": begins.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "reminders": {"useDefault": True},
    }
    # Location and description come from the record, the same single source
    # record_to_google_body uses.
    if record.get("place"):
        body["location"] = str(record["place"])
    details = event_details(record)
    if details:
        body["description"] = details
    color_id = config.get("prep_color_id") or config.get("color_id")
    if color_id:
        body["colorId"] = str(color_id)
    return body


def duty_uid(record: dict, uid_prefix: str, line: str) -> str:
    # Hashing the line rather than using its index keeps the UID stable when
    # unrelated lines are added or reordered. Editing a matched line does change
    # its UID, which correctly reads as "that duty went away, this one appeared".
    digest = hashlib.sha1(duty_parser.normalise(line).encode("utf-8")).hexdigest()[:8]
    return f"{uid_prefix}duty-{record_id(record)}-{digest}"


def duty_bodies(record: dict, config: dict, parent_body: dict, google_status: str) -> dict:
    """One calendar entry per detail line that names you. UID -> body."""
    duty_names = config.get("duty_names") or []
    lines = duty_parser.find_duty_lines(event_details(record), duty_names)
    if not lines:
        return {}

    uid_prefix = config.get("uid_prefix", DEFAULT_UID_PREFIX)
    template = config.get("duty_summary_format", "{line}")
    color_id = config.get("duty_color_id") or config.get("color_id")
    values = summary_values(record)

    bodies = {}
    for line in lines:
        # Duty templates get {line} on top of every record placeholder, and
        # the same conditional-segment spec.
        try:
            summary = _WS_RE.sub(
                " ", _FORMATTER.vformat(template, (), {**values, "line": line})
            ).strip()
        except (KeyError, IndexError) as exc:
            print(f"WARNING: invalid duty_summary_format {template!r} ({exc}); "
                  "using the line")
            summary = line

        body = {
            "summary": summary,
            "status": google_status or "confirmed",
            "extendedProperties": {"private": {"source": SOURCE_TAG}},
            "start": dict(parent_body["start"]),
            "end": dict(parent_body["end"]),
            "reminders": {"useDefault": True},
        }
        if parent_body.get("location"):
            body["location"] = parent_body["location"]
        if parent_body.get("description"):
            body["description"] = parent_body["description"]
        if color_id:
            body["colorId"] = str(color_id)
        bodies[duty_uid(record, uid_prefix, line)] = body
    return bodies


REQUIRED_FIELDS = (
    "calendar_id", "myice_login_url", "myice_username_field",
    "myice_password_field", "myice_credentials_param", "myice_filter_url",
    "myice_player_id", "myice_event_type", "myice_season", "myice_club",
    "myice_min_date", "myice_max_date",
)


# Substrings that only appear in sync_configs_example.py's fill-me-in values.
# A left-over placeholder otherwise surfaces as a bare 404 from Google, which
# says nothing about the cause - and a placeholder calendar address that happens
# to be a real shared calendar would be worse than an error.
_PLACEHOLDER_MARKERS = ("TODO_", "your.email@", "your-myice-", "your-surname")


def _placeholder_fields(config: dict) -> list[str]:
    """Config keys whose value is still an example placeholder."""
    def unfilled(value) -> bool:
        if isinstance(value, str):
            return any(m in value for m in _PLACEHOLDER_MARKERS)
        if isinstance(value, (list, tuple)):
            return any(unfilled(v) for v in value)
        if isinstance(value, dict):
            return any(unfilled(v) for v in value.values())
        return False

    return sorted(k for k, v in config.items() if unfilled(v))


def validate_configs(configs: list[dict]) -> None:
    """Check every club entry up front, so a typo fails before any syncing."""
    # calendar_id -> list of (uid_prefix, idx) seen so far on that calendar.
    by_calendar: dict[str, list[tuple[str, int]]] = {}
    for idx, config in enumerate(configs):
        missing = [f for f in REQUIRED_FIELDS if not config.get(f)]
        if missing:
            raise RuntimeError(
                f"Config at index {idx} is missing required field(s): {', '.join(missing)}")
        if config["myice_event_type"] not in ("g", "p"):
            raise RuntimeError(
                f"Config at index {idx} has myice_event_type "
                f"{config['myice_event_type']!r}; expected 'g' (games) or 'p' (trainings)")

        left_unfilled = _placeholder_fields(config)
        if left_unfilled:
            raise RuntimeError(
                f"Config at index {idx} still has example placeholder value(s) in: "
                f"{', '.join(left_unfilled)}. Fill these in from sync_configs_example.py's "
                "instructions (see docs/capturing-ids.md).")

        if "prep_minutes" in config:
            prep_minutes = config["prep_minutes"]
            valid = isinstance(prep_minutes, int) and not isinstance(prep_minutes, bool)
            if not valid and isinstance(prep_minutes, str):
                valid = prep_minutes.lstrip("-").isdigit()
            if not valid or int(prep_minutes) < 0:
                raise RuntimeError(
                    f"Config at index {idx} has invalid prep_minutes {prep_minutes!r}; "
                    "expected a non-negative integer")

        calendar_id = config["calendar_id"]
        prefix = config.get("uid_prefix", DEFAULT_UID_PREFIX)
        # Identical prefixes are the special case of "one startswith the
        # other" - either way, list_existing_synced_events(cal, prefix) for
        # one feed would also match the other feed's events, and plan_sync
        # would then plan to delete them as no-longer-in-this-feed.
        for other_prefix, other_idx in by_calendar.get(calendar_id, []):
            if prefix.startswith(other_prefix) or other_prefix.startswith(prefix):
                raise RuntimeError(
                    f"Configs at index {other_idx} and {idx} share calendar_id "
                    f"{calendar_id!r} with overlapping uid_prefix values "
                    f"{other_prefix!r} and {prefix!r}; they would delete each "
                    "other's events. Give each club a distinct, non-overlapping "
                    "uid_prefix.")
        by_calendar.setdefault(calendar_id, []).append((prefix, idx))


def build_feed(records: list[dict], config: dict) -> tuple[set, dict]:
    """
    Turn myice records into the UID/body maps plan_sync consumes.

    A record whose status says "remove" (sick, injured, excused) contributes
    nothing at all - not the event, not its prep block, not its duties. Absent
    from the feed means plan_sync deletes whatever is on the calendar.
    """
    uid_prefix = config.get("uid_prefix", DEFAULT_UID_PREFIX)
    feed_uids, feed_bodies = set(), {}

    for record in records:
        if record_id(record) is None:
            # record_uid/prep_uid/duty_uid all key off the record id; every
            # record missing one would map to the same "...-None" UID, silently
            # collapsing them into a single calendar event. Skip and warn
            # rather than fail the whole feed over one bad record - per-feed
            # isolation in handler() already bounds the blast radius of a
            # feed failure, so there is nothing to gain by raising here and a
            # transient myice glitch on one record would then cost every
            # other record in the same feed too.
            print(f"WARNING: record has no id_game/id_practice; skipping it: {record!r}")
            continue

        action, google_status = classify(record)
        if action == "remove":
            continue

        uid = record_uid(record, uid_prefix)
        body = record_to_google_body(record, config, action, google_status)
        derived = {uid: body}

        prep = prep_body(record, config, google_status)
        if prep is not None:
            derived[prep_uid(record, uid_prefix)] = prep
        derived.update(duty_bodies(record, config, body, google_status))

        for derived_uid, derived_body in derived.items():
            derived_body["iCalUID"] = derived_uid
            feed_uids.add(derived_uid)
            feed_bodies[derived_uid] = derived_body

    return feed_uids, feed_bodies


def select_configs(configs: list[dict], only: str | None) -> list[dict]:
    """Filter club entries by event type. `only` is 'g', 'p', or None."""
    if only is None:
        return list(configs)
    return [c for c in configs if c.get("myice_event_type") == only]


def load_configs() -> list[dict]:
    if PYTHON_CONFIGS is None:
        raise RuntimeError(
            "No sync_configs.py found (or it failed to import). Create one with a "
            "CONFIGS list - see sync_configs_example.py for the expected shape."
        )
    return PYTHON_CONFIGS


def get_myice_credentials(ssm_param_name: str) -> dict:
    ssm = boto3.client("ssm")
    resp = ssm.get_parameter(Name=ssm_param_name, WithDecryption=True)
    return json.loads(resp["Parameter"]["Value"])


def sync_club(service, config: dict, state: dict,
              allow_past: bool = False, plan_only: bool = False) -> dict:
    """Fetch one club's feed and plan (and unless plan_only, apply) the changes."""
    calendar_id = config["calendar_id"]
    uid_prefix = config.get("uid_prefix", DEFAULT_UID_PREFIX)

    creds = get_myice_credentials(config["myice_credentials_param"])
    session = myice_client.login(
        config["myice_login_url"], creds["username"], creds["password"],
        config["myice_username_field"], config["myice_password_field"],
        config.get("myice_login_extra_fields"),
    )
    records = myice_client.fetch_records(
        session, config["myice_filter_url"],
        player_id=config["myice_player_id"],
        event_type=config["myice_event_type"],
        season=config["myice_season"], club=config["myice_club"],
        min_date=config.get("_min_date_override") or config["myice_min_date"],
        max_date=config["myice_max_date"],
    )

    feed_uids, feed_bodies = build_feed(records, config)
    existing = list_existing_synced_events(service, calendar_id, uid_prefix)
    respect_deletes = bool(config.get("respect_manual_deletions", False))
    plan = plan_sync(feed_uids, feed_bodies, existing, state,
                      respect_deletes, uid_prefix, allow_past=allow_past)

    counts = (calendar_sync.plan_counts(plan) if plan_only
              else calendar_sync.execute_plan(service, calendar_id, plan, existing))
    counts["total_in_feed"] = len(feed_uids)
    counts["records_fetched"] = len(records)
    return {"plan": plan, "existing": existing, "counts": counts}


def describe_exception(exc: BaseException) -> str:
    """
    Render an exception for the per-club error report.

    Deliberately defensive: this sits in the one path whose whole job is to keep
    the run alive and get the CloudWatch alarm fired, so a custom __str__ that
    itself raises must not take down the remaining clubs with it. repr() does
    not call __str__, and the final fallback needs nothing from the exception
    but its type.
    """
    try:
        return f"{type(exc).__name__}: {exc}"
    except Exception:
        pass
    try:
        return repr(exc)
    except Exception:
        return f"{type(exc).__name__}: <unprintable exception>"


def handler(event, context, only: str | None = None):
    """
    The Lambda entrypoint: `event` and `context` are the AWS-supplied signature
    and must keep working unchanged for the deployed schedule (which always
    calls `handler(event, context)`).

    `only` is for local callers (run_local.py's --apply path) that need to
    restrict which club feeds actually get synced - 'g', 'p', or None for
    every feed. It is never set by the Lambda schedule itself.
    """
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

    configs = load_configs()
    validate_configs(configs)
    configs = select_configs(configs, only)

    # State is only needed for feeds that opt into respecting manual deletions.
    # If none do, behavior is byte-for-byte as before and we never touch storage
    # (important on Lambda, whose only writable/persistent store is the S3 URI).
    any_respect = any(c.get("respect_manual_deletions") for c in configs)
    state = sync_state.load() if any_respect else {"synced": {}, "tombstones": {}}

    results, overall_success = [], True
    for idx, config in enumerate(configs):
        try:
            res = sync_club(service, config, state)
            entry = {"config_index": idx, "status": "success", **res["counts"]}
        except Exception as exc:
            described = describe_exception(exc)
            print(f"Feed at index {idx} FAILED: {described}")
            entry = {"config_index": idx, "status": "error", "error": described}
            overall_success = False
        results.append(entry)

    # Saved even when a feed failed, so successful feeds' state is not lost.
    if any_respect:
        sync_state.save(sync_state.DEFAULT_STATE_URI, state)

    print(json.dumps({"overall_success": overall_success, "results": results}))
    if not overall_success:
        raise RuntimeError("One or more club feeds failed to sync.")
    return {"overall_success": True, "results": results}
