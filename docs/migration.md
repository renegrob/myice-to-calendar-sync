# Migrating from an older iCal-based sync

Read this before your first `--apply` if you have **ever synced this same Google Calendar from an iCal-based project before** — in particular the `myice-access` branch of the `aws-ical-sync` project, this project's ancestor.

## Why this matters

`SOURCE_TAG` is `myice-calendar-sync`. It is written to every event's
`extendedProperties.private.source`, and the sync uses it to decide which
calendar events it owns. If you previously ran the `myice-access` branch of
`aws-ical-sync` against a calendar, those events are tagged `aws-ical-sync`
and **this sync cannot see them**: it will create duplicates and will never
clean the originals up. Before your first `--apply`, either purge them with
the old project's purge mode, or delete them by hand.

Concretely: `list_existing_synced_events` (in `calendar_sync.py`) only matches events whose `extendedProperties.private.source` equals `myice-calendar-sync` *and* whose `iCalUID` starts with the configured `uid_prefix`. Events tagged `aws-ical-sync` fail that filter entirely — this sync treats them as if they don't exist. Every record that was already on your calendar from the old sync will be created again under the new sync's own UID, and the old copies will sit there forever since nothing ever looks at them again.

## What to do first

**Before running `./run-local.sh --apply` for the first time against a calendar that has ever been synced by the old project:**

1. **Check what's actually tagged.** Open the calendar in Google Calendar and look for duplicate-looking games/trainings, or inspect events via the API for `extendedProperties.private.source == "aws-ical-sync"`.
2. **Purge the old events**, using the old project's own purge mode (same shape this project also uses — see `handler()` in `lambda_function.py` for the payload fields: `action`, `calendar_id`, `uid_prefix`, `scope`, `confirm`). From the old project's checkout:

   ```bash
   # Dry run first - reports what would be deleted, deletes nothing
   aws lambda invoke \
     --function-name aws-ical-sync \
     --region eu-central-2 \
     --cli-binary-format raw-in-base64-out \
     --payload '{"action":"purge","calendar_id":"primary","uid_prefix":"<old-uid-prefix>","scope":"all"}' \
     out.json && cat out.json
   ```

   Once the `matched`/`would_delete` counts look right, confirm the delete:

   ```bash
   aws lambda invoke \
     --function-name aws-ical-sync \
     --region eu-central-2 \
     --cli-binary-format raw-in-base64-out \
     --payload '{"action":"purge","calendar_id":"primary","uid_prefix":"<old-uid-prefix>","scope":"all","confirm":true}' \
     out.json && cat out.json
   ```

   Replace `<old-uid-prefix>` with whatever `uid_prefix` the old `sync_configs.py` used for that calendar (its default was `"ical-"` if never overridden). If you no longer have the old Lambda deployed, delete the tagged events by hand in Google Calendar instead — there is no bulk "delete by extendedProperty" action in the Calendar UI, so this means finding and removing them individually, which is the reason the purge-mode route above is strongly preferred when available.

3. **Only after the old events are gone**, run this project's own dry run to confirm you get a clean plan with no unexpected creates:

   ```bash
   ./run-local.sh
   ```

   Inspect the generated report. Every game and training you expect should appear as a single `CREATE` (first run) or `UNCHANGED`/`UPDATE` (if some already exist with this project's own tag) — not a `CREATE` for something you know is already on the calendar from the old sync.

4. Once the dry-run report looks right, run `./run-local.sh --apply`.

## If you skip this

Nothing will error. The new sync will happily create a full new set of events alongside the orphaned old ones, you'll see every game and training twice on your calendar, and the old copies will never be cleaned up by either project (the old Lambda is presumably no longer being invoked, and the new one can't see events it didn't tag). The only way out at that point is the same purge/manual-delete step above, done after the fact.
