import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

import requests

from dualis_client import DualisClient, DualisError, URL
from notifications import DeliveryError, notify_server


def response(status=200, payload=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload if payload is not None else {"id": "123"}).encode()
    result._content_consumed = True
    result.headers.update(headers or {})
    return result


class ClientTest(unittest.TestCase):
    def login_handler(self, client, calls):
        def send(method, url, **kwargs):
            calls.append((method, kwargs))
            if method == "POST":
                prepared = requests.Request(method, url, data=kwargs["data"]).prepare()
                calls.append(parse_qs(prepared.body, keep_blank_values=True))
                client.http.cookies.set("session", "synthetic", domain="dualis.dhbw.de", path="/scripts")
                return response(headers={"Refresh": "0; URL=https://dualis.dhbw.de/?ARGUMENTS=-N12345,-N1"})
            return response()
        return send

    def test_special_password_characters_survive_real_request_encoding(self):
        for password in ("valid&password", "valid+password", "valid%41password", "ä= ?/#"):
            with self.subTest(password=password), DualisClient() as client:
                calls = []
                with patch.object(client.http, "request", side_effect=self.login_handler(client, calls)):
                    client.login("s123456", password)
                self.assertEqual(calls[1]["pass"], [password])
                self.assertEqual(calls[1]["usrname"], ["s123456@student.dhbw-mannheim.de"])

    def test_login_and_grades_use_timeouts_and_cookie_jar(self):
        with DualisClient(timeout=(2, 7)) as client:
            calls = []
            with patch.object(client.http, "request", side_effect=self.login_handler(client, calls)):
                client.login("s123456", "password")
                client.get_grades("")
            self.assertEqual(calls[0][1]["timeout"], (2, 7))
            self.assertEqual(calls[2][1]["timeout"], (2, 7))
            prepared = client.http.prepare_request(requests.Request("GET", URL))
            self.assertEqual(prepared.headers["Cookie"], "session=synthetic")
            self.assertEqual(calls[2][1]["params"]["ARGUMENTS"], "-N12345,-N000307,")

    def test_login_rejects_bad_headers_and_cookies(self):
        for headers in ({}, {"Refresh": "0; URL=?ARGUMENTS=invalid,-N1"},
                        {"Refresh": "0; URL=?ARGUMENTS=-N123,-N1"}):
            with DualisClient() as client, patch.object(client.http, "request", return_value=response(headers=headers)):
                with self.assertRaises(DualisError):
                    client.login("s123456", "password")
                self.assertIsNone(client.session_id)

    def test_http_and_transport_errors_are_sanitized(self):
        with DualisClient() as client:
            for status in (302, 403, 500):
                with patch.object(client.http, "request", return_value=response(status)):
                    with self.assertRaises(DualisError):
                        client.login("s123456", "secret")
            with patch.object(client.http, "request", side_effect=requests.ConnectionError("secret-url")):
                with self.assertRaises(DualisError) as error:
                    client.login("s123456", "secret")
                self.assertNotIn("secret", str(error.exception))

    def test_grade_fetch_without_login_is_rejected(self):
        with DualisClient() as client:
            with self.assertRaises(DualisError):
                client.get_grades("")

    def test_webhook_waits_for_confirmation_and_limits_embed_sizes(self):
        with patch("notifications.requests.post", return_value=response()) as post:
            notify_server("x" * 5000, "https://example.invalid/synthetic", "changed", (2, 7))
        arguments = post.call_args.kwargs
        self.assertEqual(arguments["timeout"], (2, 7))
        self.assertEqual(arguments["params"], {"wait": "true"})
        self.assertFalse(arguments["allow_redirects"])
        embed = arguments["json"]["embeds"][0]
        self.assertLessEqual(len(embed["title"]), 256)
        self.assertLessEqual(len(embed["description"]), 4096)
        self.assertEqual(arguments["json"]["allowed_mentions"], {"parse": []})

    def test_webhook_rejects_error_or_unconfirmed_response(self):
        for result in (response(429, {"retry_after": 120}), response(500),
                       response(204), response(200, {}), response(200, [])):
            with self.subTest(status=result.status_code), patch("notifications.requests.post", return_value=result):
                with self.assertRaises(DeliveryError):
                    notify_server("Module", "https://example.invalid/synthetic", "new")

    def test_rate_limit_delay_and_secret_sanitization(self):
        with patch("notifications.requests.post", return_value=response(429, {"retry_after": 120.5})):
            with self.assertRaises(DeliveryError) as error:
                notify_server("Module", "https://example.invalid/synthetic", "new")
            self.assertEqual(error.exception.retry_after, 120.5)
        with patch("notifications.requests.post", side_effect=requests.ConnectionError("secret-token")):
            with self.assertRaises(DeliveryError) as error:
                notify_server("Module", "https://example.invalid/synthetic", "new")
            self.assertNotIn("secret-token", str(error.exception))


if __name__ == "__main__":
    unittest.main()
