# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this project is

A single AWS Lambda function that syncs one or more iCal feeds into Google Calendar on a daily schedule (AWS Lambda + EventBridge Scheduler). No database — Google Calendar's `events.import()` (keyed on a namespaced `iCalUID`) is what makes create/update idempotent. See `README.md` for the full user-facing setup and usage guide; this file is about *working on the code itself*.

## Setup

```bash
uv sync
```

Installs runtime deps (`icalendar`, `google-api-python-client`, `google-auth`, `google-auth-httplib2`) plus the `dev` group (`boto3`, needed locally since it's provided by the Lambda runtime in production and isn't in `requirements.txt`).

## Commands

- **Syntax/import check**: `python3 -m py_compile lambda_function.py`
- **Tests**: `uv run python -m unittest discover -s . -p "test_*.py"` — note there is currently no test suite in the repo despite this documented command; if you add tests, use the `test_*.py` naming convention so this command picks them up.
- **Deploy**: `./deploy.sh` — packages the Lambda (via `uv pip install`, falling back to `pip`), creates/updates IAM roles from the `*-policy.json`/`*-trust-policy.json` files, and creates/updates both the Lambda function and its EventBridge Scheduler schedule. Idempotent — safe to re-run after any code change. Requires AWS CLI already configured and the Google service account key already stored in SSM (see README section 3) before first run.
- **Manual invoke** (after deploy): `aws lambda invoke --function-name aws-ical-sync --region <region> --log-type Tail out.json && cat out.json`

## Architecture (single file: `lambda_function.py`)

- `handler(event, context)` — entry point. Branches into one of two modes based on the invocation payload:
  1. **Purge mode** — triggered only by an explicit `{"action": "purge", ...}` payload. Never reachable from the daily schedule (which always invokes with an empty event).
  2. **Sync mode** (default) — loads feed configs and syncs each one via `sync_feed()`.
- `sync_feed()` — for one feed: fetches the ical, diffs each event against what's already on the calendar (`event_unchanged()`), and only calls `events().import_()` for genuinely new or changed events. Also deletes calendar events whose UID vanished from the source feed.
- `purge_feed()` — bulk-deletes events by `uid_prefix`/`calendar_id`, with `scope` (`"all"` vs `"future"`) and a `dry_run` default.
- Config loading precedence in `handler()`: `sync_configs.py` (Python file, if present) → `SYNC_CONFIGS_PARAM` (SSM parameter name) → `SYNC_CONFIGS` (JSON in an env var) → `ICAL_URL`/`GOOGLE_CALENDAR_ID`/`UID_PREFIX` single-feed fallback. Preserve this order if you add a new config source.

## Things that look redundant but aren't — don't "clean up" without reading the comment first

- **`current_uids` includes skipped-past-event UIDs** (`sync_feed()`). This is deliberate: it's what stops the deletion pass from removing an already-synced past event just because `SKIP_PAST_EVENTS` caused it to be skipped this run.
- **`_COMPARE_FIELDS` is a narrow, explicit tuple**, not a full dict comparison of the Google event resource. Google's API adds fields (`etag`, `sequence`, `creator`, `htmlLink`, ...) that this code never sets — comparing the whole object would make every event look "changed" on every run, defeating the unchanged-skip optimization. Only widen this list when adding a new field this code itself writes.
- **`events().import_()` is used deliberately instead of `events().insert()`/`update()`** — `import_()` matches on `iCalUID`, which is what makes re-running the sync idempotent without a database. Don't swap this out.
- **`events.import()` requires an explicit `timeZone` alongside any `dateTime` field.** Omitting it throws `400 "Missing time zone definition"` — this bit us once already (see git history). Any new datetime field written to the Google event body needs the same treatment.
- **Purge defaults to `dry_run=True`** and requires `confirm: true` in the payload to actually delete, plus both `calendar_id` and `uid_prefix` explicitly (no defaults, no guessing). Keep this guarded — it's the one genuinely destructive code path in the project.

## Known limitations (don't try to "fix" these without external changes)

- **No attendee/invitee support.** Google Calendar API requires Domain-Wide Delegation for a service account to invite attendees — a Google Workspace admin feature not available to personal Google accounts. Adding attendees to the event body will fail with `403 forbiddenForServiceAccounts` for anyone using a personal Gmail-based setup (which is the primary use case here).
- **No native multi-calendar-per-feed support**, by design — duplicate the config entry with a different `calendar_id` instead. This was a deliberate simplicity tradeoff, not an oversight.

## Conventions

- Python 3.12 (see `pyproject.toml`'s `requires-python`). Use `X | None` (PEP 604) union syntax, not `Optional[X]`.
- Stdlib `zoneinfo`, not `pytz`.
- No linter/formatter config in the repo (no ruff/black config present) — match existing style: double-quoted strings, trailing commas in multi-line calls/literals.
- Keep this as a single-file Lambda (`lambda_function.py`). Don't split into multiple modules/a framework unless the project's scope changes materially — the whole design optimizes for "one file, easy to reason about, cheap to redeploy."

## Secrets & files that must never be committed

- **`sync_configs.py`** — gitignored. Holds real feed URLs and calendar IDs. `sync_configs_example.py` (placeholder values) is the tracked counterpart — update the example when you change the config schema, but never fill it with real values.
- **Google service account key** — lives only in AWS SSM Parameter Store (`SecureString`), never in the repo.
- **`aws-login.sh`** — gitignored; contains AWS credentials if present locally. Don't reference its contents or assume it exists.
- Before adding any new local-only config/credentials file, add it to `.gitignore` in the same change.

## Deployment target

AWS Lambda (Python 3.12 runtime) triggered by **EventBridge Scheduler** (not legacy EventBridge Rules — the project migrated off `events put-rule` deliberately; `scheduler-trust-policy.json` is the Scheduler execution role's trust policy, separate from the Lambda's own execution role in `trust-policy.json`). Region and schedule cron live in the config block at the top of `deploy.sh`.
