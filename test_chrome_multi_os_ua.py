"""Deterministic regression tests: no live network requests."""
from copy import deepcopy
import unittest
from unittest.mock import MagicMock, patch

import requests

from chrome_multi_os_ua import CONFIG, UserAgentGenerator


def response(version="130.0.6723.58"):
    result = MagicMock()
    result.__enter__.return_value = result
    result.json.return_value = {"versions": [{"version": version}]}
    return result


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.network = patch("chrome_multi_os_ua.requests.get")
        self.get = self.network.start()
        self.addCleanup(self.network.stop)
        self.get.return_value = response()

    def test_constructor_does_not_fetch(self):
        UserAgentGenerator()
        self.get.assert_not_called()

    def test_desktop_formats(self):
        generator = UserAgentGenerator(chrome_version="130.0.6723.58")
        for os_type, platform in (
            ("windows", "Windows NT 10.0; Win64; x64"),
            ("mac", "Macintosh; Intel Mac OS X 10_15_7"),
            ("linux", "X11; Linux x86_64"),
        ):
            with self.subTest(os_type=os_type):
                self.assertEqual(generator.generate_user_agent(os_type),
                    f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
        self.get.assert_not_called()

    def test_android_format(self):
        ua = UserAgentGenerator(chrome_version="130.0.6723.58").generate_user_agent("android")
        self.assertEqual(ua, "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36")

    def test_ios_format_preserves_full_version(self):
        ua = UserAgentGenerator(chrome_version="130.0.6723.58").generate_user_agent("ios")
        self.assertEqual(ua, "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                         "AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0.6723.58 "
                         "Mobile/15E148 Safari/604.1")

    def test_offline_all_platforms(self):
        generator = UserAgentGenerator(offline=True)
        for platform in CONFIG["OS_OPTIONS"]:
            self.assertTrue(generator.generate_user_agent(platform).startswith("Mozilla/5.0"))
            self.assertEqual(generator.get_dynamic_chrome_version(platform),
                             CONFIG["FALLBACK_CHROME_VERSION"])
        self.get.assert_not_called()

    def test_full_version_opt_in(self):
        generator = UserAgentGenerator({"REDUCED_UA": False}, chrome_version="130.0.6723.58")
        self.assertIn("Chrome/130.0.6723.58", generator.generate_user_agent())

    def test_platform_endpoints_and_independent_caches(self):
        generator = UserAgentGenerator()
        for index, (os_type, platform) in enumerate(
            (("windows", "win"), ("mac", "mac"), ("linux", "linux"),
             ("android", "android"), ("ios", "ios"))
        ):
            version = f"{130 + index}.0.1000.1"
            self.get.return_value = response(version)
            self.assertEqual(generator.get_dynamic_chrome_version(os_type), version)
            self.assertIn(f"/platforms/{platform}/", self.get.call_args.args[0])
            self.assertEqual(self.get.call_args.kwargs["params"],
                             {"pageSize": 1, "orderBy": "version desc"})
            self.assertEqual(generator.get_dynamic_chrome_version(os_type), version)
        self.assertEqual(self.get.call_count, 5)
        self.assertEqual(generator.get_dynamic_chrome_version(), "130.0.1000.1")

    @patch("chrome_multi_os_ua.time.monotonic")
    def test_expiry_updates_generated_ua(self, clock):
        clock.return_value = 0
        generator = UserAgentGenerator({"CACHE_TTL": 10})
        self.assertIn("Chrome/130.0.0.0", generator.generate_user_agent())
        clock.return_value = 9
        generator.generate_user_agent()
        self.assertEqual(self.get.call_count, 1)
        clock.return_value = 10
        self.get.return_value = response("131.0.1000.1")
        self.assertIn("Chrome/131.0.0.0", generator.generate_user_agent())
        self.assertEqual(self.get.call_count, 2)

    @patch("chrome_multi_os_ua.time.monotonic")
    def test_failed_refresh_keeps_stale_version_and_recovers(self, clock):
        clock.return_value = 0
        generator = UserAgentGenerator({"CACHE_TTL": 10, "FAILURE_CACHE_TTL": 2})
        self.assertEqual(generator.get_dynamic_chrome_version(), "130.0.6723.58")
        clock.return_value = 10
        self.get.side_effect = requests.Timeout()
        with self.assertLogs("chrome_multi_os_ua", level="WARNING"):
            self.assertEqual(generator.get_dynamic_chrome_version(), "130.0.6723.58")
        clock.return_value = 11
        generator.get_dynamic_chrome_version()
        self.assertEqual(self.get.call_count, 2)
        clock.return_value = 12
        self.get.side_effect = None
        self.get.return_value = response("131.0.1000.1")
        self.assertEqual(generator.get_dynamic_chrome_version(), "131.0.1000.1")

    def test_network_failures_use_cached_fallback(self):
        for error in (requests.Timeout(), requests.ConnectionError(), requests.HTTPError()):
            with self.subTest(error=type(error).__name__):
                self.get.reset_mock()
                self.get.side_effect = error
                generator = UserAgentGenerator()
                with self.assertLogs("chrome_multi_os_ua", level="WARNING"):
                    self.assertEqual(generator.get_dynamic_chrome_version(),
                                     CONFIG["FALLBACK_CHROME_VERSION"])
                generator.get_dynamic_chrome_version()
                self.get.assert_called_once()

    def test_http_error_and_invalid_json(self):
        for field, error in (("raise_for_status", requests.HTTPError()),
                             ("json", ValueError("invalid JSON"))):
            with self.subTest(field=field):
                self.get.return_value = response()
                getattr(self.get.return_value, field).side_effect = error
                self.assertEqual(UserAgentGenerator().get_dynamic_chrome_version(),
                                 CONFIG["FALLBACK_CHROME_VERSION"])

    def test_malformed_api_payloads(self):
        for payload in (None, [], {}, {"versions": []}, {"versions": "bad"},
                        {"versions": [None]}, {"versions": [{}]},
                        {"versions": [{"version": "130.0\r\nInjected: yes"}]},
                        {"versions": [{"version": 130}]}):
            with self.subTest(payload=payload):
                self.get.return_value.json.return_value = payload
                self.assertEqual(UserAgentGenerator().get_dynamic_chrome_version(),
                                 CONFIG["FALLBACK_CHROME_VERSION"])

    def test_interrupts_and_programming_errors_propagate(self):
        for error in (KeyboardInterrupt(), RuntimeError("bug")):
            self.get.side_effect = error
            with self.assertRaises(type(error)):
                UserAgentGenerator().get_dynamic_chrome_version()

    def test_clear_cache(self):
        generator = UserAgentGenerator()
        generator.get_dynamic_chrome_version()
        generator.clear_cache()
        generator.get_dynamic_chrome_version()
        self.assertEqual(self.get.call_count, 2)

    def test_config_copy_and_partial_merge(self):
        original = deepcopy(CONFIG)
        config = {"OS_OPTIONS": {"linux": ["Custom Linux"]}}
        generator = UserAgentGenerator(config, offline=True)
        config["OS_OPTIONS"]["linux"].append("Changed")
        self.assertEqual(generator.config["OS_OPTIONS"]["linux"], ["Custom Linux"])
        generator.config["OS_OPTIONS"]["windows"].append("Changed")
        self.assertEqual(CONFIG, original)
        self.assertIn("Custom Linux", generator.generate_user_agent("linux"))
        self.assertIn("CriOS/", generator.generate_user_agent("ios"))

    def test_custom_choices_are_not_frozen_by_ua_cache(self):
        generator = UserAgentGenerator({"OS_OPTIONS": {"linux": ["First", "Second"]}}, offline=True)
        with patch("chrome_multi_os_ua.random.choice", side_effect=["First", "Second"]):
            self.assertIn("(First)", generator.generate_user_agent("linux"))
            self.assertIn("(Second)", generator.generate_user_agent("linux"))

    def test_custom_os_keeps_selected_format(self):
        ua = UserAgentGenerator(offline=True).generate_user_agent("android", "Custom Android")
        self.assertIn("(Custom Android)", ua)
        self.assertIn(" Mobile Safari/", ua)

    def test_aliases_and_case(self):
        generator = UserAgentGenerator(offline=True)
        for alias, canonical in ((" WIN ", "windows"), ("macOS", "mac"),
                                 ("osx", "mac"), ("iPhone", "ios")):
            self.assertEqual(generator.generate_user_agent(alias), generator.generate_user_agent(canonical))

    def test_invalid_inputs_fail_before_network(self):
        generator = UserAgentGenerator()
        for os_type in (None, [], "", "typo"):
            with self.assertRaises(ValueError):
                generator.generate_user_agent(os_type)
        for custom in ("", "bad\r\nHeader: value", "bad\x00", 123, "non-ascii \u2603"):
            with self.assertRaises(ValueError):
                generator.generate_user_agent(custom_os=custom)
        self.get.assert_not_called()

    def test_invalid_config(self):
        cases = [[], {"OS_OPTIONS": []}, {"OS_OPTIONS": {"linux": []}},
                 {"OS_OPTIONS": {"linux": "string"}}, {"OS_OPTIONS": {"bad": ["OS"]}},
                 {"OS_OPTIONS": {"linux": ["bad\n"]}}, {"CACHE_TTL": 0},
                 {"CACHE_TTL": float("nan")}, {"FAILURE_CACHE_TTL": -1},
                 {"REQUEST_TIMEOUT": True}, {"REQUEST_TIMEOUT": float("inf")},
                 {"FALLBACK_CHROME_VERSION": "123"}, {"WEBKIT_VERSION": "bad\r"},
                 {"REDUCED_UA": "yes"}, {"CHROME_VERSION_API": "http://example.com"},
                 {"CHROME_VERSION_API": "https://example.com/{unknown}"}]
        for config in cases:
            with self.subTest(config=config), self.assertRaises(ValueError):
                UserAgentGenerator(config)
        for kwargs in ({"offline": "yes"}, {"chrome_version": "bad"}):
            with self.assertRaises(ValueError):
                UserAgentGenerator(**kwargs)
        self.get.assert_not_called()

    def test_legacy_literal_api_url_and_timeout(self):
        generator = UserAgentGenerator({"CHROME_VERSION_API": "https://example.com/versions",
                                        "REQUEST_TIMEOUT": 2})
        generator.get_dynamic_chrome_version()
        self.assertEqual(self.get.call_args.args[0], "https://example.com/versions")
        self.assertEqual(self.get.call_args.kwargs["timeout"], 2)


if __name__ == "__main__":
    unittest.main()
