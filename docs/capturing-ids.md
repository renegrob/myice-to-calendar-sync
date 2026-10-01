# Capturing your IDs from DevTools

`sync_configs.py` needs several values that aren't documented anywhere public — myice's login form field names and the `playersfilter` endpoint's parameters. You capture these once, by watching the myice.hockey web app talk to its own backend in your browser's DevTools.

## 1. The login form fields

1. Open your browser's DevTools (F12 or right-click → Inspect) and switch to the **Network** tab.
2. Make sure "Preserve log" is enabled, then go to the myice.hockey login page and log in with your real credentials.
3. Find the POST request to the login endpoint in the Network tab's request list (this is `myice_login_url`).
4. Click it, then open its **Payload** tab (Chrome) or **Request** tab (Firefox). This shows the actual field names the form submitted — these are **not** necessarily `username`/`password`; myice's own names become your `myice_username_field` and `myice_password_field`.
5. If the payload includes anything beyond the username and password (a CSRF token is the usual suspect, often named something like `_token`), note its field name and value pattern — set it via `myice_login_extra_fields` in `sync_configs.py`. If it's a dynamic token rather than a fixed value, that field likely can't be hardcoded and the login endpoint may need more investigation; a static CSRF value that doesn't rotate per-session can simply be hardcoded.

Note: `myice_client.login()` sends this payload as `multipart/form-data`, matching what the real login form does (visible as a `WebKitFormBoundary...` content type in the request headers) — you don't need to change anything about the encoding, just the field names and values.

## 2. The `playersfilter` request

1. Still in the Network tab, navigate to your schedule/calendar page inside the myice app (the page that lists your upcoming games and trainings).
2. Look for a POST request to a URL containing `playersfilter` (this is `myice_filter_url`).
3. Click it, open its **Payload** tab, and read off:
   - `player_id` → your `myice_player_id`.
   - `season` → your `myice_season`.
   - `club` → your `myice_club`.
   - `event_type` → confirms whether this particular request was for games (`g`) or trainings (`p`); you'll want both, as two separate entries.
   - `minDate` / `maxDate` → these correspond to `myice_min_date` / `myice_max_date` in `sync_configs.py`.

## Things that change

- **`club` is per-team, not per-player.** If your account is linked to more than one club, switch the active club/team in the myice app's UI and re-capture the `playersfilter` payload — you'll see a different `club` value. Each club needs its own `sync_configs.py` entries (see `docs/configuration.md`).
- **`season` changes once a year.** When myice rolls over to a new season, `season` gets a new numeric value, and you'll typically want to update `myice_min_date` / `myice_max_date` to the new season's date range at the same time. Re-capture the `playersfilter` request after the rollover to get the new `season` value — don't guess it.
- `player_id` is stable for your account and shouldn't need to be re-captured.

## Sanity-checking what you captured

Once `sync_configs.py` is filled in, run a dry run (see `docs/dry-run.md`):

```bash
./run-local.sh
```

If login fails, `myice_client.login()` raises because the site's auth cookie never showed up in the session — double check `myice_username_field`/`myice_password_field` and any `myice_login_extra_fields` against what you captured. If login succeeds but the report shows zero records for a club you know has games scheduled, double-check `player_id`, `club`, `season`, and the date range.
