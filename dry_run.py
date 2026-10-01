"""
Renders a sync plan as a readable report.

Dry runs compute the *real* plan - the same plan_sync a live run uses - so the
report cannot drift from what would actually happen. It does read the calendar
(events.list); it never writes.
"""

from datetime import datetime, timezone

from calendar_sync import _COMPARE_FIELDS


def _when(body: dict) -> str:
    start = body.get("start", {})
    raw = start.get("dateTime") or start.get("date") or "?"
    return raw.replace("T", " ")[:16]


def _kind(uid: str) -> str:
    if "-prep-" in uid:
        return " [prep]"
    if "-duty-" in uid:
        return " [duty]"
    return ""


def _field_diff_lines(existing_event: dict, new_body: dict) -> list[str]:
    """
    Per-field old -> new values for an update, using the same _COMPARE_FIELDS
    execute_plan's apply-mode UPDATE DIFF uses, so the dry run and a live run
    never disagree about what counts as a change.
    """
    lines = []
    for field in _COMPARE_FIELDS:
        old, new = existing_event.get(field), new_body.get(field)
        if old != new:
            lines.append(f"      {field}: {old!r} -> {new!r}")
    return lines


def render_report(sections: list[dict]) -> str:
    lines = [
        "myice-to-calendar-sync DRY RUN",
        f"generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "nothing was created, updated or deleted",
        "",
    ]
    for section in sections:
        cfg, plan, counts = section["config"], section["plan"], section["counts"]
        existing = section.get("existing", {})
        kind = {"g": "games", "p": "trainings"}.get(cfg.get("myice_event_type"), "?")
        lines.append("=" * 72)
        lines.append(f"[{section['index']}] club {cfg.get('myice_club')} ({kind})"
                     f" -> {cfg.get('calendar_id')}  prefix {cfg.get('uid_prefix')}")
        lines.append("=" * 72)

        for uid, body in plan["create"]:
            lines.append(f"  CREATE    {_when(body)}  {body.get('summary')}{_kind(uid)}")
        for uid, body, _event_id in plan["update"]:
            lines.append(f"  UPDATE    {_when(body)}  {body.get('summary')}{_kind(uid)}")
            existing_event = existing.get(uid)
            if existing_event is not None:
                lines.extend(_field_diff_lines(existing_event, body))
        for uid, _event_id in plan["delete"]:
            lines.append(f"  DELETE    {uid}{_kind(uid)}")
        for uid in plan["tombstone"]:
            lines.append(f"  TOMBSTONE {uid}{_kind(uid)}")
        for uid in plan["skip_tombstoned"]:
            lines.append(f"  SKIPPED   {uid} (previously deleted by hand){_kind(uid)}")
        if plan["unchanged"]:
            lines.append(f"  {len(plan['unchanged'])} unchanged")

        lines.append("")
        lines.append(f"  {counts.get('created', 0)} created, "
                     f"{counts.get('updated', 0)} updated, "
                     f"{counts.get('deleted', 0)} deleted, "
                     f"{counts.get('unchanged', 0)} unchanged, "
                     f"{counts.get('tombstoned', 0)} tombstoned")
        lines.append("")
    return "\n".join(lines)
