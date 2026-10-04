# Configuration (`sync_configs.py`)

Copy `sync_configs_example.py` to `sync_configs.py` and edit it. `sync_configs.py` is gitignored — it holds your real player ID, club IDs and calendar IDs, but no secrets (credentials live in SSM, not in this file).

## One entry per club per event type

`sync_configs.py` exports a `CONFIGS` list. Each **entry** is one club, one event type (games or trainings). A myice login can be linked to several clubs, and games and trainings are fetched with separate API calls, so:

- One player, one club, games only → 1 entry.
- One player, one club, games **and** trainings → 2 entries.
- One player, two clubs, both event types → 4 entries.

Login credentials are shared across all of a player's entries (one `myice_credentials_param`); everything else — target calendar, colors, templates, duty names, preparation length — is set per entry, so games and trainings (or two different clubs) can look completely different on the calendar.

## `uid_prefix` and sharing a calendar

Every event this sync creates gets an `iCalUID` built from the entry's `uid_prefix`. The sync decides what it owns on a calendar by filtering for its tag (see [docs/migration.md](migration.md)) *and* that prefix (via a `startswith` match). Two entries pointing at the **same `calendar_id`** must use `uid_prefix` values where **neither is a prefix of the other** — otherwise each entry's cleanup pass would see the other's events as unexpected leftovers and delete them. This means `"myice-"` (the default) and `"myice-p-"` conflict just as much as two identical prefixes do, since the first is a prefix of the second. `validate_configs()` (in `lambda_function.py`) checks this up front and raises before anything is fetched: `Configs at index N and M share calendar_id ... with overlapping uid_prefix values ...; they would delete each other's events.`

Two entries with **different** calendars may safely reuse the same `uid_prefix` — no conflict is possible since the two calendars are checked independently.

## Required fields

| Field | Description |
|---|---|
| `calendar_id` | Target Google Calendar (email or calendar ID). |
| `myice_login_url` | The myice.hockey login form's POST URL. |
| `myice_username_field` | The login form's username field name. |
| `myice_password_field` | The login form's password field name. |
| `myice_credentials_param` | SSM parameter name (SecureString) holding `{"username": "...", "password": "..."}`. |
| `myice_filter_url` | The `playersfilter` endpoint URL. |
| `myice_player_id` | Your player ID. |
| `myice_event_type` | `"g"` for games, `"p"` for trainings — any other value is rejected. |
| `myice_season` | Numeric season ID. |
| `myice_club` | Numeric club/team ID. A player in several clubs sees a different value per club. |
| `myice_min_date` | `"YYYY-MM-DD"` range start for the myice query. |
| `myice_max_date` | `"YYYY-MM-DD"` range end for the myice query. |

See [docs/capturing-ids.md](capturing-ids.md) for how to find all of the `myice_*` values in your browser's DevTools.

## Optional fields

| Field | Default | Description |
|---|---|---|
| `uid_prefix` | `"myice-"` | Namespaces this entry's events. Must be unique per `calendar_id` (see above). |
| `summary_format` | `"{summary}"` | Event title template for normal (confirmed) events. Can reference any record field — see [Summary placeholders](#summary-placeholders). |
| `color_id` | *(calendar default)* | Google Calendar color, `"1"`–`"11"` (see `COLOR_REFERENCE` in `calendar_sync.py`). |
| `timezone` | `DEFAULT_TIMEZONE` env var, `"Europe/Zurich"` | Timezone used for this entry's event times. |
| `request_summary_format` | `"❓ {summary}"` | Title template used instead of `summary_format` when the record's status is "Temporär" (pending). |
| `request_color_id` | falls back to `color_id` | Color for pending ("Temporär") events. |
| `myice_login_extra_fields` | *(none)* | Extra form fields the login POST must send, e.g. a CSRF token. |
| `prep_minutes` | *(off)* | Minutes of warm-up/gathering time before the event. Omit, or set to `0`, for no preparation entries. See [docs/preparation-entries.md](preparation-entries.md). |
| `prep_summary_format` | `"Warm-up: {summary}"` | Title template for preparation entries. |
| `prep_color_id` | falls back to `color_id` | Color for preparation entries. |
| `duty_names` | *(none)* | Names to watch for in the event's detail text, e.g. `["Smith", "Jane Smith"]`. A matching line becomes its own calendar entry. See [docs/duty-entries.md](duty-entries.md). |
| `duty_summary_format` | `"{duty}"` | Title template for duty entries. `{duty}` is the matched detail line; every [summary placeholder](#summary-placeholders) is also available. |
| `duty_color_id` | falls back to `color_id` | Color for duty entries. |
| `respect_manual_deletions` | `False` | When `True`, an event you delete by hand on the calendar is tombstoned and never recreated, as long as it's still in the myice feed. Needs `STATE_BUCKET` set in `.env` for `deploy.sh` to provision S3-backed state (see `README.md` and `sync_state.py`). |

## Summary placeholders

`summary_format`, `request_summary_format`, `prep_summary_format` and
`duty_summary_format` can reference any of these, taken from the myice record:

| Placeholder | Example | Notes |
|---|---|---|
| `{summary}` | `U14 (A) HC Eisbären St. Gallen U14-A` | The default: `agegroup` (or `type`, for trainings) plus `name`. |
| `{name}` | `HC Eisbären St. Gallen U14-A`, `U14 (ICE ALL)` | |
| `{type}` | `Saison`, `Eistraining`, `Trockentraining`, `Spezial` | For games this is just `Saison`; for trainings it is the useful part. |
| `{agegroup}` | `U14 (A)` | **Empty for trainings** — myice sends `null`. |
| `{place}` | `Eishalle Deutweg, 8400 Winterthur ZH` | Often empty for ice trainings. |
| `{weekday}` | `Sa`, `Di` | |
| `{date}` | `2026-10-03` | |
| `{time_start}`, `{time_end}` | `09:00`, `10:45` | Trimmed to `HH:MM`. myice sends `HH:MM:SS`, and some game `time_end` values are recorded timestamps like `11:58:04`. |
| `{duration}` | `75` | Minutes. Trainings only. |
| `{status}` | `Gesund`, `Temporär` | The **only** route for putting the status on the calendar — it is deliberately not in the description. `Gesund` is near-universal, so `{status:? (%)}` or `request_summary_format` is usually what you want rather than `summary_format`. See [`docs/statuses.md`](statuses.md#why-the-status-is-not-in-the-description). |
| `{result}` | `9-10` | The game's final score, as myice records it (your team first). Games only, and **only after the game has been played** — empty beforehand. See the caveat below. |
| `{duty}` | `Speaker: René Grob` | `duty_summary_format` only — the matched detail line. |

An empty field renders as nothing and the surrounding whitespace is collapsed,
so `"{agegroup} {name}"` is safe for trainings — it yields `U14 (ICE ALL)`, not
a leading space.

> **`{result}` will almost never appear on your calendar.** myice only fills it
> in once a game has been played — but by then the game has ended, and a live
> sync never updates an event that has already ended. So the event keeps
> the title it was given while the game was still in the future, with an empty
> result. `{result}` is therefore only really visible in a `--since` dry-run
> report, where past events are deliberately replayed. If you want scores on
> the calendar you would have to allow past updates, which would defeat the
> guard that stops the sync rewriting your history.

### Conditional segments

A **literal separator** next to an empty field would dangle: `"{name} @ {place}"`
on a training with no place gives `U14 (ICE ALL) @`. Use a conditional segment
so the separator disappears along with the value:

```
{variable:?prefix%suffix}
```

The whole segment renders only when the value is non-empty. `%` splits prefix
from suffix; either may be omitted.

| Template | Game (has place, result) | Training (has neither) |
|---|---|---|
| `{name} @ {place}` | `HC Eisbären U14-A @ Deutweg` | `U14 (ICE ALL) @` ← dangles |
| `{name}{place:? @ %}` | `HC Eisbären U14-A @ Deutweg` | `U14 (ICE ALL)` |
| `{name}{place:? (%)}` | `HC Eisbären U14-A (Deutweg)` | `U14 (ICE ALL)` |
| `{type} {name}{duration:? (%min)}` | `Saison HC Eisbären U14-A` | `Eistraining U14 (ICE ALL) (75min)` |
| `{name}{place:? @ %}{result:? · %}` | `HC Eisbären U14-A @ Deutweg · 9-10` | `U14 (ICE ALL)` |

Spacing lives in the prefix, so `{duration:?%min}` appends `75min` with no
space while `{duration:? %min}` gives ` 75min`. A whitespace-only or `null`
value counts as empty. Ordinary format specs still work — `{duration:>4}` pads
as usual.

Because these templates are per entry, you can also just pick one suited to
each feed's data:

```python
# games entry
"summary_format": "🏒 {agegroup} {name}{place:? @ %}",
# trainings entry
"summary_format": "🏒 {type} {name}{duration:? (%min)}",
```

An unknown placeholder logs a warning and falls back to `{summary}` rather than
failing the feed.

**Location** is set from the record's `place` automatically — it is not part of
the summary template. Many ice trainings have no `place`, in which case the
event simply has no location.

## Credentials are not in this file

`myice_credentials_param` and the service-account key are both SSM parameter *names*, not secrets. The actual secrets live in AWS SSM Parameter Store as `SecureString` values — see the README's setup section for the `aws ssm put-parameter` commands. Never put a real username or password into `sync_configs.py`.

**`deploy.sh` does not verify the parameter `myice_credentials_param` points at.** Because the name lives here in the config rather than in `deploy.sh`, it is resolved only at runtime by `get_myice_credentials()`. A deploy succeeds whether or not that parameter exists; if it's missing, the club feed fails on the first invocation instead. Create it before your first run, and use `./run-local.sh` to confirm the lookup works — see the warning in the README's "Configure and Deploy" section.
