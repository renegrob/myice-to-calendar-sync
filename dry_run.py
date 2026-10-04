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


_DETAIL_FIELDS = ("location", "description")
_INDENT = "      "


def _detail_lines(body: dict) -> list[str]:
    """
    The fields a human checks before letting --apply write: where the event is
    and what it says. Absent values are spelled "(none)" rather than omitted -
    practice records routinely carry place="", and a missing line is
    indistinguishable from the report not showing locations at all.

    Multi-line descriptions are indented to hang under their label.
    """
    lines = []
    for field in _DETAIL_FIELDS:
        value = str(body.get(field) or "").strip()
        label = f"{_INDENT}{field}: "
        if not value:
            lines.append(f"{label}(none)")
            continue
        head, *rest = value.splitlines()
        lines.append(f"{label}{head}")
        lines.extend(" " * len(label) + line for line in rest)
    return lines


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


def render_report(sections: list[dict], verbose: bool = False) -> str:
    """
    Render the plan. `verbose` also expands the already-ended entries, which
    outnumber the writes several times over and so stay terse by default.
    """
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
            lines.extend(_detail_lines(body))
        for uid, body, _event_id in plan["update"]:
            lines.append(f"  UPDATE    {_when(body)}  {body.get('summary')}{_kind(uid)}")
            lines.extend(_detail_lines(body))
            existing_event = existing.get(uid)
            if existing_event is not None:
                lines.extend(_field_diff_lines(existing_event, body))
        for uid, _event_id in plan["delete"]:
            lines.append(f"  DELETE    {uid}{_kind(uid)}")
        for uid in plan["tombstone"]:
            lines.append(f"  TOMBSTONE {uid}{_kind(uid)}")
        for uid in plan["skip_tombstoned"]:
            lines.append(f"  SKIPPED   {uid} (previously deleted by hand){_kind(uid)}")
        for uid, body in plan.get("skipped_past", []):
            lines.append(f"  SKIPPED   {_when(body)}  {body.get('summary')}"
                         f" (already ended){_kind(uid)}")
            if verbose:
                lines.extend(_detail_lines(body))
        if plan["unchanged"]:
            lines.append(f"  {len(plan['unchanged'])} unchanged")

        lines.append("")
        lines.append(f"  {counts.get('created', 0)} created, "
                     f"{counts.get('updated', 0)} updated, "
                     f"{counts.get('deleted', 0)} deleted, "
                     f"{counts.get('unchanged', 0)} unchanged, "
                     f"{counts.get('tombstoned', 0)} tombstoned, "
                     f"{counts.get('skipped_past', 0)} past")
        lines.append("")
    return "\n".join(lines)
