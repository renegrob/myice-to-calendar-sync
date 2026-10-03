"""Tests for the myice.hockey HTTP client (login + playersfilter fetch)."""
import unittest

import myice_client


# The real login page, reduced to the parts login() reads. Captured from
# app.myice.hockey: the form posts to a DIFFERENT path than the page it is on,
# and carries a hidden sublogin field plus a named submit button, both of which
# the backend requires.
LOGIN_PAGE_HTML = """<!DOCTYPE html><html><body>
<form role="form" action="https://app.myice.hockey/classes/process.php" method="post" enctype="multipart/form-data">
  <input type="email" name="login_email" class="form-control" placeholder="Email" required="" value=""/>
  <input type="password" name="login_password" placeholder="Password" value="" required=""/>
  <input type="hidden" name="sublogin" value="1"/>
  <button type="submit" name="login_submit" class="btn">Login</button>
</form>
</body></html>"""


class FakeResponse:
    def __init__(self, json_data=None, status=200, text=""):
        self._json = json_data if json_data is not None else {}
        self.status_code = status
        self.text = text

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

    def __init__(self, cookies=None, response=None, page_html=LOGIN_PAGE_HTML):
        self.cookies = FakeCookies(cookies or {})
        self.calls = []
        self.gets = []
        # A real requests.Session pre-populates this header. An empty dict here
        # once hid a live 403: login() used setdefault, which silently kept
        # requests' own UA - the very value nginx rejects. Keep the stub
        # faithful so that class of bug fails here instead of in production.
        self.headers = {"User-Agent": "python-requests/2.32.0"}
        self._response = response or FakeResponse()
        self._page = FakeResponse(text=page_html)

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return self._page

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


class DiscoverLoginForm(unittest.TestCase):
    def test_finds_the_action_url(self):
        action, _ = myice_client.discover_login_form(
            LOGIN_PAGE_HTML, "https://app.myice.hockey/login", "login_email")
        self.assertEqual(action, "https://app.myice.hockey/classes/process.php")

    def test_collects_hidden_and_submit_fields(self):
        _, fields = myice_client.discover_login_form(
            LOGIN_PAGE_HTML, "https://app.myice.hockey/login", "login_email")
        self.assertEqual(fields, {"sublogin": "1", "login_submit": ""})

    def test_does_not_collect_the_credential_inputs(self):
        _, fields = myice_client.discover_login_form(
            LOGIN_PAGE_HTML, "https://app.myice.hockey/login", "login_email")
        self.assertNotIn("login_email", fields)
        self.assertNotIn("login_password", fields)

    def test_resolves_a_relative_action_against_the_page_url(self):
        html = '<form action="/classes/process.php"><input name="login_email"></form>'
        action, _ = myice_client.discover_login_form(
            html, "https://app.myice.hockey/login", "login_email")
        self.assertEqual(action, "https://app.myice.hockey/classes/process.php")

    def test_ignores_forms_without_the_username_field(self):
        html = (
            '<form action="/search"><input type="hidden" name="q" value="x"></form>'
            '<form action="/classes/process.php">'
            '<input name="login_email"><input type="hidden" name="sublogin" value="1">'
            "</form>"
        )
        action, fields = myice_client.discover_login_form(
            html, "https://app.myice.hockey/login", "login_email")
        self.assertEqual(action, "https://app.myice.hockey/classes/process.php")
        self.assertEqual(fields, {"sublogin": "1"})

    def test_would_pick_up_a_future_csrf_token(self):
        html = (
            '<form action="/classes/process.php"><input name="login_email">'
            '<input type="hidden" name="_token" value="abc123"></form>'
        )
        _, fields = myice_client.discover_login_form(
            html, "https://app.myice.hockey/login", "login_email")
        self.assertEqual(fields["_token"], "abc123")

    def test_falls_back_to_the_page_url_when_no_form_matches(self):
        action, fields = myice_client.discover_login_form(
            "<html><body>no form here</body></html>",
            "https://app.myice.hockey/login", "login_email")
        self.assertEqual(action, "https://app.myice.hockey/login")
        self.assertEqual(fields, {})


PAGE = "https://app.myice.hockey/login"
ACTION = "https://app.myice.hockey/classes/process.php"


