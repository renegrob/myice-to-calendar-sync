"""
Run the myice sync locally, reading the Google service-account key from a local
file (see run-local.sh) instead of AWS SSM.

  --dry-run (default)  Compute the real plan and write a report file. Reads the
                       calendar (events.list) but never writes to it.
  --apply              Actually sync. Same code path as the Lambda.
  --purge              Delete events this sync owns on the selected feeds.
                       Itself a dry run unless --confirm is passed.

Every planned entry lists its location and description, so the report can be
reviewed before anything is written; --verbose details the already-ended
entries too.

Feeds are selected with --club and/or --games-only/--trainings-only, in every
mode. Omitting them means every configured feed - except for --purge, which
refuses to target everything unless you say --all-feeds.

Filters apply to all modes. --since and --verbose are dry-run only; --since
because a live sync must never touch the past, --verbose because the other
modes render no report. --confirm/--all-feeds/--purge-scope are purge-only.
"""

import argparse
import json
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
    mode.add_argument("--purge", action="store_true",
                      help="Delete events this sync owns on the selected "
                           "feeds. A dry run unless --confirm is given.")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--games-only", action="store_true")
    which.add_argument("--trainings-only", action="store_true")
    parser.add_argument("--club", metavar="ID",
                        help="Only feeds for this myice club id.")
    parser.add_argument("--all-feeds", action="store_true",
                        help="Purge-only: target every configured feed.")
    parser.add_argument("--confirm", action="store_true",
                        help="Purge-only: actually delete. Without it, --purge "
                             "only reports what it would delete.")
    # default=None, not "future", so the purge-only guard below can tell
    # "not passed" from "passed as future".
    parser.add_argument("--purge-scope", choices=("future", "all"), default=None,
                        help="Purge-only: 'future' (default) leaves events that "
                             "already happened alone; 'all' deletes history too.")
    parser.add_argument("--out", help="Dry-run report path.")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Dry-run only: start date override; allows past events.")
    parser.add_argument("--verbose", action="store_true",
                        help="Dry-run only: also expand already-ended entries.")
    args = parser.parse_args()

    for flag, value in (("--confirm", args.confirm),
                        ("--all-feeds", args.all_feeds),
                        ("--purge-scope", args.purge_scope is not None)):
        if value and not args.purge:
            parser.error(f"{flag} is only valid with --purge.")
    if args.since and (args.apply or args.purge):
        parser.error("--since is dry-run only; a live sync never touches the past.")
    if args.verbose and (args.apply or args.purge):
        parser.error("--verbose is dry-run only; this mode writes no report.")
    if args.since:
        try:
            datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            parser.error(f"--since must be YYYY-MM-DD, got {args.since!r}")

    only = "g" if args.games_only else "p" if args.trainings_only else None

    if args.purge and not (args.club or only or args.all_feeds):
        # The handler's purge payload refuses to guess calendar_id/uid_prefix
        # "to avoid deleting the wrong events"; the same caution applies to the
        # shortest command being the most destructive one.
        parser.error("--purge needs a target: --club and/or --games-only/"
                     "--trainings-only, or --all-feeds to purge every "
                     "configured feed.")

    all_configs = lf.load_configs()
    # Validate the whole config, then filter - not the other way round. The
    # uid_prefix overlap check is cross-feed, so validating a filtered subset
    # would hide a conflict between a selected and an unselected feed. handler()
    # already validates before selecting; this keeps the two consistent.
    lf.validate_configs(all_configs)

    if args.club is not None:
        known = sorted({str(c.get("myice_club")) for c in all_configs})
        if str(args.club) not in known:
            # Exiting 0 having synced nothing reads as success - and on --apply
            # or --purge a typo'd club would look like a clean run.
            parser.error(f"--club {args.club!r} matches no configured feed; "
                         f"configured clubs are: {', '.join(known)}")

    configs = lf.select_configs(all_configs, only, args.club)
    if not configs:
        print("No club feeds match that filter.")
        return

    if args.purge:
        scope = args.purge_scope or "future"
        service = lf.get_calendar_service()
        print(f"PURGE (scope={scope}): "
              f"{'DELETING from' if args.confirm else 'dry run over'} "
              f"{len(configs)} feed(s)\n")
        total = 0
        for cfg in configs:
            res = lf.purge_feed(service, cfg["calendar_id"], cfg["uid_prefix"],
                                scope=scope, dry_run=not args.confirm)
            print(json.dumps(res, default=str))
            total += res.get("deleted" if args.confirm else "would_delete", 0)
        if args.confirm:
            print(f"\nDeleted {total} event(s).")
        else:
            print(f"\nDRY RUN - would delete {total} event(s). "
                  "Re-run with --confirm to actually delete.")
        return

    if args.apply:
        print(f"APPLY: syncing {len(configs)} club feed(s) to live Google Calendars\n")
        lf.handler({}, None, only=only, club=args.club)
        return

    service = lf.get_calendar_service()

    any_respect = any(c.get("respect_manual_deletions") for c in configs)
    state = sync_state.load() if any_respect else {"synced": {}, "tombstones": {}}

    sections, failed = [], False
    for idx, config in enumerate(configs):
        if args.since:
            config = {**config, "_min_date_override": args.since}
        try:
            res = lf.sync_club(service, config, state,
                               allow_past=bool(args.since), plan_only=True)
            sections.append({"index": idx, "config": config, "plan": res["plan"],
                             "counts": res["counts"], "existing": res["existing"]})
        except Exception as exc:
            failed = True
            print(f"[{idx}] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)

    # Print before writing. Computing this report costs a login, a feed fetch and
    # a calendar read per club; a typo in --out must not throw all that away, so
    # stdout gets it first and a failed write is reported rather than fatal.
    report = dry_run.render_report(sections, verbose=args.verbose)
    print(report)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = args.out or f"dry-run-{stamp}.txt"
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report)
    except OSError as exc:
        # The user asked for a file and did not get one, so fail - but the report
        # itself is already on stdout above and is not lost.
        print(f"\nERROR: could not write the report to {path}: {exc}", file=sys.stderr)
        failed = True
    else:
        print(f"\nReport written to {path}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
