"""
Run the ical sync locally, reading the Google service-account key from a local
file (see run-local.sh) instead of AWS SSM.

  --preview (default)  Read-only: fetch each feed and print what *would* sync.
                       Never touches Google Calendar.
  --apply              Actually run the sync (creates/updates/DELETES events on
                       the live calendars). Same code path as the Lambda.
"""

import argparse

import lambda_function as lf


def preview() -> None:
    from sync_configs import CONFIGS

    print(f"PREVIEW (read-only): {len(CONFIGS)} feed(s)\n")
    for idx, config in enumerate(CONFIGS):
        url = config.get("ical_url")
        prefix = config.get("uid_prefix", "ical-")
        summary_format = config.get("summary_format", "{summary}")
        color_id = config.get("color_id")
        print(f"[{idx}] {prefix}  {url}")
        try:
            cal = lf.fetch_ical(url)
        except Exception as e:
            print(f"    FETCH FAILED: {e}\n")
            continue

        total = would_sync = 0
        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            total += 1
            uid = lf.normalize_uid(str(component.get("uid")), prefix)
            body = lf.event_to_google_body(component, uid, summary_format, color_id)
            if body is None:
                continue  # skipped as a past event
            would_sync += 1
            start = body.get("start", {})
            when = start.get("dateTime") or start.get("date") or "?"
            print(f"    + {when}  {body.get('summary')}")
        print(f"    {total} event(s) in feed, {would_sync} would sync "
              f"({total - would_sync} skipped as past)\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ical sync locally.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--preview", action="store_true", help="Read-only preview (default).")
    group.add_argument("--apply", action="store_true", help="Actually sync to Google Calendar.")
    args = parser.parse_args()

    if args.apply:
        print("APPLY: syncing to live Google Calendars via lambda_function.handler\n")
        lf.handler({}, None)
    else:
        preview()


if __name__ == "__main__":
    main()
