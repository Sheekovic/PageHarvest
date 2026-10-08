"""Deterministic regression tests: no live network requests."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import requests

from chrome_multi_os_ua import CONFIG, UserAgentGenerator, profile, ua, _BUNDLED_VERSIONS


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
                             _BUNDLED_VERSIONS[platform])
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
                                     _BUNDLED_VERSIONS["windows"])
                generator.get_dynamic_chrome_version()
                self.get.assert_called_once()

    def test_http_error_and_invalid_json(self):
        for field, error in (("raise_for_status", requests.HTTPError()),
                             ("json", ValueError("invalid JSON"))):
            with self.subTest(field=field):
                self.get.return_value = response()
                getattr(self.get.return_value, field).side_effect = error
                self.assertEqual(UserAgentGenerator().get_dynamic_chrome_version(),
                                 _BUNDLED_VERSIONS["windows"])

    def test_malformed_api_payloads(self):
        for payload in (None, [], {}, {"versions": []}, {"versions": "bad"},
                        {"versions": [None]}, {"versions": [{}]},
                        {"versions": [{"version": "130.0\r\nInjected: yes"}]},
                        {"versions": [{"version": 130}]}):
            with self.subTest(payload=payload):
                self.get.return_value.json.return_value = payload
                self.assertEqual(UserAgentGenerator().get_dynamic_chrome_version(),
                                 _BUNDLED_VERSIONS["windows"])

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

    def test_simple_api_and_immutable_profile(self):
        result = profile("android", chrome_version="130.0.6723.58")
        self.assertEqual(ua("android", chrome_version="130.0.6723.58"), result.user_agent)
        self.assertEqual(result.version_source, "pinned")
        self.assertFalse(result.is_stale)
        self.assertIsNone(result.checked_at)
        with self.assertRaises(FrozenInstanceError):
            result.user_agent = "changed"
        self.get.assert_not_called()

    def test_consistent_headers(self):
        for platform, label in (("windows", "Windows"), ("mac", "macOS"),
                                ("linux", "Linux"), ("android", "Android")):
            result = profile(platform, chrome_version="130.0.6723.58")
            self.assertEqual(result.headers(), {"User-Agent": result.user_agent})
            headers = result.headers(client_hints=True)
            self.assertEqual(headers["Sec-CH-UA-Platform"], f'"{label}"')
            self.assertEqual(headers["Sec-CH-UA-Mobile"], "?1" if platform == "android" else "?0")
            self.assertEqual(headers["Sec-CH-UA"], '"Chromium";v="130", "Google Chrome";v="130"')
            headers["User-Agent"] = "changed"
            self.assertEqual(result.headers()["User-Agent"], result.user_agent)

    def test_unsupported_hints_are_omitted(self):
        generator = UserAgentGenerator(chrome_version="130.0.6723.58")
        for result in (generator.generate_profile("ios"),
                       generator.generate_profile(custom_os="Custom OS"),
                       profile("windows", chrome_version="80.0.0.1"),
                       UserAgentGenerator({"OS_OPTIONS": {"windows": ["Other OS"]}},
                                          offline=True).generate_profile()):
            self.assertFalse(result.client_hints_supported)
            self.assertEqual(result.headers(client_hints=True), {"User-Agent": result.user_agent})

    def test_live_then_cached_metadata_and_snapshot_stability(self):
        generator = UserAgentGenerator()
        first = generator.generate_profile()
        second = generator.generate_profile()
        self.assertEqual(first.version_source, "live")
        self.assertEqual(second.version_source, "cached")
        self.assertFalse(second.is_stale)
        generator.clear_cache()
        self.get.return_value = response("131.0.1000.1")
        self.assertEqual(generator.generate_profile().chrome_version, "131.0.1000.1")
        self.assertIn('v="130"', first.headers(client_hints=True)["Sec-CH-UA"])

    def test_default_helpers_share_cache(self):
        with patch("chrome_multi_os_ua._default_generator", UserAgentGenerator()):
            ua()
            self.assertEqual(profile().version_source, "cached")
            self.get.assert_called_once()

    def test_bundled_and_custom_fallback_metadata(self):
        result = profile("ios", offline=True)
        self.assertEqual(result.version_source, "bundled")
        self.assertEqual(result.chrome_version, _BUNDLED_VERSIONS["ios"])
        with patch("chrome_multi_os_ua.time.time", return_value=result.checked_at + 3601):
            self.assertTrue(profile("ios", offline=True).is_stale)
        result = UserAgentGenerator({"FALLBACK_CHROME_VERSION": "120.0.6099.109"},
                                    offline=True).generate_profile()
        self.assertEqual(result.version_source, "fallback")
        self.assertEqual(result.chrome_version, "120.0.6099.109")
        self.assertTrue(result.is_stale)
        self.get.assert_not_called()

    def test_persistent_cache_and_offline_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            first = UserAgentGenerator(cache_dir=directory).generate_profile("android")
            self.get.reset_mock()
            for offline in (False, True):
                second = profile("android", offline=offline, cache_dir=directory)
                self.assertEqual(second.user_agent, first.user_agent)
                self.assertEqual(second.version_source, "cached")
                self.assertFalse(second.is_stale)
            self.get.assert_not_called()
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 1)

    def test_stale_disk_outage_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            generator = UserAgentGenerator(cache_dir=directory)
            generator.generate_profile()
            path = next(Path(directory).glob("*.json"))
            payload = json.loads(path.read_text())
            payload["checked_at"] = time.time() - 7200
            path.write_text(json.dumps(payload))
            offline = profile(offline=True, cache_dir=directory)
            self.assertTrue(offline.is_stale)
            self.get.side_effect = requests.Timeout()
            generator = UserAgentGenerator(cache_dir=directory)
            stale = generator.generate_profile()
            self.assertTrue(stale.is_stale)
            self.assertEqual(stale.version_source, "cached")
            self.assertEqual(stale.chrome_version, "130.0.6723.58")
            self.assertEqual(json.loads(path.read_text()), payload)
            self.get.side_effect = None
            self.get.return_value = response("131.0.1000.1")
            generator.clear_cache()
            recovered = generator.generate_profile()
            self.assertFalse(recovered.is_stale)
            self.assertEqual(recovered.version_source, "live")
            self.assertEqual(json.loads(path.read_text())["version"], "131.0.1000.1")

    def test_bad_disk_data_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            generator = UserAgentGenerator(cache_dir=directory)
            generator.generate_profile()
            path = next(Path(directory).glob("*.json"))
            for data in ("invalid", "[]", '{"schema":2}',
                         json.dumps({"schema": 1, "version": "bad", "checked_at": time.time()}),
                         json.dumps({"schema": 1, "version": "130.0.0.1", "checked_at": time.time() + 999}),
                         json.dumps({"schema": 1, "version": "130.0.0.1", "checked_at": True})):
                path.write_text(data)
                with self.subTest(data=data):
                    self.assertEqual(profile(offline=True, cache_dir=directory).version_source, "bundled")

    def test_unwritable_cache_does_not_break_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file"
            path.write_text("not a directory")
            result = profile(cache_dir=path)
            self.assertEqual(result.version_source, "live")

    def test_cache_endpoint_isolation_and_no_fallback_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            profile(cache_dir=directory)
            other = UserAgentGenerator({"CHROME_VERSION_API": "https://example.com/{platform}"},
                                       cache_dir=directory, offline=True)
            self.assertEqual(other.generate_profile().version_source, "bundled")
        with tempfile.TemporaryDirectory() as directory:
            self.get.side_effect = requests.Timeout()
            self.assertEqual(profile(cache_dir=directory).version_source, "bundled")
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
