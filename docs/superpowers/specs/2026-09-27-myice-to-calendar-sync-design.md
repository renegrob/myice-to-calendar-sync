# myice-to-calendar-sync — Design

Date: 2026-09-27
Status: Approved, not yet implemented

## Purpose

Sync an athlete's myice.hockey schedule to Google Calendar, including
pending/unconfirmed entries.

myice.hockey publishes an ical export at `/api/players/ical/...`, but it only
includes entries whose `health_status` is `"1"` (Gesund/confirmed). Entries with
`health_status` `"3"` (Temporär/awaiting your response) are silently dropped, so
tentative-but-real invites never reach a calendar synced from that feed. The
authenticated `/api/players/playersfilter` endpoint the myice.hockey webapp
itself calls returns the complete list.

This project logs in and calls that endpoint directly. The trade-off is
deliberate: it requires storing real login credentials (in SSM, never in the
repo) and depends on an undocumented private endpoint that can change shape
without notice. It is more fragile than the public ical link, but complete
where the ical link is not.

## Origin

Extracted from the `myice-access` branch of `aws-ical-sync`
(commit `0c245d2`, "Implement my-ice access").

That branch forked at `1d0b236` and was never merged. `main` advanced eight
commits past that fork point, gaining sync state, `respect_manual_deletions`,
local run tooling, and an S3 state packaging fix. This project therefore starts
from `main`'s HEAD (`e68a80e`) and **re-applies** the myice work on top, rather
than continuing the stale branch. Full shared history is preserved; the
`myice-access` branch is fetched into this repo so `0c245d2` stays reachable as
the provenance of the ported code.

### Defects in the source branch that this port must close

These were found while reviewing `0c245d2` and are not optional:

1. **`requests` is imported but never declared** in `pyproject.toml`. It
   resolves today only because `google-api-python-client` pulls it in
   transitively. It must become a direct dependency.
2. **`lambda-policy.json` was never updated.** It grants `ssm:GetParameter` on
   the Google service-account parameter only, so `get_myice_credentials` would
   fail with AccessDenied on Lambda. The myice credentials parameter must be
   added to the policy. The deployed code path has almost certainly never run.
3. **Five of the seven files in the commit are `chmod`-only noise**
   (100644↔100755 flips on `deploy.sh`, `lambda-policy.json`, `pyproject.toml`,
   `trust-policy.json`, `uv.lock`). None of them carry content changes. Do not
   mistake them for real edits.
4. **No documentation and no tests.** `sync_configs_example.py` refers readers to
   a README section named "myice.hockey JSON source" that does not exist, and no
   test covers any myice code.

## Scope

myice.hockey is the **only** source type. The generic iCal path stays in
`aws-ical-sync`, which continues to own that case. This project deletes
`fetch_ical`, `event_to_google_body`, the `icalendar` dependency, the `ical_url`
config field, and the `source_type` discriminator (with one source there is
nothing to discriminate).

Not in scope: sharing code with `aws-ical-sync` via a package or submodule. The
two projects will drift on the common Google Calendar plumbing and that is
accepted.

## Architecture

Four modules, split by what each one knows about:

| File | Responsibility | Knows about |
|---|---|---|
| `myice_client.py` | Login, `playersfilter` fetch | HTTP, myice.hockey. Nothing about Google. |
| `calendar_sync.py` | `plan_sync`, reconcile/execute, `list_existing_synced_events`, `purge_feed` | Google Calendar. Nothing about myice. |
| `lambda_function.py` | `handler`, config validation, record→body mapping | Both; wires them together. |
| `sync_state.py` | S3/local state load+save | Storage only. Unchanged from `main`. |

The source branch kept all of this in one 664-line file. The split exists
because `myice_client.py` is the part that talks to an undocumented private
endpoint and is the part that will break when myice.hockey changes. Isolating it
makes that failure legible and lets it be tested without mocking Google.

### Data flow

```
handler
  └─ validate config (required fields, duplicate calendar_id+uid_prefix guard)
  └─ load state (only if some feed sets respect_manual_deletions)
  └─ for each feed:
       myice_client.login(...)        -> authenticated session
       myice_client.fetch_games(...)  -> list[record]
       record_to_google_body(...)     -> feed_uids, feed_bodies
       calendar_sync.plan_sync(...)   -> plan (pure, no I/O)
       calendar_sync.execute(...)     -> create/update/delete against Google
  └─ save state (if any feed opted in)
```

### The reconcile port

The branch's `_reconcile_events(service, calendar_id, uid_prefix, events)`
performed I/O inline. It is **dropped, not ported**. `main` independently
extracted a *pure* `plan_sync(feed_uids, feed_bodies, existing, state,
respect_deletes)` that returns action lists and does no I/O, with execution kept
in the caller. `main`'s design is better and is already covered by tests, so the
myice fetch is rewired to feed `plan_sync`.

