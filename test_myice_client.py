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
