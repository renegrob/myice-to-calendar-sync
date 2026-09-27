# myice-to-calendar-sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the cloned `aws-ical-sync` repo into a standalone myice.hockey → Google Calendar sync with per-club config, duty entries, preparation entries, status-driven removal, and a dry-run report mode.

**Architecture:** The repo already contains `main`'s pure planner `plan_sync()` plus `sync_state.py`. Work proceeds by (a) renaming the deployment identity, (b) extracting the Google-Calendar plumbing into `calendar_sync.py`, (c) adding myice-specific modules (`myice_client.py`, `duty_parser.py`, `dry_run.py`), (d) replacing the iCal record→body mapping with the myice one, and (e) rewriting `handler` and the local runner. The iCal path is deleted last, once the myice path replaces it, so the tree is never in a state where nothing works.

**Tech Stack:** Python 3.12+, `unittest` (no pytest), `uv`, `requests`, `google-api-python-client`, `boto3`, AWS Lambda + EventBridge Scheduler + SSM + S3.

**Spec:** `docs/superpowers/specs/2026-09-27-myice-to-calendar-sync-design.md`

## Global Constraints

- **Python** `requires-python = ">=3.12"`.
- **Tests** run with `uv run python -m unittest discover -s . -p "test_*.py"`. Tests live at the repo root as `test_*.py`, use `unittest.TestCase`, and use no pytest features.
- **No test makes a live network call** to myice.hockey or Google. Everything is stubbed.
- **No new dependencies** beyond adding `requests` and removing `icalendar`.
- **Deployment identity** — exact values, used verbatim everywhere:
  - Lambda function: `myice-calendar-sync`
  - IAM role: `myice-calendar-sync-role`
  - IAM inline policy name: `myice-calendar-sync-policy`
  - Google SA SSM param: `/myice-sync/google-service-account`
  - myice credentials SSM param: `/myice-sync/myice-credentials`
  - S3 state key: `s3://<STATE_BUCKET>/myice-calendar-sync/sync-state.json`
  - `SOURCE_TAG = "myice-calendar-sync"`
  - Default `uid_prefix`: `myice-`
- **`health_status` map** — `1` Gesund → confirmed; `3` Temporär → request/tentative; `6` Entschuldigt → remove; `8` Krank → remove; `9` Verletzt → remove; anything else → confirmed + log.
- **Default templates** — `summary_format` `"{summary}"`, `request_summary_format` `"❓ {summary}"`, `prep_summary_format` `"Warm-up: {summary}"`, `duty_summary_format` `"{line}"`.
- **A live sync never creates, updates, or deletes an event that has already ended.** Only `--dry-run` may set `allow_past=True`.
- **Commit after every task.** Do not squash tasks together.
- **Do not configure a git remote and do not push.** The user has explicitly deferred this.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `calendar_sync.py` | create (Task 2) | `plan_sync`, `execute_plan`, `list_existing_synced_events`, `event_unchanged`, `google_event_end`, `purge_feed`, `_state_entry`. Google Calendar only. |
| `myice_client.py` | create (Task 3) | `login()`, `fetch_records()`. HTTP + myice.hockey only. |
| `duty_parser.py` | create (Task 4) | `normalise()`, `find_duty_lines()`. Pure text. |
| `dry_run.py` | create (Task 10) | `render_report()`. Plans → text. |
| `lambda_function.py` | rewrite | `handler`, config validation, `record_to_google_body`, `prep_body`, `duty_bodies`, `build_feed_bodies`. |
| `sync_state.py` | unchanged | S3/local state. |
| `run_local.py` | rewrite (Task 10) | CLI: `--dry-run`, `--apply`, `--out`, `--games-only`, `--trainings-only`, `--since`. |
| `run-local.sh` | modify (Task 10) | Flag pass-through + help. |
| `deploy.sh`, `lambda-policy.json`, `pyproject.toml` | modify (Task 1) | Identity, IAM, deps. |
| `sync_configs_example.py` | rewrite (Task 11) | myice-only per-club example. |
| `test_sync.py` | modify | `plan_sync` tests kept; iCal orchestration tests replaced. |
| `test_myice.py`, `test_duty_parser.py`, `test_prep_entries.py`, `test_past_guard.py`, `test_dry_run.py` | create | Per-feature tests. |
| `README.md`, `docs/*.md` | rewrite (Task 12) | Documentation, written last. |

---

## Task 1: Deployment identity, dependencies, and IAM

Mechanical and independent of all code structure. Doing it first means every later task writes the correct names.

**Files:**
- Modify: `pyproject.toml`
- Modify: `lambda-policy.json`
- Modify: `deploy.sh`
- Modify: `env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Rewrite `pyproject.toml`**

`requests` is imported by the myice client but was never declared — it resolves today only because `google-api-python-client` pulls it in transitively. `icalendar` is no longer used.

```toml
[project]
name = "myice-to-calendar-sync"
version = "0.1.0"
description = "Syncs myice.hockey games and trainings to Google Calendar using AWS Lambda"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "requests>=2.32.0",
    "google-api-python-client>=2.140.0",
    "google-auth>=2.34.0",
    "google-auth-httplib2>=0.2.0",
]

[dependency-groups]
dev = [
    "boto3>=1.34.0",
]
```

- [ ] **Step 2: Regenerate the lockfile and requirements**

```bash
uv lock
uv export --no-dev --no-hashes --format requirements-txt -o requirements.txt
```

Confirm `requests` appears in `requirements.txt` as a direct entry and `icalendar` is gone:

```bash
grep -E '^(requests|icalendar)' requirements.txt
```

Expected: a `requests==` line, no `icalendar` line.

- [ ] **Step 3: Rewrite `lambda-policy.json`**

The myice credentials parameter was never granted — `get_myice_credentials` would fail with AccessDenied on Lambda. Both parameters are now listed.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:*:*:*"
    },
    {
      "Effect": "Allow",
      "Action": ["ssm:GetParameter"],
      "Resource": [
        "arn:aws:ssm:*:*:parameter/myice-sync/google-service-account",
        "arn:aws:ssm:*:*:parameter/myice-sync/myice-credentials"
      ]
    }
  ]
}
```

- [ ] **Step 4: Update `deploy.sh` identity**

Replace these exact assignments near the top of the file:

```bash
FUNCTION_NAME="myice-calendar-sync"
SSM_PARAM_NAME="/myice-sync/google-service-account"
ROLE_NAME="myice-calendar-sync-role"
```

Then replace every remaining literal `aws-ical-sync` with `myice-calendar-sync` and every `ical-sync` with `myice-sync`:

```bash
sed -i 's/aws-ical-sync/myice-calendar-sync/g; s|/ical-sync/|/myice-sync/|g' deploy.sh env.example
```

Verify nothing was missed anywhere in the repo's shell/JSON/docs:

```bash
grep -rn 'aws-ical-sync\|/ical-sync/' --include='*.sh' --include='*.json' --include='*.toml' . | grep -v '^./docs/superpowers/'
```

Expected: no output. (The spec under `docs/superpowers/` legitimately mentions the old names as history — leave it alone.)

- [ ] **Step 5: Add dry-run reports to `.gitignore`**

Append:

```gitignore
# Dry-run reports (local inspection output)
dry-run-*.txt
```

- [ ] **Step 6: Verify the deploy script still parses**

```bash
bash -n deploy.sh && bash -n run-local.sh && python3 -c "import json; json.load(open('lambda-policy.json'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock requirements.txt lambda-policy.json deploy.sh env.example .gitignore
git commit -m "Rename deployment identity to myice-calendar-sync; declare requests; grant myice creds in IAM"
```

---

## Task 2: Extract `calendar_sync.py`

Pure refactor — move the Google-Calendar plumbing out of `lambda_function.py` with **no behavior change**, so the existing `plan_sync` tests keep passing and prove it. `execute_plan` is newly extracted from the inline loop in `sync_feed` so the dry-run path can later compute a plan without executing it.

**Files:**
- Create: `calendar_sync.py`
- Modify: `lambda_function.py`
- Modify: `test_sync.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `calendar_sync.plan_sync(feed_uids: set[str], feed_bodies: dict[str, dict], existing: dict[str, dict], state: dict, respect_deletes: bool) -> dict` — returns `{"create": [(uid, body)], "update": [(uid, body, event_id)], "unchanged": [uid], "delete": [(uid, event_id)], "tombstone": [uid], "skip_tombstoned": [uid]}`
  - `calendar_sync.execute_plan(service, calendar_id: str, plan: dict, existing: dict) -> dict` — performs the Google writes, returns the counts dict
  - `calendar_sync.list_existing_synced_events(service, calendar_id: str, uid_prefix: str) -> dict[str, dict]`
  - `calendar_sync.event_unchanged(existing_event: dict, new_body: dict) -> bool`
  - `calendar_sync.google_event_end(existing_event: dict)`
  - `calendar_sync.purge_feed(...)` — signature unchanged from today
  - `calendar_sync.SOURCE_TAG: str`, `calendar_sync.COLOR_REFERENCE: dict`

- [ ] **Step 1: Create `calendar_sync.py` by moving code**

Move these from `lambda_function.py` verbatim, changing only what the import move requires: `SOURCE_TAG`, `COLOR_REFERENCE`, `_COMPARE_FIELDS`, `list_existing_synced_events`, `event_unchanged`, `google_event_end`, `purge_feed`, `_state_entry`, `plan_sync`. Set `SOURCE_TAG = "myice-calendar-sync"`.

Add the module docstring:

```python
"""
Google Calendar reconciliation: what to change, and doing it.

`plan_sync` is pure - it decides create/update/delete/tombstone from the feed,
the calendar, and the sync state, and does no I/O. `execute_plan` performs the
resulting Google writes. Keeping them apart is what lets --dry-run compute a
real plan without touching anything.

Nothing here knows about myice.hockey; it deals only in Google event bodies.
"""
```

- [ ] **Step 2: Add `execute_plan`, extracted from `sync_feed`'s inline loop**

```python
def execute_plan(service, calendar_id: str, plan: dict, existing: dict) -> dict:
    """Apply a plan to the calendar. Returns per-action counts."""
    for uid, body in plan["create"]:
        service.events().import_(calendarId=calendar_id, body=body).execute(num_retries=3)
    for uid, body, event_id in plan["update"]:
        diff = {
            f: {"existing": existing[uid].get(f), "new": body.get(f)}
            for f in _COMPARE_FIELDS
            if existing[uid].get(f) != body.get(f)
        }
        print(f"UPDATE DIFF for {uid}: {json.dumps(diff, default=str)}")
        service.events().import_(calendarId=calendar_id, body=body).execute(num_retries=3)
    for uid, event_id in plan["delete"]:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute(num_retries=3)
    return plan_counts(plan)


def plan_counts(plan: dict) -> dict:
    return {
        "created": len(plan["create"]),
        "updated": len(plan["update"]),
        "unchanged": len(plan["unchanged"]),
        "deleted": len(plan["delete"]),
        "tombstoned": len(plan["tombstone"]),
        "skipped_tombstoned": len(plan["skip_tombstoned"]),
    }
