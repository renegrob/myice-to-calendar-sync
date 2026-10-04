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

SUMMARY PLACEHOLDERS. summary_format, request_summary_format,
prep_summary_format and duty_summary_format all accept these, taken from the
myice record:

  {summary}     agegroup (or type, for trainings) plus name - the default
  {name}        "HC Eisbaeren St. Gallen U14-A" / "U14 (ICE ALL)"
  {type}        "Saison" for games, "Eistraining"/"Trockentraining"/"Spezial"
  {agegroup}    "U14 (A)" for games; EMPTY for trainings
  {place}       venue; empty for many ice trainings
  {weekday}     "Sa", "Di"
  {date}        "2026-10-03"
  {time_start}  "09:00"   {time_end}  "10:45"   (trimmed to HH:MM)
  {duration}    minutes; trainings only
  {status}      "Gesund", "Temporaer"
  {result}      "9-10"; games only, and only AFTER the game is played -
                by which point a live sync no longer updates the event,
                so this is really only visible in a --since dry run
  {duty}        duty_summary_format only - the matched detail line

A field that is empty for a record renders as nothing and the surrounding
whitespace is collapsed, so "{agegroup} {name}" is safe for trainings.

A LITERAL separator next to an empty field would dangle - "{name} @ {place}"
on a training with no place gives "U14 (ICE ALL) @". Use a CONDITIONAL SEGMENT
so the separator disappears with the value:

    {variable:?prefix%suffix}

renders prefix + value + suffix only when the value is non-empty. "%" splits
prefix from suffix; either may be omitted. Spacing lives in the prefix.

    "{name}{place:? @ %}"                game: "HC Eisbaeren @ Deutweg"
                                     training: "U14 (ICE ALL)"
    "{name}{place:? (%)}"                game: "HC Eisbaeren (Deutweg)"
    "{type} {name}{duration:? (%min)}"   training: "Eistraining U14 (ICE ALL) (75min)"
    "{name}{result:? %}"                 game: "HC Eisbaeren 9-10"

A whitespace-only or null value counts as empty. Ordinary format specs still
work ("{duration:>4}"). An unknown placeholder logs a warning and falls back
to {summary}.

Optional per entry:
  uid_prefix               default "myice-"; must be unique per calendar
  summary_format           default "{summary}"; see SUMMARY PLACEHOLDERS above
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
  duty_summary_format      default "{duty}" - the matched detail line; every
                           summary placeholder above is available too
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
        "summary_format": "🏒 {agegroup} {name}{place:? @ %}",
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
        "summary_format": "🏒 {type} {name}{duration:? (%min)}",
        "color_id": "2",
        "prep_minutes": 20,
    },
]
