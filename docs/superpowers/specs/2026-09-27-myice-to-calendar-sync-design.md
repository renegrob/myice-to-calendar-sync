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

Beyond the port, this project adds six capabilities the source branch did not
have. Each is specified in its own section below:

1. Per-club configuration, with club-specific colour, templates, and target
   calendar
2. The full detail blob in the event description
3. Duty entries — an extra calendar event per detail line that names you
4. Status-driven removal (sick / injured / excused) and request styling
5. A dry-run mode that writes a report file instead of calling Google
6. Games-only / trainings-only filters, and past-date dry runs behind a guard
   that keeps live syncs out of the past

Not in scope: sharing code with `aws-ical-sync` via a package or submodule. The
two projects will drift on the common Google Calendar plumbing and that is
accepted.

## Architecture

Four modules, split by what each one knows about:

| File | Responsibility | Knows about |
|---|---|---|
| `myice_client.py` | Login, `playersfilter` fetch | HTTP, myice.hockey. Nothing about Google. |
| `duty_parser.py` | Match duty names in a detail blob, return matched lines | Plain text. Pure, no I/O. |
| `calendar_sync.py` | `plan_sync`, execute, `list_existing_synced_events`, `purge_feed` | Google Calendar. Nothing about myice. |
| `dry_run.py` | Render a plan as a human-readable report file | Plans and text. No network. |
| `lambda_function.py` | `handler`, config validation, record→body mapping | All of the above; wires them together. |
| `sync_state.py` | S3/local state load+save | Storage only. Unchanged from `main`. |

The source branch kept all of this in one 664-line file. The split exists
because `myice_client.py` is the part that talks to an undocumented private
endpoint and is the part that will break when myice.hockey changes. Isolating it
makes that failure legible and lets it be tested without mocking Google.
`duty_parser.py` and `dry_run.py` are likewise pure, which is what makes the
trickiest logic here testable without touching either service.

### Data flow

```
handler
  └─ validate config (required fields, duplicate calendar_id+uid_prefix guard)
  └─ load state (only if some feed sets respect_manual_deletions)
  └─ for each club feed (optionally filtered to games or trainings):
       myice_client.login(...)        -> authenticated session
       myice_client.fetch_games(...)  -> list[record]
       for each record:
         classify by health_status    -> sync / sync-as-request / remove
         record_to_google_body(...)   -> the event body
         duty_parser.find_duties(...) -> extra duty event bodies
       calendar_sync.plan_sync(...)   -> plan (pure, no I/O)
       if dry run: dry_run.render(...) -> report file, and stop
       else:       calendar_sync.execute(...) -> create/update/delete
  └─ save state (if any feed opted in, apply mode only)
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

## Configuration: one entry per club

One login can be connected to several clubs, and club-specific settings —
colour, summary template, duty names, target calendar — differ between them.
The config model is therefore **one `CONFIGS` entry per club per event type**.
Credentials are shared across entries (same login), everything else is per
entry.

The target calendar may be the same for several clubs or different per club.
Both work. When two entries share a `calendar_id` they **must** have different
`uid_prefix` values, or each will delete the other's events; `handler` already
refuses to run on a duplicate `calendar_id` + `uid_prefix` pair.

Per-entry keys, required unless noted:

- `calendar_id` — target Google Calendar
- `myice_login_url`, `myice_username_field`, `myice_password_field`
- `myice_login_extra_fields` (optional) — e.g. a CSRF token field
- `myice_credentials_param` — SSM SecureString holding `{"username", "password"}`
- `myice_filter_url`, `myice_player_id`, `myice_season`, `myice_club`
- `myice_event_type` — `"g"` games, `"p"` trainings
- `myice_min_date`, `myice_max_date` — `YYYY-MM-DD`, updated once per season

Optional, all per club:

- `uid_prefix` (default `myice-`), `summary_format`, `color_id`, `timezone`
- `request_summary_format`, `request_color_id` — see Status model
- `duty_names`, `duty_summary_format`, `duty_color_id` — see Duty entries
- `respect_manual_deletions`

`handler` validates required keys for **every** entry before syncing any of
them, so a missing field fails immediately rather than partway through.

## Status model

`health_status` drives whether an event is synced, styled differently, or
removed. The codes come from the app's own `#health_status` select, plus `3`,
which is system-assigned rather than user-selectable:

| Code | Label | Action |
|---|---|---|
| `1` | Gesund | Sync normally. Google status `confirmed`. |
| `3` | Temporär | Sync as a **request**: `request_summary_format` (default `"❓ {summary}"`) and `request_color_id` if set. Google status `tentative`. |
| `6` | Entschuldigt | **Remove** from calendar. |
| `8` | Krank | **Remove** from calendar. |
| `9` | Verletzt | **Remove** from calendar. |
| anything else | unknown | Sync normally as `confirmed`, and log the unrecognised code so a new status shows up in CloudWatch rather than silently changing behaviour. |

