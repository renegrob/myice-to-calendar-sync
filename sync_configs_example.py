"""
Sync configuration. Copy to sync_configs.py, edit, then redeploy to apply.
sync_configs.py is gitignored - it holds no secrets (credentials live in SSM),
but it does hold your player/club IDs.

ONE ENTRY PER CLUB PER EVENT TYPE. A login can be connected to several clubs,
and games and trainings are fetched separately, so a player in two clubs who
wants both games and trainings has four entries. Credentials are shared; colour,
templates, duty names and the target calendar are per entry.

Two entries may share a calendar_id, but then they MUST have different
uid_prefix values or they will delete each other's events.

Required per entry:
  calendar_id              target Google Calendar (email or calendar ID)
  myice_login_url          the login PAGE url - NOT the form's POST target.
                           The sync GETs this page (which is what sets the PHP
                           session cookie), then reads the form's action and
                           its hidden/submit fields off it and posts there. So
                           the POST path, sublogin, the submit button, and any
                           CSRF token myice adds later are all picked up
                           automatically rather than configured.
  myice_username_field     the login form's username field name
  myice_password_field     the login form's password field name
  myice_credentials_param  SSM SecureString holding {"username","password"}
  myice_filter_url         the playersfilter endpoint URL
  myice_player_id          your player ID
  myice_event_type         "g" games, "p" trainings
  myice_season             numeric season ID
  myice_club               numeric club/team ID
  myice_min_date           "YYYY-MM-DD" range start (update once per season)
  myice_max_date           "YYYY-MM-DD" range end

Optional per entry:
  uid_prefix               default "myice-"; must be unique per calendar
  summary_format           default "{summary}"
  color_id                 Google colour 1-11
  timezone                 default from the DEFAULT_TIMEZONE env var
  request_summary_format   default "❓ {summary}" - status "Temporär"
  request_color_id         colour for pending-response events
  prep_minutes             warm-up length; omit for no preparation entries.
                           The record's own meeting time wins when it has one.
  prep_summary_format      default "Warm-up: {summary}"
  prep_color_id            colour for preparation entries
  duty_names               names to watch for in the event details, e.g.
                           ["Smith", "Jane Smith"]. A matching line becomes its
                           own calendar entry. Matching is case- and
                           accent-insensitive on word boundaries, so a shared
                           surname can produce a false positive.
  duty_summary_format      default "{line}"; {summary} is also available
  duty_color_id            colour for duty entries
  respect_manual_deletions when True, an event you delete by hand is never
                           recreated (needs STATE_BUCKET; see README)

See docs/capturing-ids.md for how to find player_id, season, club and the
login field names in DevTools.
"""

_CREDENTIALS = "/myice-sync/myice-credentials"
_LOGIN_URL = "https://app.myice.hockey/login"
_FILTER_URL = "https://app.myice.hockey/api/players/playersfilter"
_SEASON = "TODO_from_devtools"
_PLAYER_ID = "TODO_from_devtools"

_SHARED = {
    "myice_login_url": _LOGIN_URL,
    # As of 2026-10, app.myice.hockey's login form uses these names. Verify
    # against DevTools if login starts failing (see docs/capturing-ids.md).
    "myice_username_field": "login_email",
    "myice_password_field": "login_password",
    # Not needed: the form's hidden `sublogin=1` and its `login_submit` button
    # are scraped from the login page automatically. Set this only to force a
    # field the scraper misses - values here override the scraped ones.
    # "myice_login_extra_fields": {"sublogin": "1", "login_submit": ""},
    "myice_credentials_param": _CREDENTIALS,
    "myice_filter_url": _FILTER_URL,
    "myice_player_id": _PLAYER_ID,
    "myice_season": _SEASON,
    "myice_min_date": "2026-04-01",
    "myice_max_date": "2027-04-30",
    "calendar_id": "your.email@gmail.com",
}

CONFIGS = [
    # Club A - games
    {
        **_SHARED,
        "myice_event_type": "g",
        "myice_club": "TODO_club_a_id",
        "uid_prefix": "myice-a-game-",
        "summary_format": "🏒 {summary}",
        "color_id": "11",
        "prep_minutes": 90,
        "duty_names": ["TODO_your_surname"],
        "duty_color_id": "5",
    },
    # Club A - trainings
    {
        **_SHARED,
        "myice_event_type": "p",
        "myice_club": "TODO_club_a_id",
        "uid_prefix": "myice-a-training-",
        "summary_format": "🏒 Training {summary}",
        "color_id": "2",
        "prep_minutes": 20,
    },
]