```

- [ ] **Step 3: Re-point `lambda_function.py` at the new module**

Delete the moved definitions from `lambda_function.py` and add near the other imports:

```python
import calendar_sync
from calendar_sync import (
    COLOR_REFERENCE,
    SOURCE_TAG,
    event_unchanged,
    google_event_end,
    list_existing_synced_events,
    plan_sync,
    purge_feed,
)
```

Rewrite `sync_feed`'s tail to use the extraction:

```python
    plan = plan_sync(feed_uids, feed_bodies, existing, state, respect_deletes)
    res = calendar_sync.execute_plan(service, calendar_id, plan, existing)
    res["skipped_past"] = skipped_past
    res["total_in_feed"] = len(feed_uids)
    return res
```

- [ ] **Step 4: Point the tests at the new module**

In `test_sync.py`, change the import line to:

```python
import lambda_function
from calendar_sync import plan_sync
from lambda_function import sync_feed
```

- [ ] **Step 5: Run the full suite — it must pass unchanged**

```bash
uv run python -m unittest discover -s . -p "test_*.py" -v
```

Expected: all tests PASS. This is a pure refactor; a failure means something was moved incorrectly, not that a test needs updating.

- [ ] **Step 6: Commit**

```bash
git add calendar_sync.py lambda_function.py test_sync.py
git commit -m "Extract calendar_sync module with pure plan_sync and execute_plan"
```

---

## Task 3: `myice_client.py`

**Files:**
- Create: `myice_client.py`
- Create: `test_myice_client.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `myice_client.login(login_url: str, username: str, password: str, username_field: str, password_field: str, extra_fields: dict | None = None, session=None) -> requests.Session`
  - `myice_client.fetch_records(session, filter_url: str, player_id: str, event_type: str, season: str, club: str, min_date: str, max_date: str) -> list[dict]`
  - `myice_client.AUTH_COOKIE = "mih_v3_token"`

The `session=None` parameter exists solely so tests can inject a stub; production callers omit it.

- [ ] **Step 1: Write the failing tests**

Create `test_myice_client.py`:

```python
"""Tests for the myice.hockey HTTP client (login + playersfilter fetch)."""
import unittest

import myice_client


class FakeResponse:
    def __init__(self, json_data=None, status=200):
        self._json = json_data if json_data is not None else {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class FakeCookies:
    def __init__(self, jar):
        self._jar = jar

    def get_dict(self):
        return self._jar


class FakeSession:
    """Records calls so tests can assert on what was sent."""

    def __init__(self, cookies=None, response=None):
        self.cookies = FakeCookies(cookies or {})
        self.calls = []
        self._response = response or FakeResponse()

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


class Login(unittest.TestCase):
    def test_returns_session_when_auth_cookie_present(self):
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        result = myice_client.login(
            "https://app.myice.hockey/login", "u", "p",
            "email", "password", session=session,
        )
        self.assertIs(result, session)

    def test_raises_when_auth_cookie_absent(self):
        session = FakeSession(cookies={"other": "x"})
        with self.assertRaises(RuntimeError) as ctx:
            myice_client.login(
                "https://app.myice.hockey/login", "u", "p",
                "email", "password", session=session,
            )
        self.assertIn("login", str(ctx.exception).lower())

    def test_sends_credentials_as_multipart(self):
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        myice_client.login(
            "https://app.myice.hockey/login", "user@example.com", "secret",
            "email", "password", session=session,
        )
        _url, kwargs = session.calls[0]
        self.assertEqual(kwargs["files"]["email"], (None, "user@example.com"))
        self.assertEqual(kwargs["files"]["password"], (None, "secret"))

    def test_includes_extra_fields(self):
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        myice_client.login(
            "https://app.myice.hockey/login", "u", "p",
            "email", "password", extra_fields={"_token": "csrf123"},
            session=session,
        )
        _url, kwargs = session.calls[0]
        self.assertEqual(kwargs["files"]["_token"], (None, "csrf123"))


class FetchRecords(unittest.TestCase):
    def test_sends_expected_filter_parameters(self):
        session = FakeSession(response=FakeResponse({"data": []}))
        myice_client.fetch_records(
            session, "https://app.myice.hockey/api/players/playersfilter",
            player_id="42", event_type="g", season="7", club="3",
            min_date="2026-04-01", max_date="2027-04-30",
        )
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://app.myice.hockey/api/players/playersfilter")
        self.assertEqual(kwargs["data"], {
            "type": "filter", "player_id": "42", "event_type": "g",
            "season": "7", "club": "3",
            "minDate": "2026-04-01", "maxDate": "2027-04-30",
        })

    def test_returns_the_data_list(self):
        records = [{"id_game": "1"}, {"id_game": "2"}]
        session = FakeSession(response=FakeResponse({"data": records}))
        result = myice_client.fetch_records(
            session, "https://x/f", player_id="1", event_type="p",
            season="1", club="1", min_date="2026-01-01", max_date="2026-12-31",
        )
        self.assertEqual(result, records)

    def test_missing_data_key_returns_empty_list(self):
        session = FakeSession(response=FakeResponse({}))
        result = myice_client.fetch_records(
            session, "https://x/f", player_id="1", event_type="p",
            season="1", club="1", min_date="2026-01-01", max_date="2026-12-31",
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m unittest test_myice_client -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'myice_client'`.

- [ ] **Step 3: Write `myice_client.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m unittest test_myice_client -v
```

Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add myice_client.py test_myice_client.py
git commit -m "Add myice_client with login and playersfilter fetch"
```

---

## Task 4: `duty_parser.py`

Pure text matching, no I/O. This is the trickiest logic in the project, so it gets its own module and its own test file.

**Files:**
- Create: `duty_parser.py`
- Create: `test_duty_parser.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `duty_parser.normalise(text: str) -> str` — NFKD, combining marks stripped, casefolded
  - `duty_parser.find_duty_lines(details: str, duty_names: list[str]) -> list[str]` — the original (un-normalised, stripped) lines that mention any name, in document order, deduplicated

- [ ] **Step 1: Write the failing tests**

Create `test_duty_parser.py`. The sample blob is the spec's, with names substituted:

```python
"""Tests for matching duty names in an event's detail blob."""
import unittest

from duty_parser import find_duty_lines, normalise

BLOB = """Coach: Anna Keller Tel. 079 111 22 33
Betreuer: Marc Weber Tel. 079 444 55 66
2 Pack Farmer/Riegel bringt mit: Fam. Smith

PLO
Reporter: Lena Vogt (Lenchen)
Speaker: John Doe
Zeit: Fam. Brown
Strafbank: Smith / Green
Kuchenbuffet: Hofer / Meier"""


class Normalise(unittest.TestCase):
    def test_strips_accents_and_casefolds(self):
        self.assertEqual(normalise("César"), "cesar")
        self.assertEqual(normalise("CÉSAR"), "cesar")
        self.assertEqual(normalise("cesar"), "cesar")

    def test_leaves_plain_text_alone(self):
        self.assertEqual(normalise("Smith"), "smith")


class FindDutyLines(unittest.TestCase):
    def test_matches_first_last_form(self):
        self.assertEqual(find_duty_lines(BLOB, ["John Doe"]), ["Speaker: John Doe"])

    def test_matches_fam_prefix_form(self):
        self.assertEqual(find_duty_lines(BLOB, ["Brown"]), ["Zeit: Fam. Brown"])

    def test_matches_slash_separated_surnames(self):
        self.assertEqual(find_duty_lines(BLOB, ["Green"]), ["Strafbank: Smith / Green"])

    def test_one_name_can_match_several_lines(self):
        self.assertEqual(
            find_duty_lines(BLOB, ["Smith"]),
            ["2 Pack Farmer/Riegel bringt mit: Fam. Smith", "Strafbank: Smith / Green"],
        )

    def test_matches_last_comma_first_form(self):
        blob = "timekeeper: doe, jane"
        self.assertEqual(find_duty_lines(blob, ["Doe"]), ["timekeeper: doe, jane"])

    def test_is_case_and_accent_insensitive(self):
        blob = "Speaker: CESAR Moreno"
        self.assertEqual(find_duty_lines(blob, ["César"]), ["Speaker: CESAR Moreno"])

    def test_respects_word_boundaries(self):
        blob = "Speaker: Alan Smithson"
        self.assertEqual(find_duty_lines(blob, ["Smith"]), [])

    def test_absent_name_matches_nothing(self):
        self.assertEqual(find_duty_lines(BLOB, ["Nobody"]), [])

    def test_blank_lines_and_headers_never_match(self):
        self.assertEqual(find_duty_lines(BLOB, ["PLO"]), ["PLO"])
        self.assertEqual(find_duty_lines("\n\n   \n", ["Smith"]), [])

    def test_several_names_return_lines_in_document_order(self):
        self.assertEqual(
            find_duty_lines(BLOB, ["Brown", "John Doe"]),
            ["Speaker: John Doe", "Zeit: Fam. Brown"],
        )

    def test_a_line_matching_two_names_appears_once(self):
        self.assertEqual(
            find_duty_lines(BLOB, ["Smith", "Green"]),
            ["2 Pack Farmer/Riegel bringt mit: Fam. Smith", "Strafbank: Smith / Green"],
        )

    def test_empty_inputs_are_safe(self):
        self.assertEqual(find_duty_lines("", ["Smith"]), [])
        self.assertEqual(find_duty_lines(BLOB, []), [])
        self.assertEqual(find_duty_lines(None, ["Smith"]), [])

    def test_returned_lines_are_stripped(self):
        self.assertEqual(find_duty_lines("   Speaker: John Doe   ", ["John Doe"]),
                         ["Speaker: John Doe"])


if __name__ == "__main__":
    unittest.main()
```

Note `test_blank_lines_and_headers_never_match` asserts that `PLO` *does* match the name `"PLO"` — a header only fails to match because it contains no configured name, not because headers are special-cased. Do not add header detection.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m unittest test_duty_parser -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'duty_parser'`.

- [ ] **Step 3: Write `duty_parser.py`**

```python
"""
Finds the lines of an event's detail blob that name you.

Game and training details often assign jobs to people:

    Speaker: John Doe
    Zeit: Fam. Brown
    Strafbank: Smith / Green

When a line mentions a configured name, that job becomes its own calendar
entry, so it reads as a distinct commitment instead of being buried in a
description nobody re-reads.

Matching is deliberately loose - case- and accent-insensitive, and a bare
surname counts. A surname shared with another family will therefore produce a
false positive. That is the accepted trade: narrowing the match would silently
miss real duties like "Zeit: Fam. Brown", which is the worse failure.
"""

import re
import unicodedata


