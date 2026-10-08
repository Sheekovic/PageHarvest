"""Chrome user-agent generation with bounded, per-platform version caching."""

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import random
import re
import tempfile
from threading import RLock
import time

import requests

__all__ = ["CONFIG", "BrowserProfile", "UserAgentGenerator", "ua", "profile"]
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

CONFIG = {
    "OS_OPTIONS": {
        "windows": ["Windows NT 10.0; Win64; x64"],
        "mac": ["Macintosh; Intel Mac OS X 10_15_7"],
        "linux": ["X11; Linux x86_64"],
        "android": ["Linux; Android 10; K"],
        "ios": ["iPhone; CPU iPhone OS 17_0 like Mac OS X"],
    },
    "WEBKIT_VERSION": "537.36",
    "IOS_WEBKIT_VERSION": "605.1.15",
    "IOS_SAFARI_VERSION": "604.1",
    "IOS_MOBILE_BUILD": "15E148",
    "CHROME_VERSION_API": (
        "https://versionhistory.googleapis.com/v1/chrome/platforms/"
        "{platform}/channels/stable/versions"
    ),
    # A fixed historical baseline, deliberately not advertised as current.
    "FALLBACK_CHROME_VERSION": "120.0.6099.109",
    "CACHE_TTL": 3600,
    "FAILURE_CACHE_TTL": 60,
    "REQUEST_TIMEOUT": 5,
    "REDUCED_UA": True,
}

_PLATFORMS = {
    "windows": "win", "mac": "mac", "linux": "linux",
    "android": "android", "ios": "ios",
}
_ALIASES = {"win": "windows", "macos": "mac", "osx": "mac", "iphone": "ios"}

# Verified against each platform's Google stable-version endpoint.
_BUNDLED_CHECKED_AT = 1791426914.1393082
_BUNDLED_VERSIONS = {
    "windows": "156.0.8078.12", "mac": "156.0.8078.12",
    "linux": "155.0.8059.39", "android": "156.0.8078.25",
    "ios": "155.0.8059.37",
}


@dataclass(frozen=True)
class BrowserProfile:
    """Immutable UA and version metadata captured at generation time."""

    user_agent: str
    os_type: str
    chrome_version: str
    version_source: str
    checked_at: float | None
    is_stale: bool
    client_hints_supported: bool

    def headers(self, *, client_hints=False):
        """Return a fresh header dict; opt into basic Client Hints for HTTPS."""
        if not isinstance(client_hints, bool):
            raise ValueError("client_hints must be a boolean")
        result = {"User-Agent": self.user_agent}
        if client_hints and self.client_hints_supported:
            major = self.chrome_version.split(".")[0]
            result.update({
                "Sec-CH-UA": f'"Chromium";v="{major}", "Google Chrome";v="{major}"',
                "Sec-CH-UA-Mobile": "?1" if self.os_type == "android" else "?0",
                "Sec-CH-UA-Platform": '"' + {
                    "windows": "Windows", "mac": "macOS", "linux": "Linux",
                    "android": "Android",
                }[self.os_type] + '"',
            })
        return result


@dataclass(frozen=True)
class _VersionInfo:
    version: str
    source: str
    checked_at: float | None
    is_stale: bool = False


def _header_text(value, name):
    if not isinstance(value, str) or not value.strip() or any(
        ord(char) < 32 or ord(char) > 126 for char in value
    ):
        raise ValueError(f"{name} must be non-empty printable ASCII without control characters")
    return value


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", value):
        raise ValueError("Chrome version must contain four numeric components")
    return value


