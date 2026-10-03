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

import re
from urllib.parse import urljoin

import requests

AUTH_COOKIE = "mih_v3_token"
TIMEOUT = 30

# nginx in front of app.myice.hockey returns 403 to the default
# python-requests User-Agent - it will not even serve the login form. The
# account being used is the user's own; this is what makes the site reachable
# at all, not a way around any access control.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

_ACTION_RE = re.compile(r"""action\s*=\s*["']([^"']+)["']""", re.I)
_NAME_RE = re.compile(r"""name\s*=\s*["']([^"']+)["']""", re.I)
_VALUE_RE = re.compile(r"""value\s*=\s*["']([^"']*)["']""", re.I)
# Hidden inputs carry things like sublogin=1; submit buttons are sent by
# browsers too, and this form's backend branches on them.
_SENT_TAG_RE = re.compile(
    r"""<(?:input[^>]*type\s*=\s*["'](?:hidden|submit)["'][^>]*|button[^>]*type\s*=\s*["']submit["'][^>]*)>""",
    re.I,
)


def discover_login_form(html: str, page_url: str, username_field: str) -> tuple[str, dict]:
    """
    Find the login form's POST target and the extra fields a browser would send.

    The form is identified by containing `username_field`, so a search box or
    newsletter form elsewhere on the page is not mistaken for it. Returns
    (action_url, fields); falls back to (page_url, {}) when no matching form is
    found, which keeps a layout change from being a hard crash.

    Scraping rather than configuring these means a changed action path, a new
    hidden field, or an added CSRF token is picked up automatically - which
    matters for an undocumented endpoint that can change without notice.
    """
    for chunk in re.split(r"(?i)<form", html)[1:]:
        body = chunk.split("</form")[0]
        if not re.search(rf"""name\s*=\s*["']{re.escape(username_field)}["']""", body, re.I):
            continue
        open_tag = body.split(">")[0]
        action = _ACTION_RE.search(open_tag)
        fields = {}
        for tag in _SENT_TAG_RE.findall(body):
            name = _NAME_RE.search(tag)
            if not name:
                continue
            value = _VALUE_RE.search(tag)
            fields[name.group(1)] = value.group(1) if value else ""
        return urljoin(page_url, action.group(1)) if action else page_url, fields
    return page_url, {}


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

    `login_url` is the login *page*. We GET it first - that is what sets the
    PHP session cookie the POST needs - then read the form's action and its
    hidden/submit fields off that page and post the credentials there. So the
    POST target is discovered, not configured.

    username_field/password_field must match the real form's field names;
    capture them from DevTools (see docs/capturing-ids.md). extra_fields is an
    escape hatch layered on top of the scraped fields for anything scraping
    misses.

    `session` is a test seam; production callers omit it.
    """
    session = session if session is not None else requests.Session()
    headers = getattr(session, "headers", None)
    if headers is not None:
        # Not setdefault: requests pre-populates User-Agent with
        # "python-requests/<version>", which is the exact value nginx rejects,
        # so setdefault would never replace it. Override that default (and an
        # absent header), but leave a genuinely caller-chosen UA alone.
        current = headers.get("User-Agent") or ""
        if not current or "python-requests" in current.lower():
            headers["User-Agent"] = USER_AGENT

    page = session.get(login_url, timeout=TIMEOUT)
    page.raise_for_status()
    action_url, form_fields = discover_login_form(page.text, login_url, username_field)

    payload = dict(form_fields)
    payload[username_field] = username
    payload[password_field] = password
    if extra_fields:
        payload.update(extra_fields)

    # The login form submits as multipart/form-data (enctype on the form tag,
    # and a WebKitFormBoundary visible in DevTools), not urlencoded. Some
    # backends only accept the exact encoding their frontend uses, so replicate
    # it rather than risk a silent mismatch. The files= trick sends plain
    # strings as multipart parts: each value becomes (filename=None, content).
    multipart = {k: (None, str(v)) for k, v in payload.items()}
    resp = session.post(action_url, files=multipart, timeout=TIMEOUT, allow_redirects=True)
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


def switch_club(session, filter_url: str, club: str) -> None:
    """
    Make `club` the session's active club.

    This is not optional and it is not what the `club` field in the
    playersfilter body does. The webapp switches club with a GET to /?cl=<id>
    that 302s, and playersfilter then answers for whatever club the SESSION
    holds - the body field does not select it. Skipping this makes the endpoint
    return the post-login default club for every request, so two feeds
    configured for different clubs come back byte-identical, each writing the
    same events to a different calendar.

    The switch URL is derived from `filter_url`'s origin rather than configured
    separately: they are necessarily the same host, and one fewer value to
    capture by hand is one fewer to get wrong.
    """
    resp = session.get(urljoin(filter_url, f"/?cl={club}"), timeout=TIMEOUT,
                       allow_redirects=True)
    resp.raise_for_status()


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

    Switches the session's active club first - see switch_club for why that is
    load-bearing.

    event_type is "g" for games, "p" for trainings. club and season are the
    numeric IDs myice.hockey uses internally.
    """
    switch_club(session, filter_url, club)
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
