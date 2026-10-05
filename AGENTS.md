# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this project is

A single AWS Lambda function that syncs myice.hockey games and trainings into Google Calendar on a daily schedule (AWS Lambda + EventBridge Scheduler). It is myice-only — there is no generic iCal-feed mode. Rather than consuming myice's own public iCal export (which silently omits pending/"Temporär" entries), it logs in and calls the authenticated `playersfilter` endpoint the myice web app itself uses. No database — Google Calendar's `events.import()` (keyed on a namespaced `iCalUID`) is what makes create/update idempotent. See `README.md` for the full user-facing setup and usage guide; this file is about *working on the code itself*.

## Setup

```bash
uv sync
```

Installs runtime deps (`requests`, `google-api-python-client`, `google-auth`, `google-auth-httplib2`) plus the `dev` group (`boto3`, needed locally since it's provided by the Lambda runtime in production and isn't in `requirements.txt`).

## Commands

- **Syntax/import check**: `python3 -m py_compile lambda_function.py`
- **Tests**: `uv run python -m unittest discover -s . -p "test_*.py"` — a real suite (305 tests at last count) across `test_myice.py`, `test_myice_client.py`, `test_duty_parser.py`, `test_duty_entries.py`, `test_prep_entries.py`, `test_sync.py`, `test_sync_state.py`, `test_past_guard.py`, `test_handler.py`, `test_orchestration.py`, `test_dry_run.py`, and `test_run_local.py`. Always run with `uv run`, never a bare `python3`. Use the `test_*.py` naming convention for new tests so this command picks them up.
- **CI**: `.github/workflows/ci.yml` runs on every push and PR — `uv sync --locked`, `py_compile`, the test suite, and `bash -n` over the tracked shell scripts, on Python 3.12 to match the Lambda runtime. It needs no secrets, and **`sync_configs.py` is absent there** because it is gitignored. That is the point of the job, not a gap: the suite must never depend on real club config. If a test starts failing in CI for want of it, fix the test — do not add the file or a secret. (`aws-login.sh` is gitignored too, so only three scripts get syntax-checked.)
- **Deploy**: `./deploy.sh` — packages the Lambda (via `uv pip install`, falling back to `pip`), creates/updates IAM roles from the `*-policy.json`/`*-trust-policy.json` files, and creates/updates both the Lambda function and its EventBridge Scheduler schedule. Idempotent — safe to re-run after any code change. Requires AWS CLI already configured.
  - **`deploy.sh` pre-flight-checks only the Google service-account parameter** (its `SSM_PARAM_NAME`, `/myice-sync/google-service-account`). It contains no reference to the myice-credentials parameter at all — that name comes from each config entry's `myice_credentials_param` and is read only at runtime, inside the Lambda. A deploy therefore succeeds with the myice credentials absent, and the sync fails on its first invocation rather than at deploy time. Both parameters must be stored before the first run (see README section 3). Teaching `deploy.sh` to check it would mean having it read `sync_configs.py` for the name; that is a recorded follow-up, not current behavior — don't document it as if it already happens.
- **Manual invoke** (after deploy): `aws lambda invoke --function-name myice-calendar-sync --region <region> --log-type Tail out.json && cat out.json`
- **Local dry run**: `./run-local.sh` (default) computes a real plan and writes a report without touching Google Calendar; `./run-local.sh --apply` runs the live sync locally. See `docs/dry-run.md`.

## Architecture (several modules, not a single file)