class UserAgentGenerator:
    """Generate desktop and mobile UAs; construction never accesses the network.

    chrome_version pins a version and disables lookups. offline=True uses the
    configured fallback. Overrides are copied and merged with default settings.
    """

    def __init__(self, config=None, *, offline=False, chrome_version=None, cache_dir=None):
        if config is not None and not isinstance(config, dict):
            raise ValueError("config must be a dictionary")
        self.config = deepcopy(CONFIG)
        for key, value in (config or {}).items():
            if key == "OS_OPTIONS":
                if not isinstance(value, dict):
                    raise ValueError("OS_OPTIONS must be a dictionary")
                self.config[key].update(deepcopy(value))
            else:
                self.config[key] = deepcopy(value)
        for os_type, options in self.config["OS_OPTIONS"].items():
            if os_type not in _PLATFORMS:
                raise ValueError(f"Unsupported OS in OS_OPTIONS: {os_type!r}")
            if not isinstance(options, (list, tuple)) or not options:
                raise ValueError(f"OS_OPTIONS[{os_type!r}] must be a non-empty list or tuple")
            for option in options:
                _header_text(option, "OS option")
        for key in ("CACHE_TTL", "FAILURE_CACHE_TTL", "REQUEST_TIMEOUT"):
            value = self.config[key]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{key} must be a finite positive number")
        for key in ("WEBKIT_VERSION", "IOS_WEBKIT_VERSION", "IOS_SAFARI_VERSION", "IOS_MOBILE_BUILD"):
            _header_text(self.config[key], key)
        if not isinstance(self.config["REDUCED_UA"], bool):
            raise ValueError("REDUCED_UA must be a boolean")
        if not isinstance(offline, bool):
            raise ValueError("offline must be a boolean")
        api = _header_text(self.config["CHROME_VERSION_API"], "CHROME_VERSION_API")
        if not api.startswith("https://"):
            raise ValueError("CHROME_VERSION_API must use HTTPS")
        try:
            api.format(platform="win")
        except (KeyError, ValueError, IndexError, AttributeError) as exc:
            raise ValueError("CHROME_VERSION_API only supports the {platform} placeholder") from exc
        _version(self.config["FALLBACK_CHROME_VERSION"])
        self.offline = offline
        self.chrome_version = _version(chrome_version) if chrome_version is not None else None
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._custom_fallback = config is not None and "FALLBACK_CHROME_VERSION" in config
        self._version_cache = {}
        self._disk_loaded = set()
        self._lock = RLock()

    @staticmethod
    def _normalize_os(os_type):
        if not isinstance(os_type, str):
            raise ValueError("os_type must be a supported OS name")
        name = os_type.strip().lower()
        name = _ALIASES.get(name, name)
        if name not in _PLATFORMS:
            raise ValueError(f"Unsupported OS {os_type!r}; choose from {', '.join(_PLATFORMS)}")
        return name

    def clear_cache(self):
        """Discard memory cache and bypass disk until the next successful fetch."""
        with self._lock:
            self._version_cache.clear()
            self._disk_loaded.update(_PLATFORMS)

    def _cache_path(self, os_type):
        url = self.config["CHROME_VERSION_API"].format(platform=_PLATFORMS[os_type])
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        return self.cache_dir / f"{os_type}-{digest}.json"

    def _read_disk(self, os_type):
        if self.cache_dir is None or os_type in self._disk_loaded:
            return
        self._disk_loaded.add(os_type)
        try:
            with self._cache_path(os_type).open(encoding="utf-8") as stream:
                data = json.loads(stream.read(4097))
            if not isinstance(data, dict) or data.get("schema") != 1:
                raise ValueError("Unsupported cache schema")
            version = _version(data.get("version"))
            checked_at = data.get("checked_at")
            if (isinstance(checked_at, bool) or not isinstance(checked_at, (int, float))
                    or not math.isfinite(checked_at) or not 0 < checked_at <= time.time()):
                raise ValueError("Invalid cache timestamp")
            remaining = self.config["CACHE_TTL"] - (time.time() - checked_at)
            info = _VersionInfo(version, "cached", checked_at, remaining <= 0)
            self._version_cache[os_type] = (info, time.monotonic() + max(0, remaining))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring version cache for %s (%s)", os_type, type(exc).__name__)

    def _write_disk(self, os_type, info):
        if self.cache_dir is None:
            return
        temporary = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_dir,
                                             prefix=".chrome-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"schema": 1, "version": info.version,
                           "checked_at": info.checked_at}, stream)
            os.replace(temporary, self._cache_path(os_type))
        except OSError as exc:
            logger.warning("Could not persist version for %s (%s)", os_type, type(exc).__name__)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _fallback(self, os_type):
        if self._custom_fallback:
            return _VersionInfo(self.config["FALLBACK_CHROME_VERSION"], "fallback", None, True)
        return _VersionInfo(_BUNDLED_VERSIONS[os_type], "bundled", _BUNDLED_CHECKED_AT,
                            time.time() - _BUNDLED_CHECKED_AT >= self.config["CACHE_TTL"])

    def get_dynamic_chrome_version(self, os_type="windows"):
        """Return a validated stable version, stale cached version, or fallback.

        Failures are cached briefly to avoid repeated timeouts during outages.
        KeyboardInterrupt and unexpected programming errors are not suppressed.
        """
        os_type = self._normalize_os(os_type)
        with self._lock:
            return self._resolve_version(os_type).version

    def _resolve_version(self, os_type):
        if self.chrome_version is not None:
            return _VersionInfo(self.chrome_version, "pinned", None)
        self._read_disk(os_type)
        cached = self._version_cache.get(os_type)
        if self.offline:
            if cached is not None:
                info = cached[0]
                return replace(info, source="cached" if info.source == "live" else info.source,
                               is_stale=info.is_stale or
                               time.time() - info.checked_at >= self.config["CACHE_TTL"])
            return self._fallback(os_type)
        if cached is not None and time.monotonic() < cached[1]:
            info = cached[0]
            return replace(info, source="cached" if info.source == "live" else info.source)
        url = self.config["CHROME_VERSION_API"].format(platform=_PLATFORMS[os_type])
        ttl = self.config["CACHE_TTL"]
        try:
            with requests.get(
                url, params={"pageSize": 1, "orderBy": "version desc"},
                timeout=self.config["REQUEST_TIMEOUT"],
            ) as response:
                response.raise_for_status()
                data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("versions"), list):
                raise ValueError("Version API response must contain a versions list")
            versions = data["versions"]
            if not versions or not isinstance(versions[0], dict):
                raise ValueError("Version API response has no version entry")
            version = _version(versions[0].get("version"))
            info = _VersionInfo(version, "live", time.time())
            self._write_disk(os_type, info)
        except (requests.RequestException, ValueError) as exc:
            info = (replace(cached[0], source="cached", is_stale=True)
                    if cached is not None and cached[0].source in ("live", "cached")
                    else self._fallback(os_type))
            ttl = self.config["FAILURE_CACHE_TTL"]
            logger.warning("Chrome version lookup failed for %s (%s); using %s",
                           os_type, type(exc).__name__, info.version)
        self._version_cache[os_type] = (info, time.monotonic() + ttl)
        return info

    def generate_user_agent(self, os_type="windows", custom_os=None):
        """Generate a UA. os_type determines format even with custom_os."""
        return self.generate_profile(os_type, custom_os).user_agent

    def generate_profile(self, os_type="windows", custom_os=None):
        """Capture a reusable UA, header settings, and version provenance."""
        os_type = self._normalize_os(os_type)
        os_choice = (_header_text(custom_os, "custom_os") if custom_os is not None
                     else random.choice(self.config["OS_OPTIONS"][os_type]))
        with self._lock:
            info = self._resolve_version(os_type)
        user_agent = self._format_ua(os_type, os_choice, info.version)
        supports_hints = (os_type != "ios" and custom_os is None
                          and os_choice in CONFIG["OS_OPTIONS"][os_type]
                          and int(info.version.split(".")[0]) >= 89)
        return BrowserProfile(user_agent, os_type, info.version, info.source,
                              info.checked_at, info.is_stale, supports_hints)

    def _format_ua(self, os_type, os_choice, version):
        if os_type == "ios":
            return (
                f"Mozilla/5.0 ({os_choice}) AppleWebKit/{self.config['IOS_WEBKIT_VERSION']} "
                f"(KHTML, like Gecko) CriOS/{version} "
                f"Mobile/{self.config['IOS_MOBILE_BUILD']} Safari/{self.config['IOS_SAFARI_VERSION']}"
            )
        if self.config["REDUCED_UA"]:
            version = version.split(".")[0] + ".0.0.0"
        webkit = self.config["WEBKIT_VERSION"]
        mobile = " Mobile" if os_type == "android" else ""
        return (f"Mozilla/5.0 ({os_choice}) AppleWebKit/{webkit} "
                f"(KHTML, like Gecko) Chrome/{version}{mobile} Safari/{webkit}")


_default_generator = UserAgentGenerator()


def profile(os_type="windows", *, offline=False, chrome_version=None, cache_dir=None):
    """Create a profile; default calls share an in-memory version cache."""
    if offline is False and chrome_version is None and cache_dir is None:
        generator = _default_generator
    else:
        generator = UserAgentGenerator(offline=offline, chrome_version=chrome_version,
                                       cache_dir=cache_dir)
    return generator.generate_profile(os_type)


def ua(os_type="windows", *, offline=False, chrome_version=None, cache_dir=None):
    """Return just a UA string with the same options as profile()."""
    return profile(os_type, offline=offline, chrome_version=chrome_version,
                   cache_dir=cache_dir).user_agent


if __name__ == "__main__":
    generator = UserAgentGenerator()
    for platform in _PLATFORMS:
        print(generator.generate_user_agent(platform))