def normalise(text: str) -> str:
    """Casefold and strip accents, so César == CESAR == cesar."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return without_marks.casefold()


def find_duty_lines(details: str, duty_names: list[str]) -> list[str]:
    """
    Return the lines of `details` that mention any of `duty_names`.

    Lines come back in document order, stripped, and deduplicated - a line
    naming two of your names yields one entry, not two.
    """
    if not details or not duty_names:
        return []

    # Word-boundary patterns, built once. \b would not fire correctly next to
    # accented characters after normalisation, but since normalise() reduces
    # everything to ASCII-ish letters, \b is safe here.
    patterns = [
        re.compile(rf"\b{re.escape(normalise(name))}\b")
        for name in duty_names
        if name and name.strip()
    ]
    if not patterns:
        return []

    matched = []
    for raw_line in details.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalised_line = normalise(line)
        if any(p.search(normalised_line) for p in patterns):
            matched.append(line)
    return matched
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m unittest test_duty_parser -v
```

Expected: 15 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add duty_parser.py test_duty_parser.py
git commit -m "Add duty_parser for accent-insensitive duty line matching"
```

---

## Task 5: Status model and the myice record → Google body mapping

Replaces the iCal mapping. After this task `lambda_function.py` no longer imports `icalendar`.

**Files:**
- Modify: `lambda_function.py`
- Create: `test_myice.py`
- Modify: `test_sync.py` (drop the iCal orchestration tests)

**Interfaces:**
- Consumes: `calendar_sync.SOURCE_TAG` (Task 2).
- Produces:
  - `lambda_function.STATUS_ACTIONS: dict[str, tuple[str, str]]` — `health_status` → `(action, google_status)` where action is `"sync"`, `"request"`, or `"remove"`
  - `lambda_function.classify(record: dict) -> tuple[str, str]` — `(action, google_status)`
  - `lambda_function.event_details(record: dict) -> str` — the full detail blob for the description
  - `lambda_function.record_to_google_body(record: dict, config: dict, action: str, google_status: str) -> dict` — always returns a body; past-event policy lives in `plan_sync`, not here
  - `lambda_function.record_uid(record: dict, uid_prefix: str) -> str`
  - `lambda_function.event_start_end(record: dict, tz_name: str) -> tuple[datetime, datetime]`

- [ ] **Step 1: Write the failing tests**

Create `test_myice.py`:

```python
"""Tests for classifying myice records and mapping them to Google bodies."""
import unittest

import lambda_function as lf


def record(**overrides):
    base = {
        "id_game": "5001",
        "date": "2099-03-14",
        "time_start": "19:30:00",
        "time_end": "21:00:00",
        "agegroup": "U13",
        "name": "vs Eisbären",
        "place": "Eishalle Nord",
        "health_status": "1",
        "health_status_label": "Gesund",
        "meeting": "00:00:00",
        "notes": "",
        "health_notes": "",
    }
    base.update(overrides)
    return base


def config(**overrides):
    base = {"calendar_id": "cal@example.com", "uid_prefix": "myice-"}
    base.update(overrides)
    return base


class Classify(unittest.TestCase):
    def test_gesund_syncs_confirmed(self):
        self.assertEqual(lf.classify(record(health_status="1")), ("sync", "confirmed"))

    def test_temporaer_is_a_request_and_tentative(self):
        self.assertEqual(lf.classify(record(health_status="3")), ("request", "tentative"))

    def test_entschuldigt_is_removed(self):
        self.assertEqual(lf.classify(record(health_status="6"))[0], "remove")

    def test_krank_is_removed(self):
        self.assertEqual(lf.classify(record(health_status="8"))[0], "remove")

    def test_verletzt_is_removed(self):
        self.assertEqual(lf.classify(record(health_status="9"))[0], "remove")

    def test_unknown_status_syncs_confirmed(self):
        self.assertEqual(lf.classify(record(health_status="99")), ("sync", "confirmed"))

    def test_integer_status_is_handled(self):
        self.assertEqual(lf.classify(record(health_status=3)), ("request", "tentative"))

    def test_missing_status_syncs_confirmed(self):
        r = record()
        del r["health_status"]
        self.assertEqual(lf.classify(r), ("sync", "confirmed"))


class RecordToBody(unittest.TestCase):
    def test_summary_combines_agegroup_and_name(self):
        body = lf.record_to_google_body(record(), config(), "sync", "confirmed")
        self.assertEqual(body["summary"], "U13 vs Eisbären")

    def test_summary_format_is_applied(self):
        body = lf.record_to_google_body(
            record(), config(summary_format="🏒 {summary}"), "sync", "confirmed")
        self.assertEqual(body["summary"], "🏒 U13 vs Eisbären")

    def test_request_uses_the_request_template_and_colour(self):
        body = lf.record_to_google_body(
            record(health_status="3"),
            config(request_summary_format="❓ {summary}", request_color_id="5"),
            "request", "tentative",
        )
        self.assertEqual(body["summary"], "❓ U13 vs Eisbären")
        self.assertEqual(body["colorId"], "5")
        self.assertEqual(body["status"], "tentative")

    def test_request_falls_back_to_default_template(self):
        body = lf.record_to_google_body(
            record(health_status="3"), config(), "request", "tentative")
        self.assertEqual(body["summary"], "❓ U13 vs Eisbären")

    def test_invalid_summary_format_falls_back_to_raw(self):
        body = lf.record_to_google_body(
            record(), config(summary_format="{nope}"), "sync", "confirmed")
        self.assertEqual(body["summary"], "U13 vs Eisbären")

    def test_missing_name_and_agegroup_gets_a_placeholder(self):
        body = lf.record_to_google_body(
            record(agegroup="", name=""), config(), "sync", "confirmed")
        self.assertEqual(body["summary"], "myice.hockey Event")

    def test_start_and_end_use_the_feed_timezone(self):
        body = lf.record_to_google_body(
            record(), config(timezone="Europe/Zurich"), "sync", "confirmed")
        self.assertEqual(body["start"]["dateTime"], "2099-03-14T19:30:00+01:00")
        self.assertEqual(body["end"]["dateTime"], "2099-03-14T21:00:00+01:00")
        self.assertEqual(body["start"]["timeZone"], "Europe/Zurich")

    def test_place_becomes_location(self):
        body = lf.record_to_google_body(record(), config(), "sync", "confirmed")
        self.assertEqual(body["location"], "Eishalle Nord")

    def test_source_tag_is_set(self):
        body = lf.record_to_google_body(record(), config(), "sync", "confirmed")
        self.assertEqual(
            body["extendedProperties"]["private"]["source"], "myice-calendar-sync")

    def test_always_returns_a_body_even_for_past_events(self):
        # Past-event policy lives in plan_sync, not the mapper.
        body = lf.record_to_google_body(
            record(date="2001-01-01"), config(), "sync", "confirmed")
        self.assertIsNotNone(body)


class EventDetails(unittest.TestCase):
    def test_notes_are_included_verbatim(self):
        blob = "Speaker: John Doe\nZeit: Fam. Brown"
        self.assertIn(blob, lf.event_details(record(notes=blob)))

    def test_health_notes_are_included(self):
        self.assertIn("Zurück ab Montag",
                      lf.event_details(record(health_notes="Zurück ab Montag")))

    def test_real_meeting_time_is_included(self):
        self.assertIn("18:45", lf.event_details(record(meeting="18:45:00")))

    def test_placeholder_meeting_times_are_suppressed(self):
        self.assertNotIn("Meeting", lf.event_details(record(meeting="00:00:00")))
        self.assertNotIn("Meeting", lf.event_details(record(meeting="00:00")))

    def test_status_label_is_included(self):
        self.assertIn("Gesund", lf.event_details(record()))

    def test_empty_record_yields_empty_details(self):
        r = record(notes="", health_notes="", meeting="00:00:00",
                   health_status_label="")
        self.assertEqual(lf.event_details(r), "")


class RecordUid(unittest.TestCase):
    def test_uid_is_prefixed_game_id(self):
        self.assertEqual(lf.record_uid(record(), "myice-"), "myice-5001")

    def test_uid_is_stable(self):
        self.assertEqual(lf.record_uid(record(), "myice-"),
                         lf.record_uid(record(), "myice-"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m unittest test_myice -v
```

Expected: FAIL with `AttributeError: module 'lambda_function' has no attribute 'classify'`.

- [ ] **Step 3: Replace the iCal mapping in `lambda_function.py`**

Delete `fetch_ical`, `event_to_google_body`, `is_past_event`, `normalize_uid`, the `from icalendar import Calendar` import, the `urllib.request` import, and the now-unused `SKIP_PAST_EVENTS` constant. Add:

```python
MYICE_CREDENTIALS_PARAM_DEFAULT = "/myice-sync/myice-credentials"

# health_status -> (action, Google status).
#   sync    - normal event
#   request - awaiting your response in the app; styled differently
#   remove  - you are out; the calendar entry and everything derived from it goes
# Codes come from the app's own #health_status select, plus "3", which the
# system assigns rather than offering as a choice.
STATUS_ACTIONS = {
    "1": ("sync", "confirmed"),     # Gesund
    "3": ("request", "tentative"),  # Temporär - pending your response
    "6": ("remove", ""),            # Entschuldigt
    "8": ("remove", ""),            # Krank
    "9": ("remove", ""),            # Verletzt
}
PLACEHOLDER_MEETING_TIMES = ("", "00:00", "00:00:00")


def classify(record: dict) -> tuple[str, str]:
    """Decide what to do with a record based on its health_status."""
    raw = record.get("health_status")
    key = str(raw) if raw is not None else ""
    if key in STATUS_ACTIONS:
        return STATUS_ACTIONS[key]
    if key:
        # Log rather than guess silently: a new status should be visible in
        # CloudWatch, not quietly treated as normal forever.
        print(f"WARNING: unknown health_status {key!r}; syncing as confirmed")
    return ("sync", "confirmed")


def event_details(record: dict) -> str:
    """The event's full detail text, as it goes into the description."""
    parts = []
    if record.get("notes"):
        parts.append(str(record["notes"]))
    if record.get("health_notes"):
        parts.append(f"Note: {record['health_notes']}")
    meeting = str(record.get("meeting") or "")
    if meeting not in PLACEHOLDER_MEETING_TIMES:
        parts.append(f"Meeting time: {meeting}")
    if record.get("health_status_label"):
        parts.append(f"Status: {record['health_status_label']}")
    return "\n".join(parts)


def record_uid(record: dict, uid_prefix: str) -> str:
    return f"{uid_prefix}{record.get('id_game')}"


