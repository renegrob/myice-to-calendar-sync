"""
Run the myice sync locally, reading the Google service-account key from a local
file (see run-local.sh) instead of AWS SSM.

  --dry-run (default)  Compute the real plan and write a report file. Reads the
                       calendar (events.list) but never writes to it.
  --apply              Actually sync. Same code path as the Lambda.

Filters and --since apply to dry runs; --since is refused with --apply, because
a live sync must never touch the past.
"""

import argparse
import sys
from datetime import datetime, timezone

import dry_run
import lambda_function as lf
import sync_state


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the myice sync locally.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Plan only and write a report (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Actually sync to Google Calendar.")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--games-only", action="store_true")
    which.add_argument("--trainings-only", action="store_true")
    parser.add_argument("--out", help="Dry-run report path.")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Dry-run only: start date override; allows past events.")
    args = parser.parse_args()

    if args.since and args.apply:
        parser.error("--since is dry-run only; a live sync never touches the past.")
    if args.since:
        try:
            datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            parser.error(f"--since must be YYYY-MM-DD, got {args.since!r}")

    only = "g" if args.games_only else "p" if args.trainings_only else None
    configs = lf.select_configs(lf.load_configs(), only)
    lf.validate_configs(configs)
    if not configs:
        print("No club feeds match that filter.")
        return

    service = lf.get_calendar_service()

    if args.apply:
        print(f"APPLY: syncing {len(configs)} club feed(s) to live Google Calendars\n")
        lf.handler({}, None)
        return

    any_respect = any(c.get("respect_manual_deletions") for c in configs)
    state = sync_state.load() if any_respect else {"synced": {}, "tombstones": {}}

    sections, failed = [], False
    for idx, config in enumerate(configs):
        if args.since:
            config = {**config, "_min_date_override": args.since}
        try:
            res = lf.sync_club(service, config, state,
                               allow_past=bool(args.since), plan_only=True)
            sections.append({"index": idx, "config": config,
                             "plan": res["plan"], "counts": res["counts"]})
        except Exception as exc:
            failed = True
            print(f"[{idx}] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)

    report = dry_run.render_report(sections)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = args.out or f"dry-run-{stamp}.txt"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report)

    print(report)
    print(f"\nReport written to {path}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