A consequence worth stating: this project inherits `respect_manual_deletions`
and tombstones on day one. That matters more here than for iCal, because
pending/tentative hockey events are exactly the kind a user deletes by hand and
does not want resurrected on the next run.

## Deployment identity

`aws-ical-sync` is already deployed. Every name is changed so the two
deployments cannot collide:

| Thing | aws-ical-sync | this project |
|---|---|---|
| Lambda function | `aws-ical-sync` | `myice-calendar-sync` |
| IAM role | `aws-ical-sync-role` | `myice-calendar-sync-role` |
| Google SA param | `/ical-sync/google-service-account` | `/myice-sync/google-service-account` |
| myice creds param | — | `/myice-sync/myice-credentials` |
| S3 state prefix | `aws-ical-sync/` | `myice-calendar-sync/` |
| `SOURCE_TAG` | `aws-ical-sync` | `myice-calendar-sync` |

The Google service-account key must be re-created under the new SSM path; it is
not shared.

### SOURCE_TAG is load-bearing

`SOURCE_TAG` is written to `extendedProperties.private.source` on every synced
event, and `list_existing_synced_events` filters on it to decide which calendar
events this sync owns. Changing it means the new Lambda cannot see events the
old one created: it will treat them as absent and create duplicates, and it will
never clean them up.

This is documented in the README as a migration warning rather than handled in
code. If events tagged `aws-ical-sync` already exist on a target calendar, purge
them with the old project's purge mode before the first run of this one.

## Configuration

Per-feed config keys, all required unless noted:

- `calendar_id` — target Google Calendar
- `myice_login_url`, `myice_username_field`, `myice_password_field`
- `myice_login_extra_fields` (optional) — e.g. a CSRF token field
- `myice_credentials_param` — SSM SecureString holding `{"username", "password"}`
- `myice_filter_url`, `myice_player_id`, `myice_event_type` (`"g"` games /
  `"p"` practices), `myice_season`, `myice_club`
- `myice_min_date`, `myice_max_date` — `YYYY-MM-DD`, updated once per season
- Optional: `uid_prefix` (default `myice-`), `summary_format`, `color_id`,
  `timezone`, `respect_manual_deletions`

A player can belong to more than one club; use a separate feed entry per
club/team. `handler` validates required keys for every feed **before** syncing
any of them, so a missing field fails immediately rather than partway through.

## Error handling

- **Login failure** — `myice_client` raises if the `mih_v3_token` cookie is
  absent from the jar after the login POST. Cookie presence is the success
  signal rather than sniffing the response body, which varies by locale and
  theme; a wrong password never produces the cookie.
- **Per-feed isolation** — a feed that raises is recorded as failed and the run
  continues with the remaining feeds. State from feeds that succeeded is still
  persisted. The handler raises at the end if any feed failed, which surfaces via
  the existing CloudWatch alarm → SNS email path.
- **Malformed `summary_format`** — logs a warning and falls back to the raw
  summary rather than failing the feed.
- **Unknown `health_status`** — defaults to `confirmed`.

## Testing

Ported from `main` (they are pure and apply unchanged): the `plan_sync` tests in
`test_sync.py`, and `test_sync_state.py`.

New, in `test_myice.py`:

- `record_to_google_body`: `health_status` `"3"` → `tentative`, `"1"` →
  `confirmed`, unknown → `confirmed`
- `record_to_google_body`: `meeting` of `"00:00"`/`"00:00:00"` is suppressed from
  the description
- `record_to_google_body`: past events return `None` when `SKIP_PAST_EVENTS`
- `record_to_google_body`: invalid `summary_format` falls back to raw summary
- `myice_client.login`: raises when `mih_v3_token` is missing from the jar
- `myice_client.fetch_games`: sends the expected filter parameters

All against stubbed sessions. No test makes a live myice.hockey call.

## Sequence

1. `git clone` `aws-ical-sync` → `/home/rg/code/python/myice-to-calendar-sync`,
   remove `origin`, fetch `myice-access` for provenance. *(done)*
2. Commit this spec.
3. Port: module split, myice source on top of `plan_sync`, iCal removal.
4. Rename deployment identity throughout; fix `lambda-policy.json` and
   `pyproject.toml`.
5. Rewrite `README.md` for this project, including how to capture
   `player_id`/`season`/`club`/login field names from DevTools, and the
   `SOURCE_TAG` migration warning.
6. Run tests, linters, and a `run-local.sh` dry run.
7. Only after the above verifies: delete `myice-access` from `aws-ical-sync`.

The GitHub remote (`renegrob/myice-to-calendar-sync`, public) is **deferred**.
The user has asked that nothing be pushed yet. No remote is configured and no
push happens without an explicit go-ahead.

`aws-ical-sync` is not modified at any point before step 7.
