"""Validated browser/platform combinations; Chrome keeps live version lookup."""

from dataclasses import dataclass
import re
import time

from chrome_multi_os_ua import UserAgentGenerator, profile as chrome_profile

_SUPPORTED = {
    "chrome": ("windows", "mac", "linux", "android", "ios"),
    "edge": ("windows", "mac", "linux"),
    "firefox": ("windows", "mac", "linux", "android"),
    "safari": ("mac", "ios"),
}
# Firefox and Edge snapshots verified from official release feeds on 2026-10-08.
# Safari is an explicit historical 18.6 profile, not a claim of the latest version.
_VERSIONS = {"firefox": "157.0.1", "edge": "154.0.4258.62", "safari": "18.6"}
_CHECKED_AT = 1791426914.1393082
_OS = {
    "windows": "Windows NT 10.0; Win64; x64",
    "mac": "Macintosh; Intel Mac OS X 10_15_7",
    "linux": "X11; Linux x86_64",
    "android": "Android 15; Mobile",
    "ios": "iPhone; CPU iPhone OS 18_6 like Mac OS X",
}


@dataclass(frozen=True)
class BrowserProfile:
    """One immutable browser identity suitable for a whole HTTP session."""

    browser: str
    os_type: str
    version: str
    user_agent: str
    version_source: str
    checked_at: float | None
    is_stale: bool

    def headers(self, *, client_hints=False):
        """Basic headers; optional low-entropy Client Hints for HTTPS requests.

        Hints model brand, major version, platform and mobile state, not a complete
        browser fingerprint. Browser-specific GREASE brand ordering is omitted.
        """
        if not isinstance(client_hints, bool):
            raise ValueError("client_hints must be a boolean")
        result = {"User-Agent": self.user_agent}
        major = self.version.split(".")[0]
        if client_hints and self.browser in ("chrome", "edge") and self.os_type != "ios" and int(major) >= 89:
            brand = "Google Chrome" if self.browser == "chrome" else "Microsoft Edge"
            result.update({
                "Sec-CH-UA": f'"Chromium";v="{major}", "{brand}";v="{major}"',
                "Sec-CH-UA-Mobile": "?1" if self.os_type == "android" else "?0",
                "Sec-CH-UA-Platform": '"' + {
                    "windows": "Windows", "mac": "macOS", "linux": "Linux", "android": "Android",
                }[self.os_type] + '"',
            })
        return result


def profile(os_type=None, *, browser="chrome", version=None, offline=False, cache_dir=None):
    """Get a reusable profile. Chrome supports live lookup; others use snapshots.

    Pin version for repeatable tests. Unsupported browser/OS pairs fail clearly.
    """
    if not isinstance(browser, str) or browser.strip().lower() not in _SUPPORTED:
        raise ValueError(f"browser must be one of: {', '.join(_SUPPORTED)}")
    browser = browser.strip().lower()
    if os_type is None:
        os_type = "mac" if browser == "safari" else "windows"
    os_type = UserAgentGenerator._normalize_os(os_type)
    if os_type not in _SUPPORTED[browser]:
        raise ValueError(f"{browser} supports: {', '.join(_SUPPORTED[browser])}")
    if not isinstance(offline, bool):
        raise ValueError("offline must be a boolean")
    if version is not None and (not isinstance(version, str) or
                                not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version)):
        raise ValueError("version must contain two to four numeric components")
    if browser == "chrome":
        original = chrome_profile(os_type, offline=offline, chrome_version=version, cache_dir=cache_dir)
        return BrowserProfile(browser, os_type, original.chrome_version, original.user_agent,
                              original.version_source, original.checked_at, original.is_stale)
    if browser == "edge" and version is not None and len(version.split(".")) != 4:
        raise ValueError("Edge version must contain four numeric components")
    source = "pinned" if version is not None else "bundled"
    version = version or _VERSIONS[browser]
    checked_at = None if source == "pinned" or browser == "safari" else _CHECKED_AT
    stale = source != "pinned" and (checked_at is None or time.time() - checked_at >= 3600)
    if browser == "firefox":
        # Desktop Firefox freezes the macOS token at 10.15 and reports major.0.
        platform = "Macintosh; Intel Mac OS X 10.15" if os_type == "mac" else _OS[os_type]
        ua_version = version.split(".")[0] + ".0"
        trail = ua_version if os_type == "android" else "20100101"
        text = f"Mozilla/5.0 ({platform}; rv:{ua_version}) Gecko/{trail} Firefox/{ua_version}"
    elif browser == "edge":
        chromium = version.split(".")[0] + ".0.0.0"
        text = (f"Mozilla/5.0 ({_OS[os_type]}) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{chromium} Safari/537.36 Edg/{version}")
    else:
        mobile = " Mobile/15E148" if os_type == "ios" else ""
        safari = "604.1" if os_type == "ios" else "605.1.15"
        text = (f"Mozilla/5.0 ({_OS[os_type]}) AppleWebKit/605.1.15 "
                f"(KHTML, like Gecko) Version/{version}{mobile} Safari/{safari}")
    return BrowserProfile(browser, os_type, version, text, source, checked_at, stale)


def ua(os_type=None, **options):
    """Return the user-agent string from profile()."""
    return profile(os_type, **options).user_agent