- `lambda_function.py` — the Lambda entry point and myice-specific mapping layer:
  - `handler(event, context)` — branches into one of two modes based on the invocation payload:
    1. **Purge mode** — triggered only by an explicit `{"action": "purge", ...}` payload. Never reachable from the daily schedule (which always invokes with an empty event).
    2. **Sync mode** (default) — loads club configs (`sync_configs.py`'s `CONFIGS`), validates them (`validate_configs()`), and syncs each one via `sync_club()`.
  - `classify()` — maps a record's `health_status` to a `(action, google_status)` pair; see `docs/statuses.md`.
  - `build_feed()` — turns myice records into the `{uid: body}` maps `plan_sync()` consumes, including derived preparation (`prep_body()`) and duty (`duty_bodies()`) entries.
- `myice_client.py` — all HTTP calls to myice.hockey: `login()` and `fetch_records()` against the authenticated `playersfilter` endpoint. Isolated here because it is the part most likely to break when myice.hockey changes anything.
- `duty_parser.py` — `find_duty_lines()`, the accent-/case-insensitive, word-boundary name matching that drives duty entries.
- `calendar_sync.py` — everything Google-Calendar-specific and myice-agnostic: `plan_sync()` (pure — decides create/update/delete/tombstone, no I/O), `execute_plan()` (performs the Google writes), `purge_feed()`, and the past-event guard (`body_has_ended()`). Keeping `plan_sync` pure is what lets dry runs compute a real plan without touching anything.
- `sync_state.py` — loads/saves the `{"synced": ..., "tombstones": ...}` JSON blob used by `respect_manual_deletions`, from a local file or `s3://bucket/key`.
- `dry_run.py` — `render_report()`, turning a computed plan into the human-readable report `run_local.py` prints and writes.
- `run_local.py` / `run-local.sh` — the local CLI: dry run by default, `--apply` for a live sync, `--purge` to delete what this sync owns (dry run unless `--confirm`; needs `--club`/a type filter or `--all-feeds`; defaults to `--purge-scope future`), `--club` to select one club's feeds in any mode, `--since` for a dry-run-only past-date override, `--verbose` to detail already-ended entries too. Mode-specific flags are `parser.error`s, never silent no-ops.
- Config loading: `sync_configs.py` (gitignored, a Python file with a `CONFIGS` list) is the **only** source — there is no SSM/env-var config fallback. `load_configs()` raises if it's missing.

## Things that look redundant but aren't — don't "clean up" without reading the comment first

- **The past-event guard checks `feed_uids`, not just `feed_bodies`, for the deletion pass** (`plan_sync()` in `calendar_sync.py`). `feed_uids` includes every UID currently in the feed, even ones `plan_sync` itself will skip creating/updating because they're in the past; this is what stops the deletion pass from removing an already-synced past event just because it was filtered out of `feed_bodies` this run.
- **`_COMPARE_FIELDS` is a narrow, explicit tuple**, not a full dict comparison of the Google event resource. Google's API adds fields (`etag`, `sequence`, `creator`, `htmlLink`, ...) that this code never sets — comparing the whole object would make every event look "changed" on every run, defeating the unchanged-skip optimization. Only widen this list when adding a new field this code itself writes.
- **`events().import_()` is used deliberately instead of `events().insert()`/`update()`** — `import_()` matches on `iCalUID`, which is what makes re-running the sync idempotent without a database. Don't swap this out.
- **`events.import()` requires an explicit `timeZone` alongside any `dateTime` field.** Omitting it throws `400 "Missing time zone definition"` — this bit us once already (see git history). Any new datetime field written to the Google event body needs the same treatment.
- **Purge defaults to `dry_run=True`** and requires `confirm: true` in the payload to actually delete, plus both `calendar_id` and `uid_prefix` explicitly (no defaults, no guessing). Keep this guarded — it's the one genuinely destructive code path in the project.
- **The past-event guard (`allow_past=False`) is hardcoded for every live sync** — only `run_local.py`'s `--since` flag can set `allow_past=True`, and only for a dry run (`--since` and `--apply` are mutually exclusive, enforced in `run_local.py`). Don't add a way to set `allow_past=True` on a live path; the whole point is that a deployed Lambda run never touches history.
- **`duty_parser.find_duty_lines()` filters out blank/whitespace-only names before compiling patterns.** An empty name would compile to a `\b\b` regex that matches at every word boundary, silently promoting every line in a detail blob to a duty. Tests pin this (`test_duty_parser.py`) — don't remove the filter as "dead code."

## Known limitations (don't try to "fix" these without external changes)

- **No attendee/invitee support.** Google Calendar API requires Domain-Wide Delegation for a service account to invite attendees — a Google Workspace admin feature not available to personal Google accounts. Adding attendees to the event body will fail with `403 forbiddenForServiceAccounts` for anyone using a personal Gmail-based setup (which is the primary use case here).
- **No per-event reminder overrides.** There is no `reminder_minutes`/`reminder_method` config option — every event body sets `"reminders": {"useDefault": True}` unconditionally. A prior version of this project's ancestor did expose a per-event override, and it was removed after live testing showed it silently did nothing: Google reminders are private per authenticated identity, and the Lambda authenticates as the service account, never as the calendar's real owner, so any override it set was invisible to the human who owns the calendar. Don't reintroduce a per-event reminder option without first confirming the auth model has changed (e.g. real OAuth as the user, not a service account). The functional alternative is calendar-level default reminders, set by the owner via Google Calendar's own Settings UI — see README section 2.
- **No native multi-calendar-per-entry support**, by design — duplicate the config entry with a different `calendar_id` instead. This was a deliberate simplicity tradeoff, not an oversight.
- **The myice `playersfilter` endpoint is undocumented and private.** It is what the myice web app itself calls internally, not a published/stable API. It can change shape or move without notice; see `myice_client.py`'s module docstring.

## Conventions

- Python 3.12 (see `pyproject.toml`'s `requires-python`). Use `X | None` (PEP 604) union syntax, not `Optional[X]`.
- Stdlib `zoneinfo`, not `pytz`.
- No linter/formatter config in the repo (no ruff/black config present) — match existing style: double-quoted strings, trailing commas in multi-line calls/literals.
- The Lambda is packaged from several small modules (`lambda_function.py`, `calendar_sync.py`, `myice_client.py`, `duty_parser.py`, `sync_state.py`), each with one clear responsibility — see Architecture above. Keep that separation (myice-specific mapping vs. Google-Calendar-agnostic reconciliation vs. HTTP client vs. name matching) rather than collapsing everything back into one file.

## Secrets & files that must never be committed

- **`sync_configs.py`** — gitignored. Holds real player/club/calendar IDs. `sync_configs_example.py` (placeholder values) is the tracked counterpart — update the example when you change the config schema, but never fill it with real values.
- **Google service account key** — lives only in AWS SSM Parameter Store (`SecureString`), never in the repo.
- **myice.hockey credentials** — lives only in AWS SSM Parameter Store (`SecureString`, `{"username": "...", "password": "..."}`), never in the repo or in `sync_configs.py`.
- **`aws-login.sh`** — gitignored; contains AWS credentials if present locally. Don't reference its contents or assume it exists.
- Before adding any new local-only config/credentials file, add it to `.gitignore` in the same change.

## Deployment target

AWS Lambda (Python 3.12 runtime) triggered by **EventBridge Scheduler** (not legacy EventBridge Rules — the project migrated off `events put-rule` deliberately; `scheduler-trust-policy.json` is the Scheduler execution role's trust policy, separate from the Lambda's own execution role in `trust-policy.json`). Region and schedule cron live in the config block at the top of `deploy.sh`.