"Remove" is implemented by omitting the record from **both** `feed_uids` and
`feed_bodies`. `plan_sync` then sees a UID on the calendar that is no longer in
the feed and deletes it — no new code path, and it composes correctly with
tombstones. Removing a record also removes every duty entry derived from it: if
you are sick, you are not on the timekeeping bench either.

The mapping lives in a module-level dict so an added status is a one-line
change, not a refactor.

## Duty entries

Games and trainings carry a free-text detail blob. The whole blob goes into the
calendar event's description, unchanged — that requirement is independent of
everything below.

Beyond that, the blob often assigns jobs to named people:

```
Coach: [First Name] [Last Name] Tel. [Phone Number]
Betreuer: [First Name] [Last Name] Tel. [Phone Number]
2 Pack Farmer/Riegel bringt mit: Fam. [Family Name A]

PLO
Reporter: [First Name] [Last Name] ([Name])
Speaker: [First Name] [Last Name]
Zeit: Fam. [Family Name B]
Strafbank: [Family Name A] / [Family Name C]
Kuchenbuffet: [Family Name D] / [Family Name E]
```

When a line mentions one of your names, that job gets its **own** calendar
entry, so it appears as a distinct commitment rather than being buried in a
description you will not re-read.

**Configuration.** `duty_names` is a flat per-club list of strings, e.g.
`["César", "Smith"]`. Surnames and full names both work; the list is whatever
you want matched.

**Matching.** Per line, case-insensitive and accent-insensitive: both line and
name are Unicode-normalised (NFKD, combining marks stripped) and casefolded, so
`César` matches `CESAR` and `cesar`. A name matches only on word boundaries, so
`Smith` matches `Fam. Smith`, `Smith / Green`, and `smith, jane`, but not
`Smithson`. Blank lines and lines that are pure section headers (no separator,
e.g. `PLO`) cannot match because they contain no name.

**Known limitation, accepted.** A surname shared with another family produces a
false-positive duty entry. Deleting the stray entry by hand plus
`respect_manual_deletions` is the mitigation; making the match narrower would
silently miss real duties, which is the worse failure.

**The generated entry.** For each matched line:

- Summary: `duty_summary_format` applied to the line, default `"{line}"`.
  `{summary}` is also available for the parent event's title.
- Description: the **full** detail blob, so the context is there.
- Start/end: identical to the parent event. A duty usually starts earlier in
  practice, but guessing an offset would be wrong more often than right; the
  parent's time is at least correct and legible.
- `colorId`: `duty_color_id` if set, else the club's `color_id`.
- Location: the parent's.
- UID: `{uid_prefix}duty-{id_game}-{sha1(normalised line)[:8]}`. Hashing the
  line rather than using its index keeps the UID stable when unrelated lines are
  added or reordered. If the line itself is edited, the old entry is deleted and
  a new one created — correct, if slightly noisy.

Duty entries are ordinary events in the same feed and calendar, so they flow
through `plan_sync` with everything else and need no special handling for
updates, deletions, or state.

## Dry run, filters, and the past

All three are `run-local.sh` flags. The deployed Lambda has no dry-run or filter
mode; these are inspection and testing tools.

- `--dry-run` — computes the full plan and writes a human-readable report
  instead of calling Google. Nothing is created, updated, or deleted, and state
  is **not** saved. Default output `dry-run-<UTC timestamp>.txt` in the project
  root (gitignored), overridable with `--out PATH`. The report groups by club
  feed and lists every create / update / delete / unchanged / tombstoned event
  with date, time, summary, and — for updates — the field-level diff. Duty
  entries are marked as such. A summary count per feed closes each section.
- `--games-only` / `--trainings-only` — restrict the run to config entries whose
  `myice_event_type` is `g` or `p`. Mutually exclusive.
- `--since YYYY-MM-DD` — **dry-run only.** Overrides `myice_min_date` and
  disables past-event skipping, so you can replay a past week and inspect what
  the sync would have done. Combining it with `--apply` is refused with an
  error, not silently ignored.

### The past-event guard

A live sync never touches an event that has already ended. Concretely, in apply
mode:

- Past events are never created or updated (existing `SKIP_PAST_EVENTS`
  behaviour).
- Past events are never **deleted**, including via the feed-removal pass. This
  is a change from `main`, where a calendar event whose UID left the feed was
  deleted regardless of age. Here, history stays as it was recorded.

