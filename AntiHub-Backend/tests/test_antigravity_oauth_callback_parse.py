import unittest

from app.services.plugin_api_service import PluginAPIService


class TestAntigravityOAuthCallbackParse(unittest.TestCase):
    def setUp(self) -> None:
        # _parse_google_oauth_callback 不依赖 db/redis，这里绕过 __init__
        self.svc = PluginAPIService.__new__(PluginAPIService)

    def test_parse_full_url(self) -> None:
        out = self.svc._parse_google_oauth_callback(
            "http://localhost:51121/oauth-callback?code=abc&state=xyz"
        )
        self.assertEqual(out["code"], "abc")
        self.assertEqual(out["state"], "xyz")

    def test_parse_query_only(self) -> None:
        out = self.svc._parse_google_oauth_callback("code=abc&state=xyz")
        self.assertEqual(out["code"], "abc")
        self.assertEqual(out["state"], "xyz")

    def test_parse_question_prefix(self) -> None:
        out = self.svc._parse_google_oauth_callback("?code=abc&state=xyz")
        self.assertEqual(out["code"], "abc")
        self.assertEqual(out["state"], "xyz")

    def test_parse_host_without_scheme(self) -> None:
        out = self.svc._parse_google_oauth_callback(
            "localhost:51121/oauth-callback?code=abc&state=xyz"
        )
        self.assertEqual(out["code"], "abc")
        self.assertEqual(out["state"], "xyz")

    def test_parse_fragment(self) -> None:
        out = self.svc._parse_google_oauth_callback(
            "http://localhost:51121/oauth-callback#code=abc&state=xyz"
        )
        self.assertEqual(out["code"], "abc")
        self.assertEqual(out["state"], "xyz")

    def test_parse_error(self) -> None:
        with self.assertRaises(ValueError):
            self.svc._parse_google_oauth_callback(
                "http://localhost:51121/oauth-callback?error=access_denied&state=xyz"
            )

    def test_missing_state_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.svc._parse_google_oauth_callback("code=abc")


if __name__ == "__main__":
    unittest.main()

