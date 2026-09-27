"""
Talks to myice.hockey.

myice.hockey's own ical export only includes entries whose health_status is "1"
(Gesund) - it silently drops "3" (Temporär, awaiting your response), so
tentative-but-real invites never reach a calendar synced from it. The
authenticated /api/players/playersfilter endpoint the webapp itself calls
returns the complete list, so we log in and use that instead.

This is an undocumented private endpoint and is the part of this project most
likely to break when myice.hockey changes. It is isolated here so that failure
is legible and testable without touching Google.
"""

import requests

AUTH_COOKIE = "mih_v3_token"
TIMEOUT = 30


def login(
    login_url: str,
    username: str,
    password: str,
    username_field: str,
    password_field: str,
    extra_fields: dict | None = None,
    session=None,
):
    """
    Log in and return an authenticated session (cookie jar) for reuse.

    username_field/password_field/extra_fields must match the real login form's
    field names exactly - capture them from DevTools (see docs/capturing-ids.md).
    extra_fields covers anything else the form sends, e.g. a CSRF token.

    `session` is a test seam; production callers omit it.
    """
    session = session if session is not None else requests.Session()
    payload = {username_field: username, password_field: password}
    if extra_fields:
        payload.update(extra_fields)

    # The login form submits as multipart/form-data (a WebKitFormBoundary is
    # visible in DevTools), not urlencoded. Some backends only accept the exact
    # encoding their frontend uses, so replicate it rather than risk a silent
    # mismatch. The files= trick sends plain strings as multipart parts: each
    # value becomes (filename=None, content=value).
    multipart = {k: (None, str(v)) for k, v in payload.items()}
    resp = session.post(login_url, files=multipart, timeout=TIMEOUT, allow_redirects=True)
    resp.raise_for_status()

    # Success signal: the site's auth cookie actually landed in the jar. More
    # robust than sniffing the post-redirect page, whose text varies by locale
    # and theme. A wrong password never produces this cookie, so its absence is
    # unambiguous.
    if AUTH_COOKIE not in session.cookies.get_dict():
        raise RuntimeError(
            "myice.hockey login produced no auth cookie - check the credentials "
            "in SSM and myice_username_field/myice_password_field in sync_configs.py"
        )
    return session


def fetch_records(
    session,
    filter_url: str,
    player_id: str,
    event_type: str,
    season: str,
    club: str,
    min_date: str,
    max_date: str,
) -> list[dict]:
    """
    Call playersfilter with explicit per-club parameters, so player_id, club,
    season and event_type stay visible, editable config values rather than
    being buried in a hand-captured opaque request body.

    event_type is "g" for games, "p" for trainings. club and season are the
    numeric IDs myice.hockey uses internally.
    """
    resp = session.post(
        filter_url,
        data={
            "type": "filter",
            "player_id": str(player_id),
            "event_type": event_type,
            "season": str(season),
            "club": str(club),
            "minDate": min_date,
            "maxDate": max_date,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])
