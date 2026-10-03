# Capturing your IDs from DevTools

`sync_configs.py` needs several values that aren't documented anywhere public — myice's login form field names and the `playersfilter` endpoint's parameters. You capture these once, by watching the myice.hockey web app talk to its own backend in your browser's DevTools.

## 1. The login form fields

**Most of this is now automatic.** `myice_login_url` is the login **page**, and `myice_client.login()` GETs it before posting — that GET is what sets the PHP session cookie the login depends on. From that page it reads the form's `action` and every hidden input and submit button, then posts your credentials there. So the POST path, the hidden `sublogin=1`, the `login_submit` button, and any CSRF token myice adds in future are discovered rather than configured.

That leaves only two values to capture, and as of 2026-10 they are already correct in `sync_configs_example.py` (`login_email` / `login_password`). Re-capture them only if login starts failing:

1. Open DevTools (F12 or right-click → Inspect) and switch to the **Elements** tab.
2. Go to the myice.hockey login page and inspect the email and password inputs.
3. Their `name` attributes are your `myice_username_field` and `myice_password_field` — these are **not** necessarily `username`/`password`.

`myice_login_extra_fields` is an escape hatch, not a requirement: values you set there are layered on top of the scraped ones, for the case where scraping misses something.

Two things worth knowing about how the request is shaped, because both were load-bearing:

- The payload goes as `multipart/form-data`, matching the form's own `enctype` (visible as a `WebKitFormBoundary...` content type in DevTools). Nothing to configure.
- The client sends a **browser `User-Agent`**. The nginx in front of `app.myice.hockey` returns `403` to the default `python-requests` one and will not serve the login form at all.

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

If login fails, `myice_client.login()` raises because the site's auth cookie never showed up in the session — check the credentials in SSM first, then `myice_username_field`/`myice_password_field` against the login page.

A `403 Forbidden` is a different failure and means the request never reached the login logic: either the `User-Agent` was rejected, or `myice_login_url` no longer serves the form. A quick way to tell the two apart:

```bash
uv run python -c "
import myice_client
try:
    myice_client.login('https://app.myice.hockey/login',
                       'not-real@example.invalid', 'not-a-password',
                       'login_email', 'login_password')
except RuntimeError as e:
    print('reached the backend; it rejected the fake credentials (good)')
except Exception as e:
    print('did not reach the backend:', type(e).__name__, e)
"
```

A `RuntimeError` means the whole request shape is working and only the credentials are at fault. Anything else — `HTTPError`, a connection error — means the shape is broken. Using a deliberately fake email keeps this off your real account's failed-login counter.

If login succeeds but the report shows zero records for a club you know has games scheduled, double-check `player_id`, `club`, `season`, and the date range.