def do_login(session, **kw):
    return myice_client.login(
        PAGE, kw.pop("username", "u"), kw.pop("password", "p"),
        "login_email", "login_password", session=session, **kw,
    )


class Login(unittest.TestCase):
    def test_returns_session_when_auth_cookie_present(self):
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        self.assertIs(do_login(session), session)

    def test_raises_when_auth_cookie_absent(self):
        session = FakeSession(cookies={"other": "x"})
        with self.assertRaises(RuntimeError) as ctx:
            do_login(session)
        self.assertIn("login", str(ctx.exception).lower())

    def test_gets_the_login_page_before_posting(self):
        """The GET is what sets the PHP session cookie the POST depends on."""
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        do_login(session)
        self.assertEqual([u for u, _ in session.gets], [PAGE])

    def test_posts_to_the_discovered_action_not_the_page(self):
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        do_login(session)
        self.assertEqual(session.calls[0][0], ACTION)

    def test_sets_a_browser_user_agent(self):
        """nginx serves 403 to the default python-requests UA."""
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        do_login(session)
        self.assertIn("Mozilla/", session.headers["User-Agent"])

    def test_does_not_override_a_caller_supplied_user_agent(self):
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        session.headers["User-Agent"] = "custom/1.0"
        do_login(session)
        self.assertEqual(session.headers["User-Agent"], "custom/1.0")

    def test_sends_credentials_as_multipart(self):
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        do_login(session, username="user@example.com", password="secret")
        _url, kwargs = session.calls[0]
        self.assertEqual(kwargs["files"]["login_email"], (None, "user@example.com"))
        self.assertEqual(kwargs["files"]["login_password"], (None, "secret"))

    def test_sends_the_scraped_hidden_and_submit_fields(self):
        """sublogin=1 is required by the backend and is never in config."""
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        do_login(session)
        files = session.calls[0][1]["files"]
        self.assertEqual(files["sublogin"], (None, "1"))
        self.assertEqual(files["login_submit"], (None, ""))

    def test_extra_fields_are_layered_on_top_of_scraped_ones(self):
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        do_login(session, extra_fields={"sublogin": "9", "custom": "x"})
        files = session.calls[0][1]["files"]
        self.assertEqual(files["sublogin"], (None, "9"))
        self.assertEqual(files["custom"], (None, "x"))

    def test_a_failed_page_fetch_propagates(self):
        session = FakeSession(cookies={"mih_v3_token": "abc"})
        session._page = FakeResponse(status=403, text="")
        with self.assertRaises(RuntimeError):
            do_login(session)
        self.assertEqual(session.calls, [])  # never posted credentials


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


class SwitchClub(unittest.TestCase):
    """The club lives in the session, not in the playersfilter body."""

    def test_derives_the_switch_url_from_the_filter_url_origin(self):
        session = FakeSession()
        myice_client.switch_club(session, "https://app.myice.hockey/api/players/playersfilter", "113")
        self.assertEqual([u for u, _ in session.gets], ["https://app.myice.hockey/?cl=113"])

    def test_a_failed_switch_propagates(self):
        session = FakeSession()
        session._page = FakeResponse(status=500)
        with self.assertRaises(RuntimeError):
            myice_client.switch_club(session, "https://app.myice.hockey/api/x", "113")


class FetchRecordsSwitchesClubFirst(unittest.TestCase):
    def test_switches_before_posting(self):
        """Without this, two feeds for different clubs return identical records."""
        session = FakeSession(response=FakeResponse({"data": []}))
        myice_client.fetch_records(
            session, "https://app.myice.hockey/api/players/playersfilter",
            player_id="40991", event_type="g", season="11", club="113",
            min_date="2026-04-01", max_date="2027-04-30",
        )
        self.assertEqual([u for u, _ in session.gets], ["https://app.myice.hockey/?cl=113"])
        self.assertEqual(len(session.calls), 1)

    def test_each_club_switches_to_its_own(self):
        for club in ("113", "7"):
            session = FakeSession(response=FakeResponse({"data": []}))
            myice_client.fetch_records(
                session, "https://app.myice.hockey/api/players/playersfilter",
                player_id="40991", event_type="g", season="11", club=club,
                min_date="2026-04-01", max_date="2027-04-30",
            )
            self.assertIn(f"?cl={club}", session.gets[0][0])


if __name__ == "__main__":
    unittest.main()