The guard sits in `plan_sync` so it is covered by pure tests, and it is keyed
off an explicit `allow_past` parameter that only the dry-run path can set. That
makes "live never touches the past" a property of the planner rather than a
convention the callers have to remember.

To keep one mechanism rather than two, `main`'s `SKIP_PAST_EVENTS` check inside
the record→body mapper is **removed**; `record_to_google_body` becomes a pure
mapper that always returns a body, and every past-event decision is made in
`plan_sync`. The `feed_uids` / `feed_bodies` split that existed solely to stop
past events being deleted therefore collapses: `plan_sync` receives all bodies
and decides. This simplifies the call sites and puts the entire policy in one
tested place.

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
- **Unknown `health_status`** — syncs as `confirmed` and logs the code, so an
  added status is visible in CloudWatch instead of silently changing behaviour.
- **`--since` with `--apply`** — refused with an error. A flag that exists to
  reach into the past must never be combined with a mode that writes.

## Testing

Ported from `main` (pure, apply unchanged): the `plan_sync` tests in
`test_sync.py`, and `test_sync_state.py`.

`test_myice.py` — record mapping and client:

- `health_status` `1` → `confirmed`, `3` → `tentative` + request template and
  colour, unknown code → `confirmed` and logged
- `health_status` `6`/`8`/`9` → excluded from `feed_uids` and `feed_bodies`, and
  the resulting plan deletes the calendar event
- removing a record also removes its duty entries
- full detail blob lands in the description verbatim
- `meeting` of `"00:00"`/`"00:00:00"` suppressed
- invalid `summary_format` falls back to the raw summary
- `myice_client.login` raises when `mih_v3_token` is absent from the jar
- `myice_client.fetch_games` sends the expected filter parameters

`test_duty_parser.py` — the example blob from this spec is the primary fixture:

- matches `Speaker: John Doe`, `Zeit: Fam. Smith`, `Strafbank: Smith / Green`,
  `timekeeper: smith, jane`
- accent-insensitivity: `César` matches `CESAR`, `cesar`, `César`
- word boundaries: `Smith` does not match `Smithson`
- a name absent from the blob produces no entries; `PLO` and blank lines never
  match
- multiple matched lines produce multiple entries with distinct, stable UIDs
- the same blob parsed twice produces identical UIDs; reordering unrelated lines
  does not change them

`test_past_guard.py` — `plan_sync` with `allow_past=False` never emits a past
event in `create`, `update`, or `delete`; with `allow_past=True` it does.

`test_dry_run.py` — rendering a known plan produces a report containing each
action, and the dry-run path performs no Google calls (asserted against a
service stub that raises on any use).

All against stubs. No test makes a live myice.hockey or Google call.

## Documentation plan

Written **after** the implementation works, per the agreed sequencing — the
README will not describe a feature until its tests pass.

- `README.md` — setup, per-club configuration, deployment, and a short section
  per feature linking into `docs/`.
- `docs/configuration.md` — the per-club model: one entry per club per event
  type, shared credentials, when calendars may be shared and the `uid_prefix`
  rule, full key reference.
- `docs/statuses.md` — the `health_status` table, what "request" means, and why
  sick/injured/excused remove rather than skip.
- `docs/duty-entries.md` — the detail blob, `duty_names`, matching rules, the
  false-positive caveat, and worked examples from the spec's sample blob.
- `docs/dry-run.md` — flags, report format, `--since` and why it cannot be
  combined with `--apply`.
- `docs/capturing-ids.md` — DevTools walkthrough for `player_id`, `season`,
  `club`, and the login field names.
- `docs/migration.md` — the `SOURCE_TAG` warning.

## Sequence

1. `git clone` `aws-ical-sync` → `/home/rg/code/python/myice-to-calendar-sync`,
   remove `origin`, fetch `myice-access` for provenance. *(done)*
2. Commit this spec. *(done; revised here)*
3. Port: module split, myice source on top of `plan_sync`, iCal removal.
4. Rename deployment identity throughout; fix `lambda-policy.json` and
   `pyproject.toml`.
5. Build the new features: status model, duty entries, dry run, filters,
   past-event guard — each with its tests.
6. Run tests, linters, and a real `run-local.sh --dry-run` against live
   myice.hockey data; inspect the report.
7. Write `README.md` and the `docs/` pages.
8. Only after the above verifies: delete `myice-access` from `aws-ical-sync`.

The GitHub remote (`renegrob/myice-to-calendar-sync`, public) is **deferred**.
The user has asked that nothing be pushed yet. No remote is configured and no
push happens without an explicit go-ahead.

`aws-ical-sync` is not modified at any point before step 7.