def event_start_end(record: dict, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    day = record["date"]
    start = datetime.strptime(f"{day} {record['time_start']}", "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(f"{day} {record['time_end']}", "%Y-%m-%d %H:%M:%S")
    return start.replace(tzinfo=tz), end.replace(tzinfo=tz)


def _format_summary(template: str, raw_summary: str) -> str:
    try:
        return template.format(summary=raw_summary)
    except (KeyError, IndexError):
        print(f"WARNING: invalid summary format {template!r}, using raw summary")
        return raw_summary


def record_to_google_body(record: dict, config: dict, action: str, google_status: str) -> dict:
    """
    Map a myice record to a Google event body.

    Always returns a body. Whether a past event is actually written is decided
    by plan_sync, so that policy lives in exactly one tested place.
    """
    tz_name = config.get("timezone", DEFAULT_TIMEZONE)
    start, end = event_start_end(record, tz_name)

    raw_summary = f"{record.get('agegroup', '')} {record.get('name', '')}".strip()
    raw_summary = raw_summary or "myice.hockey Event"

    if action == "request":
        template = config.get("request_summary_format", "❓ {summary}")
        color_id = config.get("request_color_id") or config.get("color_id")
    else:
        template = config.get("summary_format", "{summary}")
        color_id = config.get("color_id")

    body = {
        "summary": _format_summary(template, raw_summary),
        "status": google_status or "confirmed",
        "extendedProperties": {"private": {"source": SOURCE_TAG}},
        "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
        "reminders": {"useDefault": True},
    }
    if record.get("place"):
        body["location"] = str(record["place"])
    details = event_details(record)
    if details:
        body["description"] = details
    if color_id:
        body["colorId"] = str(color_id)
    return body
```

- [ ] **Step 4: Remove the iCal orchestration tests**

In `test_sync.py`, delete the `_FakeRequest`/`_FakeEvents`/`FakeService` helpers, the `SyncFeedOrchestration` class, the `HandlerStateWiring` class, and the `from icalendar import Calendar, Event` import. Keep `PlanSyncDefaultMode` and `PlanSyncRespectMode` and their `body`/`existing`/`empty_state` helpers untouched — they are pure and still correct. Orchestration is re-tested against myice in Task 9.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run python -m unittest test_myice test_duty_parser test_myice_client test_sync -v
```

Expected: all PASS. `lambda_function.py` will still fail to import if `sync_feed` references deleted functions — delete `sync_feed` too; it is rewritten in Task 9. Leave `handler` temporarily broken only if it still imports; otherwise comment nothing out, just let Task 9 replace it.

- [ ] **Step 6: Confirm icalendar is fully gone**

```bash
grep -rn 'icalendar\|ical_url\|fetch_ical' --include='*.py' . | grep -v '^./docs/'
```

Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add lambda_function.py test_myice.py test_sync.py
git commit -m "Replace iCal mapping with myice record mapping and status model"
```

---

## Task 6: Preparation entries

**Files:**
- Modify: `lambda_function.py`
- Create: `test_prep_entries.py`

**Interfaces:**
- Consumes: `record_to_google_body`, `event_start_end`, `event_details`, `record_uid` (Task 5).
- Produces:
  - `lambda_function.prep_start(record: dict, start: datetime, tz_name: str, prep_minutes: int) -> datetime`
  - `lambda_function.prep_body(record: dict, config: dict, parent_body: dict, google_status: str) -> dict | None` — `None` when `prep_minutes` is not configured
  - `lambda_function.prep_uid(record: dict, uid_prefix: str) -> str`

- [ ] **Step 1: Write the failing tests**

Create `test_prep_entries.py`:

```python
"""Tests for the preparation (warm-up) entry derived from each event."""
import unittest

import lambda_function as lf
from test_myice import config, record


def parent(**cfg_overrides):
    cfg = config(**cfg_overrides)
    return cfg, lf.record_to_google_body(record(), cfg, "sync", "confirmed")


class PrepEnabled(unittest.TestCase):
    def test_no_prep_minutes_means_no_entry(self):
        cfg, body = parent()
        self.assertIsNone(lf.prep_body(record(), cfg, body, "confirmed"))

    def test_prep_minutes_creates_an_entry(self):
        cfg, body = parent(prep_minutes=60)
        self.assertIsNotNone(lf.prep_body(record(), cfg, body, "confirmed"))


class PrepTiming(unittest.TestCase):
    def test_offset_is_used_when_no_meeting_time(self):
        cfg, body = parent(prep_minutes=60, timezone="Europe/Zurich")
        prep = lf.prep_body(record(meeting="00:00:00"), cfg, body, "confirmed")
        self.assertEqual(prep["start"]["dateTime"], "2099-03-14T18:30:00+01:00")
        self.assertEqual(prep["end"]["dateTime"], "2099-03-14T19:30:00+01:00")

    def test_meeting_time_wins_over_the_offset(self):
        cfg, body = parent(prep_minutes=60, timezone="Europe/Zurich")
        prep = lf.prep_body(record(meeting="18:45:00"), cfg, body, "confirmed")
        self.assertEqual(prep["start"]["dateTime"], "2099-03-14T18:45:00+01:00")
        self.assertEqual(prep["end"]["dateTime"], "2099-03-14T19:30:00+01:00")

    def test_placeholder_meeting_times_are_treated_as_absent(self):
        cfg, body = parent(prep_minutes=30, timezone="Europe/Zurich")
        for placeholder in ("00:00", "00:00:00", ""):
            prep = lf.prep_body(record(meeting=placeholder), cfg, body, "confirmed")
            self.assertEqual(prep["start"]["dateTime"], "2099-03-14T19:00:00+01:00",
                             f"meeting={placeholder!r}")

    def test_meeting_at_or_after_start_falls_back_to_the_offset(self):
        cfg, body = parent(prep_minutes=60, timezone="Europe/Zurich")
        for bad in ("19:30:00", "20:00:00"):
            prep = lf.prep_body(record(meeting=bad), cfg, body, "confirmed")
            self.assertEqual(prep["start"]["dateTime"], "2099-03-14T18:30:00+01:00",
                             f"meeting={bad!r}")

    def test_prep_always_ends_at_the_event_start(self):
        cfg, body = parent(prep_minutes=90, timezone="Europe/Zurich")
        prep = lf.prep_body(record(), cfg, body, "confirmed")
        self.assertEqual(prep["end"]["dateTime"], body["start"]["dateTime"])


class PrepContent(unittest.TestCase):
    def test_default_summary_template(self):
        cfg, body = parent(prep_minutes=60)
        prep = lf.prep_body(record(), cfg, body, "confirmed")
        self.assertEqual(prep["summary"], "Warm-up: U13 vs Eisbären")

    def test_custom_summary_template(self):
        cfg, body = parent(prep_minutes=60, prep_summary_format="🔥 {summary}")
        prep = lf.prep_body(record(), cfg, body, "confirmed")
        self.assertEqual(prep["summary"], "🔥 U13 vs Eisbären")

    def test_prep_colour_overrides_the_club_colour(self):
        cfg, body = parent(prep_minutes=60, color_id="7", prep_color_id="2")
        prep = lf.prep_body(record(), cfg, body, "confirmed")
        self.assertEqual(prep["colorId"], "2")

    def test_falls_back_to_the_club_colour(self):
        cfg, body = parent(prep_minutes=60, color_id="7")
        prep = lf.prep_body(record(), cfg, body, "confirmed")
        self.assertEqual(prep["colorId"], "7")

    def test_inherits_location_and_details(self):
        blob = "Speaker: John Doe"
        cfg, body = parent(prep_minutes=60)
        prep = lf.prep_body(record(notes=blob), cfg, body, "confirmed")
        self.assertEqual(prep["location"], "Eishalle Nord")
        self.assertIn(blob, prep["description"])

    def test_request_status_is_inherited(self):
        cfg, body = parent(prep_minutes=60)
        prep = lf.prep_body(record(health_status="3"), cfg, body, "tentative")
        self.assertEqual(prep["status"], "tentative")


class PrepUid(unittest.TestCase):
    def test_uid_shape(self):
        self.assertEqual(lf.prep_uid(record(), "myice-"), "myice-prep-5001")

    def test_uid_is_stable_across_meeting_time_changes(self):
        # A changed meeting time must update the entry, not delete and recreate it.
        a = lf.prep_uid(record(meeting="18:45:00"), "myice-")
        b = lf.prep_uid(record(meeting="19:00:00"), "myice-")
        self.assertEqual(a, b)

    def test_prep_uid_differs_from_the_event_uid(self):
        self.assertNotEqual(lf.prep_uid(record(), "myice-"),
                            lf.record_uid(record(), "myice-"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m unittest test_prep_entries -v
```

Expected: FAIL with `AttributeError: module 'lambda_function' has no attribute 'prep_body'`.

- [ ] **Step 3: Implement prep entries in `lambda_function.py`**

Add `timedelta` to the `datetime` import, then:

```python
def prep_uid(record: dict, uid_prefix: str) -> str:
    # Not derived from the meeting time, so that a club editing the meeting time
    # updates this entry in place rather than deleting and recreating it.
    return f"{uid_prefix}prep-{record.get('id_game')}"


def prep_start(record: dict, start: datetime, tz_name: str, prep_minutes: int) -> datetime:
    """
    When the preparation block begins.

    Prefers the club's own stated meeting time for this specific event, because
    that is real data rather than a per-club average. Falls back to the
    configured offset when absent or nonsensical.
    """
    meeting = str(record.get("meeting") or "")
    if meeting in PLACEHOLDER_MEETING_TIMES:
        return start - timedelta(minutes=prep_minutes)

    try:
        parts = [int(p) for p in meeting.split(":")]
        hour, minute = parts[0], parts[1]
        second = parts[2] if len(parts) > 2 else 0
        candidate = start.replace(hour=hour, minute=minute, second=second, microsecond=0)
    except (ValueError, IndexError):
        print(f"WARNING: unparseable meeting time {meeting!r}; using prep_minutes")
        return start - timedelta(minutes=prep_minutes)

    if candidate >= start:
        print(f"WARNING: meeting time {meeting!r} is at or after the event start; "
              "using prep_minutes")
        return start - timedelta(minutes=prep_minutes)
    return candidate


def prep_body(record: dict, config: dict, parent_body: dict, google_status: str) -> dict | None:
    """The warm-up / gathering block before an event, or None if not configured."""
    prep_minutes = config.get("prep_minutes")
    if not prep_minutes:
        return None

    tz_name = config.get("timezone", DEFAULT_TIMEZONE)
    start, _end = event_start_end(record, tz_name)
    begins = prep_start(record, start, tz_name, int(prep_minutes))

    raw_summary = f"{record.get('agegroup', '')} {record.get('name', '')}".strip()
    raw_summary = raw_summary or "myice.hockey Event"
    template = config.get("prep_summary_format", "Warm-up: {summary}")

    body = {
        "summary": _format_summary(template, raw_summary),
        "status": google_status or "confirmed",
        "extendedProperties": {"private": {"source": SOURCE_TAG}},
        "start": {"dateTime": begins.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "reminders": {"useDefault": True},
    }
    if parent_body.get("location"):
        body["location"] = parent_body["location"]
    if parent_body.get("description"):
        body["description"] = parent_body["description"]
    color_id = config.get("prep_color_id") or config.get("color_id")
    if color_id:
        body["colorId"] = str(color_id)
    return body
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m unittest test_prep_entries -v
```

Expected: 16 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add lambda_function.py test_prep_entries.py
git commit -m "Add preparation entries, preferring the record's meeting time"
```

---

## Task 7: Duty entries

**Files:**
- Modify: `lambda_function.py`
- Create: `test_duty_entries.py`

**Interfaces:**
- Consumes: `duty_parser.find_duty_lines` (Task 4), `record_to_google_body` (Task 5).
- Produces:
  - `lambda_function.duty_uid(record: dict, uid_prefix: str, line: str) -> str`
  - `lambda_function.duty_bodies(record: dict, config: dict, parent_body: dict, google_status: str) -> dict[str, dict]` — UID → body, empty when no `duty_names` or no matches

- [ ] **Step 1: Write the failing tests**

Create `test_duty_entries.py`:

```python
"""Tests for the extra calendar entries created from duty lines."""
import unittest

import lambda_function as lf
from test_myice import config, record

BLOB = """Coach: Anna Keller Tel. 079 111 22 33
Speaker: John Doe
Zeit: Fam. Brown
Strafbank: Smith / Green"""


def parent(**cfg_overrides):
    cfg = config(**cfg_overrides)
    rec = record(notes=BLOB)
    return rec, cfg, lf.record_to_google_body(rec, cfg, "sync", "confirmed")


class DutyBodies(unittest.TestCase):
    def test_no_duty_names_means_no_entries(self):
        rec, cfg, body = parent()
        self.assertEqual(lf.duty_bodies(rec, cfg, body, "confirmed"), {})

    def test_no_match_means_no_entries(self):
        rec, cfg, body = parent(duty_names=["Nobody"])
        self.assertEqual(lf.duty_bodies(rec, cfg, body, "confirmed"), {})

    def test_one_entry_per_matched_line(self):
        rec, cfg, body = parent(duty_names=["Brown", "John Doe"])
        self.assertEqual(len(lf.duty_bodies(rec, cfg, body, "confirmed")), 2)

    def test_summary_defaults_to_the_matched_line(self):
        rec, cfg, body = parent(duty_names=["John Doe"])
        summaries = [b["summary"] for b in lf.duty_bodies(rec, cfg, body, "confirmed").values()]
        self.assertEqual(summaries, ["Speaker: John Doe"])

    def test_custom_summary_template_can_use_line_and_summary(self):
        rec, cfg, body = parent(duty_names=["John Doe"],
                                duty_summary_format="{line} ({summary})")
        summaries = [b["summary"] for b in lf.duty_bodies(rec, cfg, body, "confirmed").values()]
        self.assertEqual(summaries, ["Speaker: John Doe (U13 vs Eisbären)"])

    def test_description_is_the_full_blob(self):
        rec, cfg, body = parent(duty_names=["John Doe"])
        duty = next(iter(lf.duty_bodies(rec, cfg, body, "confirmed").values()))
        self.assertIn(BLOB, duty["description"])

    def test_times_match_the_parent_event(self):
        rec, cfg, body = parent(duty_names=["John Doe"])
        duty = next(iter(lf.duty_bodies(rec, cfg, body, "confirmed").values()))
        self.assertEqual(duty["start"], body["start"])
        self.assertEqual(duty["end"], body["end"])

    def test_duty_colour_overrides_the_club_colour(self):
        rec, cfg, body = parent(duty_names=["John Doe"], color_id="7", duty_color_id="11")
        duty = next(iter(lf.duty_bodies(rec, cfg, body, "confirmed").values()))
        self.assertEqual(duty["colorId"], "11")

    def test_request_status_is_inherited(self):
        rec, cfg, body = parent(duty_names=["John Doe"])
        duty = next(iter(lf.duty_bodies(rec, cfg, body, "tentative").values()))
        self.assertEqual(duty["status"], "tentative")

    def test_location_is_inherited(self):
        rec, cfg, body = parent(duty_names=["John Doe"])
        duty = next(iter(lf.duty_bodies(rec, cfg, body, "confirmed").values()))
        self.assertEqual(duty["location"], "Eishalle Nord")


class DutyUid(unittest.TestCase):
    def test_uid_is_stable_for_the_same_line(self):
        self.assertEqual(lf.duty_uid(record(), "myice-", "Speaker: John Doe"),
                         lf.duty_uid(record(), "myice-", "Speaker: John Doe"))

    def test_different_lines_get_different_uids(self):
        self.assertNotEqual(lf.duty_uid(record(), "myice-", "Speaker: John Doe"),
                            lf.duty_uid(record(), "myice-", "Zeit: Fam. Brown"))

    def test_uid_is_unaffected_by_reordering_other_lines(self):
        rec_a = record(notes="Speaker: John Doe\nZeit: Fam. Brown")
        rec_b = record(notes="Zeit: Fam. Brown\nSpeaker: John Doe")
        cfg = config(duty_names=["John Doe"])
        body_a = lf.record_to_google_body(rec_a, cfg, "sync", "confirmed")
        body_b = lf.record_to_google_body(rec_b, cfg, "sync", "confirmed")
        self.assertEqual(
            set(lf.duty_bodies(rec_a, cfg, body_a, "confirmed")),
            set(lf.duty_bodies(rec_b, cfg, body_b, "confirmed")),
        )

    def test_uid_carries_the_prefix_and_game_id(self):
        uid = lf.duty_uid(record(), "myice-", "Speaker: John Doe")
        self.assertTrue(uid.startswith("myice-duty-5001-"))

    def test_duty_uid_differs_from_event_and_prep_uids(self):
        uid = lf.duty_uid(record(), "myice-", "Speaker: John Doe")
        self.assertNotIn(uid, {lf.record_uid(record(), "myice-"),
                               lf.prep_uid(record(), "myice-")})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m unittest test_duty_entries -v
```

Expected: FAIL with `AttributeError: module 'lambda_function' has no attribute 'duty_bodies'`.

- [ ] **Step 3: Implement duty entries in `lambda_function.py`**

Add `import hashlib` and `import duty_parser` at the top, then:

```python
def duty_uid(record: dict, uid_prefix: str, line: str) -> str:
    # Hashing the line rather than using its index keeps the UID stable when
    # unrelated lines are added or reordered. Editing a matched line does change
    # its UID, which correctly reads as "that duty went away, this one appeared".
    digest = hashlib.sha1(duty_parser.normalise(line).encode("utf-8")).hexdigest()[:8]
    return f"{uid_prefix}duty-{record.get('id_game')}-{digest}"


def duty_bodies(record: dict, config: dict, parent_body: dict, google_status: str) -> dict:
    """One calendar entry per detail line that names you. UID -> body."""
    duty_names = config.get("duty_names") or []
    lines = duty_parser.find_duty_lines(event_details(record), duty_names)
    if not lines:
        return {}

    uid_prefix = config.get("uid_prefix", DEFAULT_UID_PREFIX)
    template = config.get("duty_summary_format", "{line}")
    color_id = config.get("duty_color_id") or config.get("color_id")
    raw_summary = f"{record.get('agegroup', '')} {record.get('name', '')}".strip()
    raw_summary = raw_summary or "myice.hockey Event"

    bodies = {}
    for line in lines:
        try:
            summary = template.format(line=line, summary=raw_summary)
        except (KeyError, IndexError):
            print(f"WARNING: invalid duty_summary_format {template!r}, using the line")
            summary = line

        body = {
            "summary": summary,
            "status": google_status or "confirmed",
            "extendedProperties": {"private": {"source": SOURCE_TAG}},
            "start": dict(parent_body["start"]),
            "end": dict(parent_body["end"]),
            "reminders": {"useDefault": True},
        }
        if parent_body.get("location"):
            body["location"] = parent_body["location"]
        if parent_body.get("description"):
            body["description"] = parent_body["description"]
        if color_id:
            body["colorId"] = str(color_id)
        bodies[duty_uid(record, uid_prefix, line)] = body
    return bodies
```

Also add near the other constants:

```python
DEFAULT_UID_PREFIX = "myice-"
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m unittest test_duty_entries -v
```

Expected: 15 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add lambda_function.py test_duty_entries.py
git commit -m "Add duty entries derived from matched detail lines"
```

---

## Task 8: The past-event guard in `plan_sync`

Consolidates every past-event decision into the planner. This **changes behavior**: previously a calendar event whose UID left the feed was deleted regardless of age; now past events are never created, updated, or deleted in a live sync.

**Files:**
- Modify: `calendar_sync.py`
- Create: `test_past_guard.py`
- Modify: `test_sync.py`

**Interfaces:**
- Consumes: `calendar_sync.plan_sync` (Task 2).
- Produces: `plan_sync(..., respect_deletes: bool, allow_past: bool = False)` — the signature gains a trailing keyword argument. `calendar_sync.body_has_ended(body: dict, now: datetime | None = None) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `test_past_guard.py`:

```python
"""A live sync never touches an event that has already ended."""
import unittest

from calendar_sync import plan_sync

PAST = "2001-01-01"
FUTURE = "2099-01-01"


def body(uid, day):
    return {"iCalUID": uid, "summary": "Event",
            "start": {"date": day}, "end": {"date": day}}


def existing(uid, day):
    e = body(uid, day)
    e["id"] = f"gid-{uid}"
    return e


def empty_state():
    return {"synced": {}, "tombstones": {}}


class LiveSyncIgnoresThePast(unittest.TestCase):
    def test_past_event_is_not_created(self):
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", PAST)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, allow_past=False)
        self.assertEqual(plan["create"], [])

    def test_past_event_is_not_updated(self):
        changed = body("a", PAST)
        changed["summary"] = "Renamed"
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": changed},
                         existing={"a": existing("a", PAST)}, state=empty_state(),
                         respect_deletes=False, allow_past=False)
        self.assertEqual(plan["update"], [])

    def test_past_event_removed_from_feed_is_not_deleted(self):
        plan = plan_sync(feed_uids=set(), feed_bodies={},
                         existing={"a": existing("a", PAST)}, state=empty_state(),
                         respect_deletes=False, allow_past=False)
        self.assertEqual(plan["delete"], [])

    def test_future_event_removed_from_feed_is_still_deleted(self):
        plan = plan_sync(feed_uids=set(), feed_bodies={},
                         existing={"a": existing("a", FUTURE)}, state=empty_state(),
                         respect_deletes=False, allow_past=False)
        self.assertEqual([uid for uid, _ in plan["delete"]], ["a"])

    def test_future_events_are_unaffected_by_the_guard(self):
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", FUTURE)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, allow_past=False)
        self.assertEqual([uid for uid, _ in plan["create"]], ["a"])


class DryRunMayReachIntoThePast(unittest.TestCase):
    def test_past_event_is_planned_when_allowed(self):
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", PAST)},
                         existing={}, state=empty_state(),
                         respect_deletes=False, allow_past=True)
        self.assertEqual([uid for uid, _ in plan["create"]], ["a"])

    def test_past_deletion_is_planned_when_allowed(self):
        plan = plan_sync(feed_uids=set(), feed_bodies={},
                         existing={"a": existing("a", PAST)}, state=empty_state(),
                         respect_deletes=False, allow_past=True)
        self.assertEqual([uid for uid, _ in plan["delete"]], ["a"])


class GuardWithRespectDeletes(unittest.TestCase):
    def test_past_event_is_not_tombstoned(self):
        state = {"synced": {"a": {"date": PAST, "summary": "Event"}}, "tombstones": {}}
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", PAST)},
                         existing={}, state=state,
                         respect_deletes=True, allow_past=False)
        self.assertEqual(plan["tombstone"], [])

    def test_future_event_is_still_tombstoned(self):
        state = {"synced": {"a": {"date": FUTURE, "summary": "Event"}}, "tombstones": {}}
        plan = plan_sync(feed_uids={"a"}, feed_bodies={"a": body("a", FUTURE)},
                         existing={}, state=state,
                         respect_deletes=True, allow_past=False)
        self.assertEqual(plan["tombstone"], ["a"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m unittest test_past_guard -v
```

Expected: FAIL — `plan_sync() got an unexpected keyword argument 'allow_past'`.

- [ ] **Step 3: Implement the guard in `calendar_sync.py`**

Add the helper:

```python
def body_has_ended(body: dict, now: datetime | None = None) -> bool:
    """True if this event's end is in the past."""
    end = body.get("end", {})
    raw = end.get("dateTime") or end.get("date")
    if not raw:
        return False
    if "T" in raw:
        when = datetime.fromisoformat(raw)
        now = now or datetime.now(when.tzinfo)
        return when < now
    # All-day: ends at the end of that day.
    day = date.fromisoformat(raw[:10])
    return day < (now.date() if now else date.today())
```

Then give `plan_sync` the new parameter and three guards. Change the signature to:

```python
def plan_sync(feed_uids, feed_bodies, existing, state, respect_deletes, allow_past=False):
```

Extend the docstring with:

```
    allow_past - when False (the default, and always true for a live sync), an
                 event that has already ended is never created, updated,
                 deleted or tombstoned. Only --dry-run sets this True, so it can
                 replay a past week for inspection.
```

Immediately after `tombstones = state["tombstones"]`, insert the skip for past feed events:

```python
    if not allow_past:
        feed_bodies = {uid: b for uid, b in feed_bodies.items() if not body_has_ended(b)}
```

And guard the deletion pass — replace the `for uid, existing_event in existing.items():` loop body's first line with:

```python
    for uid, existing_event in existing.items():
        if uid in feed_uids:
            continue
        if not allow_past and body_has_ended(existing_event):
            continue  # history stays as it was recorded
        plan["delete"].append((uid, existing_event["id"]))
        if respect_deletes:
            synced.pop(uid, None)
            tombstones.pop(uid, None)
```

- [ ] **Step 4: Run the new tests and the whole suite**

```bash
uv run python -m unittest test_past_guard -v
uv run python -m unittest discover -s . -p "test_*.py"
```

Expected: `test_past_guard` PASSES (9 tests). Existing `test_sync.py` tests also pass — their fixtures use `2099-01-01`, which is in the future, so the guard does not fire.

- [ ] **Step 5: Add an explicit regression test to `test_sync.py`**

So the behavior change is documented where the old behavior was tested. Append to `PlanSyncDefaultMode`:

```python
    def test_past_event_removed_from_feed_is_left_alone(self):
        """Changed from the iCal original: history is never rewritten."""
        plan = plan_sync(
            feed_uids=set(), feed_bodies={},
            existing={"a": existing("a", day="2001-01-01")},
            state=empty_state(), respect_deletes=False,
        )
        self.assertEqual(plan["delete"], [])
```

- [ ] **Step 6: Run the full suite**

```bash
uv run python -m unittest discover -s . -p "test_*.py"
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add calendar_sync.py test_past_guard.py test_sync.py
git commit -m "Never create, update or delete past events in a live sync"
```

---

## Task 9: Feed assembly, config validation, and `handler`

Wires everything together and restores a working end-to-end sync.

**Files:**
- Modify: `lambda_function.py`
- Create: `test_handler.py`

**Interfaces:**
- Consumes: everything from Tasks 2–8.
- Produces:
  - `lambda_function.build_feed(records: list[dict], config: dict) -> tuple[set[str], dict[str, dict]]` — `(feed_uids, feed_bodies)`. Records classified `remove` contribute **nothing** to either, so `plan_sync` deletes them and everything derived from them.
  - `lambda_function.validate_configs(configs: list[dict]) -> None` — raises `RuntimeError` on the first problem
  - `lambda_function.sync_club(service, config: dict, state: dict, allow_past: bool = False, plan_only: bool = False) -> dict` — returns `{"plan": ..., "existing": ..., "counts": ...}`
  - `lambda_function.select_configs(configs: list[dict], only: str | None) -> list[dict]` — `only` is `"g"`, `"p"`, or `None`
  - `lambda_function.REQUIRED_FIELDS: tuple[str, ...]`

- [ ] **Step 1: Write the failing tests**

Create `test_handler.py`:

```python
"""Tests for feed assembly, config validation, and club selection."""
import unittest

import lambda_function as lf
from test_myice import config, record


class BuildFeed(unittest.TestCase):
    def test_a_healthy_record_yields_one_event(self):
        uids, bodies = lf.build_feed([record()], config())
        self.assertEqual(uids, {"myice-5001"})
        self.assertEqual(set(bodies), {"myice-5001"})

    def test_prep_and_duty_entries_are_included(self):
        cfg = config(prep_minutes=60, duty_names=["John Doe"])
        uids, bodies = lf.build_feed([record(notes="Speaker: John Doe")], cfg)
        self.assertIn("myice-5001", bodies)
        self.assertIn("myice-prep-5001", bodies)
        self.assertEqual(len([u for u in bodies if "-duty-" in u]), 1)
        self.assertEqual(uids, set(bodies))

    def test_removed_status_contributes_nothing(self):
        cfg = config(prep_minutes=60, duty_names=["John Doe"])
        for status in ("6", "8", "9"):
            uids, bodies = lf.build_feed(
                [record(health_status=status, notes="Speaker: John Doe")], cfg)
            self.assertEqual(uids, set(), f"status={status}")
            self.assertEqual(bodies, {}, f"status={status}")

    def test_request_status_marks_everything_tentative(self):
        cfg = config(prep_minutes=60, duty_names=["John Doe"])
        _uids, bodies = lf.build_feed(
            [record(health_status="3", notes="Speaker: John Doe")], cfg)
        self.assertTrue(all(b["status"] == "tentative" for b in bodies.values()))

    def test_every_body_carries_its_uid_as_icaluid(self):
        cfg = config(prep_minutes=60, duty_names=["John Doe"])
        _uids, bodies = lf.build_feed([record(notes="Speaker: John Doe")], cfg)
        for uid, body in bodies.items():
            self.assertEqual(body["iCalUID"], uid)


class ValidateConfigs(unittest.TestCase):
    def full(self, **overrides):
        cfg = {
            "calendar_id": "c@example.com",
            "myice_login_url": "https://x/login",
            "myice_username_field": "email",
            "myice_password_field": "password",
            "myice_credentials_param": "/myice-sync/myice-credentials",
            "myice_filter_url": "https://x/filter",
            "myice_player_id": "1", "myice_event_type": "g",
            "myice_season": "1", "myice_club": "1",
            "myice_min_date": "2026-04-01", "myice_max_date": "2027-04-30",
        }
        cfg.update(overrides)
        return cfg

    def test_a_complete_config_passes(self):
        lf.validate_configs([self.full()])

    def test_a_missing_required_field_raises(self):
        cfg = self.full()
        del cfg["myice_player_id"]
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([cfg])
        self.assertIn("myice_player_id", str(ctx.exception))

    def test_duplicate_calendar_and_prefix_raises(self):
        a, b = self.full(uid_prefix="myice-"), self.full(uid_prefix="myice-")
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([a, b])
        self.assertIn("uid_prefix", str(ctx.exception))

    def test_same_calendar_with_different_prefixes_is_fine(self):
        lf.validate_configs([self.full(uid_prefix="a-"), self.full(uid_prefix="b-")])

    def test_different_calendars_with_the_same_prefix_is_fine(self):
        lf.validate_configs([self.full(calendar_id="one@x"),
                             self.full(calendar_id="two@x")])

    def test_bad_event_type_raises(self):
        with self.assertRaises(RuntimeError):
            lf.validate_configs([self.full(myice_event_type="x")])

    def test_validation_reports_the_config_index(self):
        cfg = self.full()
        del cfg["calendar_id"]
        with self.assertRaises(RuntimeError) as ctx:
            lf.validate_configs([self.full(), cfg])
        self.assertIn("1", str(ctx.exception))


class SelectConfigs(unittest.TestCase):
    def setUp(self):
        self.configs = [{"myice_event_type": "g"}, {"myice_event_type": "p"},
                        {"myice_event_type": "g"}]

    def test_none_returns_everything(self):
        self.assertEqual(len(lf.select_configs(self.configs, None)), 3)

    def test_games_only(self):
        picked = lf.select_configs(self.configs, "g")
        self.assertTrue(all(c["myice_event_type"] == "g" for c in picked))
        self.assertEqual(len(picked), 2)

    def test_trainings_only(self):
        self.assertEqual(len(lf.select_configs(self.configs, "p")), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m unittest test_handler -v
```

Expected: FAIL — `module 'lambda_function' has no attribute 'build_feed'`.

- [ ] **Step 3: Implement feed assembly and validation**

```python
REQUIRED_FIELDS = (
    "calendar_id", "myice_login_url", "myice_username_field",
    "myice_password_field", "myice_credentials_param", "myice_filter_url",
    "myice_player_id", "myice_event_type", "myice_season", "myice_club",
    "myice_min_date", "myice_max_date",
)


def validate_configs(configs: list[dict]) -> None:
    """Check every club entry up front, so a typo fails before any syncing."""
    seen = {}
    for idx, config in enumerate(configs):
        missing = [f for f in REQUIRED_FIELDS if not config.get(f)]
        if missing:
            raise RuntimeError(
                f"Config at index {idx} is missing required field(s): {', '.join(missing)}")
        if config["myice_event_type"] not in ("g", "p"):
            raise RuntimeError(
                f"Config at index {idx} has myice_event_type "
                f"{config['myice_event_type']!r}; expected 'g' (games) or 'p' (trainings)")
        key = (config["calendar_id"], config.get("uid_prefix", DEFAULT_UID_PREFIX))
        if key in seen:
            raise RuntimeError(
                f"Configs at index {seen[key]} and {idx} share calendar_id "
                f"{key[0]!r} and uid_prefix {key[1]!r}; they would delete each "
                "other's events. Give each club a distinct uid_prefix.")
        seen[key] = idx


def build_feed(records: list[dict], config: dict) -> tuple[set, dict]:
    """
    Turn myice records into the UID/body maps plan_sync consumes.

    A record whose status says "remove" (sick, injured, excused) contributes
    nothing at all - not the event, not its prep block, not its duties. Absent
    from the feed means plan_sync deletes whatever is on the calendar.
    """
    uid_prefix = config.get("uid_prefix", DEFAULT_UID_PREFIX)
    feed_uids, feed_bodies = set(), {}

    for record in records:
        action, google_status = classify(record)
        if action == "remove":
            continue

        uid = record_uid(record, uid_prefix)
        body = record_to_google_body(record, config, action, google_status)
        derived = {uid: body}

        prep = prep_body(record, config, body, google_status)
        if prep is not None:
            derived[prep_uid(record, uid_prefix)] = prep
        derived.update(duty_bodies(record, config, body, google_status))

        for derived_uid, derived_body in derived.items():
            derived_body["iCalUID"] = derived_uid
            feed_uids.add(derived_uid)
            feed_bodies[derived_uid] = derived_body

    return feed_uids, feed_bodies


def select_configs(configs: list[dict], only: str | None) -> list[dict]:
    """Filter club entries by event type. `only` is 'g', 'p', or None."""
    if only is None:
        return list(configs)
    return [c for c in configs if c.get("myice_event_type") == only]
```

- [ ] **Step 4: Implement `sync_club` and rewrite `handler`**

```python
def get_myice_credentials(ssm_param_name: str) -> dict:
    ssm = boto3.client("ssm")
    resp = ssm.get_parameter(Name=ssm_param_name, WithDecryption=True)
    return json.loads(resp["Parameter"]["Value"])


def sync_club(service, config: dict, state: dict,
              allow_past: bool = False, plan_only: bool = False) -> dict:
    """Fetch one club's feed and plan (and unless plan_only, apply) the changes."""
    calendar_id = config["calendar_id"]
    uid_prefix = config.get("uid_prefix", DEFAULT_UID_PREFIX)

    creds = get_myice_credentials(config["myice_credentials_param"])
    session = myice_client.login(
        config["myice_login_url"], creds["username"], creds["password"],
        config["myice_username_field"], config["myice_password_field"],
        config.get("myice_login_extra_fields"),
    )
    records = myice_client.fetch_records(
        session, config["myice_filter_url"],
        player_id=config["myice_player_id"],
        event_type=config["myice_event_type"],
        season=config["myice_season"], club=config["myice_club"],
        min_date=config.get("_min_date_override") or config["myice_min_date"],
        max_date=config["myice_max_date"],
    )

    feed_uids, feed_bodies = build_feed(records, config)
    existing = list_existing_synced_events(service, calendar_id, uid_prefix)
    respect_deletes = bool(config.get("respect_manual_deletions", False))
    plan = plan_sync(feed_uids, feed_bodies, existing, state,
                     respect_deletes, allow_past=allow_past)

    counts = (calendar_sync.plan_counts(plan) if plan_only
              else calendar_sync.execute_plan(service, calendar_id, plan, existing))
    counts["total_in_feed"] = len(feed_uids)
    counts["records_fetched"] = len(records)
    return {"plan": plan, "existing": existing, "counts": counts}


def handler(event, context):
    service = get_calendar_service()
    configs = load_configs()

    if isinstance(event, dict) and event.get("action") == "purge":
        return purge_feed(service, event["calendar_id"], event["uid_prefix"],
                          confirm=event.get("confirm"))

    validate_configs(configs)

    any_respect = any(c.get("respect_manual_deletions") for c in configs)
    state = sync_state.load() if any_respect else {"synced": {}, "tombstones": {}}

    results, overall_success = [], True
    for idx, config in enumerate(configs):
        try:
            res = sync_club(service, config, state)
            entry = {"config_index": idx, "status": "success", **res["counts"]}
        except Exception as exc:
            print(f"Feed at index {idx} FAILED: {type(exc).__name__}: {exc}")
            entry = {"config_index": idx, "status": "error",
                     "error": f"{type(exc).__name__}: {exc}"}
            overall_success = False
        results.append(entry)

    # Saved even when a feed failed, so successful feeds' state is not lost.
    if any_respect:
        sync_state.save(sync_state.DEFAULT_STATE_URI, state)

    print(json.dumps({"overall_success": overall_success, "results": results}))
    if not overall_success:
        raise RuntimeError("One or more club feeds failed to sync.")
    return {"overall_success": True, "results": results}
```

Keep the existing `load_configs` helper if one exists; otherwise add:

```python
def load_configs() -> list[dict]:
    if PYTHON_CONFIGS is None:
        raise RuntimeError("sync_configs.py not found - copy sync_configs_example.py to it.")
    return PYTHON_CONFIGS
```

Add `import myice_client` to the imports.

- [ ] **Step 5: Run the full suite**

```bash
uv run python -m unittest discover -s . -p "test_*.py" -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add lambda_function.py test_handler.py
git commit -m "Assemble per-club feeds, validate configs, rewrite handler"
```

---

## Task 10: `dry_run.py` and the local CLI

Replaces the existing `--preview` mode, which re-implements the mapping inline in `run_local.py` and can therefore drift from what the sync actually does.

**Files:**
- Create: `dry_run.py`
- Create: `test_dry_run.py`
- Rewrite: `run_local.py`
- Modify: `run-local.sh`

**Interfaces:**
- Consumes: `sync_club` (Task 9), `calendar_sync.plan_counts` (Task 2).
- Produces: `dry_run.render_report(sections: list[dict]) -> str`, where each section is `{"index": int, "config": dict, "plan": dict, "counts": dict}`.

- [ ] **Step 1: Write the failing tests**

Create `test_dry_run.py`:

```python
"""Tests for the dry-run report."""
import unittest

import dry_run


def section():
    return {
        "index": 0,
        "config": {"calendar_id": "c@example.com", "uid_prefix": "myice-",
                   "myice_event_type": "g", "myice_club": "3"},
        "plan": {
            "create": [("myice-1", {"summary": "U13 vs A",
                                    "start": {"dateTime": "2099-03-14T19:30:00+01:00"}})],
            "update": [("myice-2", {"summary": "U13 vs B",
                                    "start": {"dateTime": "2099-03-15T19:30:00+01:00"}},
                        "gid-2")],
            "delete": [("myice-3", "gid-3")],
            "unchanged": ["myice-4"],
            "tombstone": ["myice-5"],
            "skip_tombstoned": [],
        },
        "counts": {"created": 1, "updated": 1, "deleted": 1, "unchanged": 1,
                   "tombstoned": 1, "skipped_tombstoned": 0, "total_in_feed": 5},
    }


class RenderReport(unittest.TestCase):
    def setUp(self):
        self.text = dry_run.render_report([section()])

    def test_lists_creates(self):
        self.assertIn("U13 vs A", self.text)
        self.assertIn("CREATE", self.text)

    def test_lists_updates_and_deletes(self):
        self.assertIn("UPDATE", self.text)
        self.assertIn("DELETE", self.text)

    def test_identifies_the_club_feed(self):
        self.assertIn("c@example.com", self.text)
        self.assertIn("myice-", self.text)

    def test_includes_a_count_summary(self):
        self.assertIn("1 created", self.text)
        self.assertIn("1 deleted", self.text)

    def test_marks_derived_entries(self):
        s = section()
        s["plan"]["create"].append(
            ("myice-prep-1", {"summary": "Warm-up: U13 vs A",
                              "start": {"dateTime": "2099-03-14T18:30:00+01:00"}}))
        s["plan"]["create"].append(
            ("myice-duty-1-abcd1234", {"summary": "Speaker: John Doe",
                                       "start": {"dateTime": "2099-03-14T19:30:00+01:00"}}))
        text = dry_run.render_report([s])
        self.assertIn("[prep]", text)
        self.assertIn("[duty]", text)

    def test_an_empty_plan_still_renders(self):
        empty = section()
        empty["plan"] = {k: [] for k in empty["plan"]}
        empty["counts"] = {k: 0 for k in empty["counts"]}
        self.assertIn("c@example.com", dry_run.render_report([empty]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run python -m unittest test_dry_run -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'dry_run'`.

- [ ] **Step 3: Write `dry_run.py`**

```python
"""
Renders a sync plan as a readable report.

Dry runs compute the *real* plan - the same plan_sync a live run uses - so the
report cannot drift from what would actually happen. It does read the calendar
(events.list); it never writes.
"""

from datetime import datetime, timezone


def _when(body: dict) -> str:
    start = body.get("start", {})
    raw = start.get("dateTime") or start.get("date") or "?"
    return raw.replace("T", " ")[:16]


def _kind(uid: str) -> str:
    if "-prep-" in uid:
        return " [prep]"
    if "-duty-" in uid:
        return " [duty]"
    return ""


def render_report(sections: list[dict]) -> str:
    lines = [
        "myice-to-calendar-sync DRY RUN",
        f"generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "nothing was created, updated or deleted",
        "",
    ]
    for section in sections:
        cfg, plan, counts = section["config"], section["plan"], section["counts"]
        kind = {"g": "games", "p": "trainings"}.get(cfg.get("myice_event_type"), "?")
        lines.append("=" * 72)
        lines.append(f"[{section['index']}] club {cfg.get('myice_club')} ({kind})"
                     f" -> {cfg.get('calendar_id')}  prefix {cfg.get('uid_prefix')}")
        lines.append("=" * 72)

        for uid, body in plan["create"]:
            lines.append(f"  CREATE    {_when(body)}  {body.get('summary')}{_kind(uid)}")
        for uid, body, _event_id in plan["update"]:
            lines.append(f"  UPDATE    {_when(body)}  {body.get('summary')}{_kind(uid)}")
        for uid, _event_id in plan["delete"]:
            lines.append(f"  DELETE    {uid}{_kind(uid)}")
        for uid in plan["tombstone"]:
            lines.append(f"  TOMBSTONE {uid}{_kind(uid)}")
        for uid in plan["skip_tombstoned"]:
            lines.append(f"  SKIPPED   {uid} (previously deleted by hand){_kind(uid)}")
        if plan["unchanged"]:
            lines.append(f"  {len(plan['unchanged'])} unchanged")

        lines.append("")
        lines.append(f"  {counts.get('created', 0)} created, "
                     f"{counts.get('updated', 0)} updated, "
                     f"{counts.get('deleted', 0)} deleted, "
                     f"{counts.get('unchanged', 0)} unchanged, "
                     f"{counts.get('tombstoned', 0)} tombstoned")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run python -m unittest test_dry_run -v
```

Expected: 6 tests PASS.

- [ ] **Step 5: Rewrite `run_local.py`**

```python
"""
Run the myice sync locally, reading the Google service-account key from a local
file (see run-local.sh) instead of AWS SSM.

  --dry-run (default)  Compute the real plan and write a report file. Reads the
                       calendar (events.list) but never writes to it.
  --apply              Actually sync. Same code path as the Lambda.

Filters and --since apply to dry runs; --since is refused with --apply, because
a live sync must never touch the past.
"""

import argparse
import sys
from datetime import datetime, timezone

import dry_run
import lambda_function as lf
import sync_state


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the myice sync locally.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Plan only and write a report (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Actually sync to Google Calendar.")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--games-only", action="store_true")
    which.add_argument("--trainings-only", action="store_true")
    parser.add_argument("--out", help="Dry-run report path.")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Dry-run only: start date override; allows past events.")
    args = parser.parse_args()

    if args.since and args.apply:
        parser.error("--since is dry-run only; a live sync never touches the past.")
    if args.since:
        try:
            datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            parser.error(f"--since must be YYYY-MM-DD, got {args.since!r}")

    only = "g" if args.games_only else "p" if args.trainings_only else None
    configs = lf.select_configs(lf.load_configs(), only)
    lf.validate_configs(configs)
    if not configs:
        print("No club feeds match that filter.")
        return

    service = lf.get_calendar_service()

    if args.apply:
        print(f"APPLY: syncing {len(configs)} club feed(s) to live Google Calendars\n")
        lf.handler({}, None)
        return

    any_respect = any(c.get("respect_manual_deletions") for c in configs)
    state = sync_state.load() if any_respect else {"synced": {}, "tombstones": {}}

    sections, failed = [], False
    for idx, config in enumerate(configs):
        if args.since:
            config = {**config, "_min_date_override": args.since}
        try:
            res = lf.sync_club(service, config, state,
                               allow_past=bool(args.since), plan_only=True)
            sections.append({"index": idx, "config": config,
                             "plan": res["plan"], "counts": res["counts"]})
        except Exception as exc:
            failed = True
            print(f"[{idx}] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)

    report = dry_run.render_report(sections)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = args.out or f"dry-run-{stamp}.txt"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report)

    print(report)
    print(f"\nReport written to {path}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Update `run-local.sh`**

Replace the `MODE` parsing block with pass-through, since the CLI now takes several flags:

```bash
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage; exit 0
fi

MODE="${*:---dry-run}"
```

Replace the usage heredoc's `Usage:` block with:

```
Usage:
  ./run-local.sh                       DRY RUN - plan and write a report file
  ./run-local.sh --games-only          Dry run, games feeds only
  ./run-local.sh --trainings-only      Dry run, training feeds only
  ./run-local.sh --since 2026-01-01    Dry run that may include past events
  ./run-local.sh --out plan.txt        Dry run, explicit report path
  ./run-local.sh --apply               Actually create/update/DELETE events
  ./run-local.sh --help                Show this help

Note: --dry-run reads the calendar to compute a real diff, so it needs the
service-account key (unlike the old --preview, which it replaces). It never
writes. --since is refused with --apply: a live sync never touches the past.
```

Then ensure the invocation passes `$MODE` unquoted-as-array — change the final python invocation to use `${MODE}` word-split, or simply `"$@"`. Prefer:

```bash
exec uv run python run_local.py "$@"
```

and delete the now-unused `MODE` variable, keeping only the `--help` short-circuit and the key-file discovery above it. Keep the `--apply` key-file existence check, gated on whether `--apply` appears in `"$@"`:

```bash
if [[ " $* " == *" --apply "* ]]; then
  KEY_FILE="${GOOGLE_SERVICE_ACCOUNT_FILE:-}"
  ...
fi
```

- [ ] **Step 7: Verify the scripts parse and the CLI wiring is sane**

```bash
bash -n run-local.sh
uv run python run_local.py --help
uv run python run_local.py --since 2026-01-01 --apply   # must exit non-zero
```

Expected: help prints; the last command errors with the `--since is dry-run only` message.

- [ ] **Step 8: Run the full suite and commit**

```bash
uv run python -m unittest discover -s . -p "test_*.py"
git add dry_run.py test_dry_run.py run_local.py run-local.sh
git commit -m "Replace --preview with a real dry-run report, filters, and --since"
```

---

## Task 11: `sync_configs_example.py`

**Files:**
- Rewrite: `sync_configs_example.py`

- [ ] **Step 1: Rewrite the file**

```python
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
  myice_login_url          the login form's POST URL
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
    "myice_username_field": "TODO_capture_from_devtools",
    "myice_password_field": "TODO_capture_from_devtools",
    # "myice_login_extra_fields": {"_token": "TODO_if_the_form_has_a_csrf_field"},
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
```

- [ ] **Step 2: Verify it is valid Python and the example validates**

```bash
uv run python -c "
import sync_configs_example as ex, lambda_function as lf
lf.validate_configs(ex.CONFIGS)
print(f'OK: {len(ex.CONFIGS)} entries validate')
"
```

Expected: `OK: 2 entries validate`.

- [ ] **Step 3: Commit**

```bash
git add sync_configs_example.py
git commit -m "Rewrite example config for per-club myice entries"
```

---

## Task 12: README and `docs/`

Written **after** the features work, so nothing documents a capability that does not exist. Do not start this task until Tasks 1–11 are committed and the suite is green.

**Files:**
- Rewrite: `README.md`
- Create: `docs/configuration.md`, `docs/statuses.md`, `docs/duty-entries.md`, `docs/preparation-entries.md`, `docs/dry-run.md`, `docs/capturing-ids.md`, `docs/migration.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Rewrite `README.md`**

Keep the existing structure (local setup → Google Cloud setup → SSM → configure/deploy → test → purge → cost), changing the iCal specifics to myice. It must contain:

- A title and one-paragraph purpose: syncs myice.hockey games and trainings to Google Calendar, **including pending entries that myice's own ical export silently drops** (only `health_status` `1` is exported).
- A "How it differs from a plain ical sync" note: this logs in and calls a private endpoint, needs stored credentials, and can break without notice.
- Setup: `uv sync`, Google service account, `aws ssm put-parameter` for **both** `/myice-sync/google-service-account` and `/myice-sync/myice-credentials` (the latter holding `{"username": "...", "password": "..."}` as a SecureString).
- Configure: copy `sync_configs_example.py` → `sync_configs.py`, one entry per club per event type; link to `docs/configuration.md` and `docs/capturing-ids.md`.
- Deploy: `./deploy.sh`.
- Local runs: `./run-local.sh` (dry run) and `./run-local.sh --apply`; link to `docs/dry-run.md`.
- Feature sections of 2–4 sentences each, linking to the matching `docs/` page: statuses, duty entries, preparation entries, dry run.
- A **Migration** callout linking to `docs/migration.md`.
- The cost breakdown section, updated for one Lambda.

- [ ] **Step 2: Write `docs/migration.md`**

This one is the highest-consequence page. It must say, explicitly:

> `SOURCE_TAG` is `myice-calendar-sync`. It is written to every event's
> `extendedProperties.private.source`, and the sync uses it to decide which
> calendar events it owns. If you previously ran the `myice-access` branch of
> `aws-ical-sync` against a calendar, those events are tagged `aws-ical-sync`
> and **this sync cannot see them**: it will create duplicates and will never
> clean the originals up. Before your first `--apply`, either purge them with
> the old project's purge mode, or delete them by hand.

Include the exact purge invocation from the old project and a `--dry-run` first-check step.

- [ ] **Step 3: Write `docs/capturing-ids.md`**

A DevTools walkthrough: open the Network tab, log in, find the login POST, read the form field names off the Payload tab (these become `myice_username_field` / `myice_password_field`, plus any CSRF field). Then navigate to the schedule, find the `playersfilter` POST, and read `player_id`, `season`, `club`, `event_type` off its Payload tab. Note that a player in several clubs sees a different `club` value per team, and that `season` changes once a year along with `myice_min_date` / `myice_max_date`.

- [ ] **Step 4: Write the remaining `docs/` pages**

Each page is short and concrete, drawn from the matching spec section:

- `docs/configuration.md` — the one-entry-per-club-per-type model, shared credentials, the `uid_prefix` uniqueness rule when sharing a calendar, and a full key reference table.
- `docs/statuses.md` — the `health_status` table (1/3/6/8/9 and unknown), what "request" means, and why sick/injured/excused **remove** the entry rather than skipping it.
- `docs/duty-entries.md` — the detail blob, `duty_names`, the matching rules (case- and accent-insensitive, word boundaries), the shared-surname false-positive caveat and its mitigation, and worked examples using the spec's sample blob.
- `docs/preparation-entries.md` — `prep_minutes`, that the record's own meeting time wins when present, the fallback and the at-or-after-start guard, and giving a club's games and trainings different warm-up lengths.
- `docs/dry-run.md` — the flags, the report format with a short sample, that dry runs read the calendar but never write, and why `--since` cannot be combined with `--apply`.

- [ ] **Step 5: Update `AGENTS.md`**

Correct the two stale claims: the project is now myice-only, and the test suite **does** exist. Update the tests line to note the suite is real and list the modules.

- [ ] **Step 6: Verify every link resolves**

```bash
grep -oE '\]\(docs/[a-z-]+\.md\)' README.md | tr -d '](' | sed 's/)//' | while read -r f; do
  [ -f "$f" ] || echo "BROKEN: $f"
done; echo "link check done"
```

Expected: `link check done` with no `BROKEN:` lines.

- [ ] **Step 7: Commit**

```bash
git add README.md docs/ AGENTS.md
git commit -m "Document per-club config, statuses, duty and prep entries, dry run"
```

---

## Task 13: Final verification

Nothing here changes behavior; it proves the whole thing works.

**Files:** none modified unless a check fails.

- [ ] **Step 1: Full test suite**

```bash
uv run python -m unittest discover -s . -p "test_*.py" -v
```

Expected: all PASS, zero errors, zero skips.

- [ ] **Step 2: Confirm no iCal remnants and no old identity**

```bash
grep -rn 'icalendar\|ical_url\|fetch_ical\|source_type\|--preview' \
  --include='*.py' --include='*.sh' --include='*.json' --include='*.toml' . \
  | grep -v '^./docs/superpowers/'
grep -rn 'aws-ical-sync\|/ical-sync/' --include='*.py' --include='*.sh' \
  --include='*.json' --include='*.toml' . | grep -v '^./docs/superpowers/'
```

Expected: no output from either.

- [ ] **Step 3: Confirm every module imports cleanly**

```bash
uv run python -c "
import calendar_sync, duty_parser, dry_run, myice_client, sync_state, lambda_function
print('imports OK')
"
```

Expected: `imports OK`.

- [ ] **Step 4: Real dry run against live myice.hockey data**

This is the only step that touches the network. It needs `sync_configs.py`, the service-account key, and the SSM credentials parameter.

```bash
./run-local.sh --dry-run
```

Inspect the report and confirm, by eye:
- Each configured club appears as its own section with the right calendar.
- Games have preparation entries where `prep_minutes` is set, and their start times match the club's stated meeting time where myice supplies one.
- Duty entries appear only for lines that actually name you — **check for false positives from a shared surname** and adjust `duty_names` if needed.
- No event you are `Krank` / `Verletzt` / `Entschuldigt` for appears as a create.
- Pending (`Temporär`) events carry the request template.

- [ ] **Step 5: Past-date dry run**

```bash
./run-local.sh --since 2026-08-01 --out /tmp/past-check.txt
```

Confirm past events appear in the report, then confirm the guard holds by checking that a normal dry run does **not** list them.

- [ ] **Step 6: Filter check**

```bash
./run-local.sh --games-only --out /tmp/games.txt
./run-local.sh --trainings-only --out /tmp/trainings.txt
```

Expected: each report contains only feeds of that type.

- [ ] **Step 7: Review the full diff against the spec**

```bash
git diff e68a80e..HEAD --stat
```

Re-read the spec's Scope section and confirm all seven capabilities are present.

- [ ] **Step 8: Report status to the user**

State plainly what passed, what was inspected by eye, and anything that could not be verified. **Do not** create a git remote, and **do not** push — the user deferred that. Deleting the `myice-access` branch (spec sequence step 8) already happened in `aws-ical-sync`; the branch is preserved in this repo and should stay.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Origin defect 1 — `requests` undeclared | 1 |
| Origin defect 2 — IAM missing myice param | 1 |
| Origin defect 3 — chmod noise | n/a (nothing to port) |
| Origin defect 4 — no docs, no tests | 3–12 |
| Scope — iCal removal | 5 |
| Architecture — module split | 2, 3, 4, 10 |
| Reconcile port — drop `_reconcile_events`, use `plan_sync` | 2, 9 |
| Deployment identity + `SOURCE_TAG` | 1, 12 |
| Configuration — one entry per club | 9, 11 |
| Status model | 5 |
| Duty entries | 4, 7 |
| Preparation entries | 6 |
| Dry run, filters, `--since` | 10 |
| Past-event guard | 8 |
| Error handling | 3, 5, 6, 9, 10 |
| Testing | every task |
| Documentation plan | 12 |

No spec requirement is unassigned.

**Placeholder scan:** The only `TODO_` strings are inside `sync_configs_example.py`, where they are intentional user-fill-in markers, not plan gaps.

**Type consistency checked:** `plan_sync` gains `allow_past` in Task 8 and every later caller (`sync_club`, Task 9) passes it. `SOURCE_TAG` is set once in Task 2 and imported thereafter. `DEFAULT_UID_PREFIX` is introduced in Task 7 and used in Tasks 9 and 11. `event_details` is defined in Task 5 and consumed by Task 7's `duty_bodies`. `_format_summary` is defined in Task 5 and reused in Tasks 6 and 7. `calendar_sync.plan_counts` is defined in Task 2 and used in Task 9.

**Known ordering constraint:** the tree does not fully import between Task 5 (which deletes `sync_feed`) and Task 9 (which adds `sync_club` and rewrites `handler`). `test_sync.py`'s pure `plan_sync` tests still pass throughout because they import from `calendar_sync`, not `lambda_function`. This is deliberate — the alternative is a much larger single task — but an executor should not treat a `handler`-related import error during Tasks 6–8 as a defect.

