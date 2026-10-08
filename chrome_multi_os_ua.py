"""Chrome user-agent generation with bounded, per-platform version caching."""

from copy import deepcopy
import logging
import math
import random
import re
import time

import requests

__all__ = ["CONFIG", "UserAgentGenerator"]
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

    def __init__(self, config=None, *, offline=False, chrome_version=None):
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
        self._version_cache = {}

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
        """Discard cached versions so the next online call fetches again."""
        self._version_cache.clear()

    def get_dynamic_chrome_version(self, os_type="windows"):
        """Return a validated stable version, stale cached version, or fallback.

        Failures are cached briefly to avoid repeated timeouts during outages.
        KeyboardInterrupt and unexpected programming errors are not suppressed.
        """
        os_type = self._normalize_os(os_type)
        if self.chrome_version is not None:
            return self.chrome_version
        if self.offline:
            return self.config["FALLBACK_CHROME_VERSION"]
        cached = self._version_cache.get(os_type)
        if cached is not None and time.monotonic() < cached[1]:
            return cached[0]
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
        except (requests.RequestException, ValueError) as exc:
            version = cached[0] if cached is not None else self.config["FALLBACK_CHROME_VERSION"]
            ttl = self.config["FAILURE_CACHE_TTL"]
            logger.warning("Chrome version lookup failed for %s (%s); using %s",
                           os_type, type(exc).__name__, version)
        self._version_cache[os_type] = (version, time.monotonic() + ttl)
        return version

    def generate_user_agent(self, os_type="windows", custom_os=None):
        """Generate a UA. os_type determines format even with custom_os."""
        os_type = self._normalize_os(os_type)
        os_choice = (_header_text(custom_os, "custom_os") if custom_os is not None
                     else random.choice(self.config["OS_OPTIONS"][os_type]))
        version = self.get_dynamic_chrome_version(os_type)
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


if __name__ == "__main__":
    generator = UserAgentGenerator()
    for platform in _PLATFORMS:
        print(generator.generate_user_agent(platform))
