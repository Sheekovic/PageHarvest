# ChromeMultiOSUA

Generate Chrome user-agent strings for Windows, macOS, Linux, Android, and iOS.
Online mode retrieves stable versions from Google's Version History API.
Offline mode and pinned versions make generation predictable without network access.

## Installation

Requires Python 3.10 or newer.

```sh
git clone https://github.com/Sheekovic/ChromeMultiOSUA.git
cd ChromeMultiOSUA
python -m pip install .
```

For use as a standalone module, install dependencies with
`python -m pip install -r requirements.txt`.

## Usage

```python
from chrome_multi_os_ua import UserAgentGenerator

generator = UserAgentGenerator()
print(generator.generate_user_agent())          # Windows
print(generator.generate_user_agent("mac"))
print(generator.generate_user_agent("linux"))
print(generator.generate_user_agent("android")) # Mobile token
print(generator.generate_user_agent("ios"))     # CriOS and Safari-style tokens

# No network access, including during construction.
offline = UserAgentGenerator(offline=True)
pinned = UserAgentGenerator(chrome_version="130.0.6723.58")
print(pinned.generate_user_agent("windows"))
# Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36

# Select the platform explicitly when overriding the OS text.
print(pinned.generate_user_agent(
    os_type="android", custom_os="Linux; Android 13; Pixel 7"
))
```

OS names are case-insensitive; `win`, `macos`, `osx`, and `iphone` are accepted
aliases. Unknown names raise `ValueError`. Custom OS text must be non-empty
printable ASCII without control characters. It replaces only the OS section:
`os_type` still controls version lookup and desktop/mobile formatting.

Desktop and Android defaults follow
[Chromium's reduced UA format](https://www.chromium.org/updates/ua-reduction/):
the Chrome version becomes `major.0.0.0`, and OS/device values are frozen.
Windows 10 and Windows 11 share the same UA OS token. iOS uses the
[Chrome for iOS format](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/ios/user_agent.md)
with `CriOS/<full version>`. Its default OS, WebKit, Mobile, and Safari tokens
are a configurable iPhone profile, not automatic device detection.

These are UA strings only; they do not emulate a browser's JavaScript APIs,
TLS fingerprint, or User-Agent Client Hints.

## Version lookup and caching

- Construction performs no HTTP requests. First generation fetches on demand.
- Each platform uses its own stable-version endpoint and cache.
- Successful lookups expire after one hour. Requests time out after five seconds.
- On network/HTTP/JSON errors, reuse the previous version for that platform.
  If none exists, use the fixed historical fallback `120.0.6099.109`.
  This fallback is **not guaranteed current or released on every platform**;
  pin a known version when exact release matching matters.
- Failures are cached for 60 seconds before another attempt, avoiding repeated
  timeouts during outages. The library emits a warning through Python logging
  without configuring the application's root logger.
- `generator.clear_cache()` discards cached versions.
- `generator.get_dynamic_chrome_version("android")` returns the full version,
  regardless of UA reduction.
- Generated strings are not cached, so version refreshes and configured random
  OS choices take effect on each call. Instances are intended for single-thread
  use; use separate instances or external synchronization across threads.

The API request uses a one-entry page sorted by descending version; see
[Google's Version History reference](https://developer.chrome.com/docs/web-platform/versionhistory/reference).

## Configuration

Pass partial overrides; defaults and input dictionaries are deep-copied.
Individual `OS_OPTIONS` entries merge with the other platform defaults.

```python
generator = UserAgentGenerator({
    "CACHE_TTL": 1800,
    "FAILURE_CACHE_TTL": 30,
    "REQUEST_TIMEOUT": 3,
    "FALLBACK_CHROME_VERSION": "130.0.6723.58",
    "REDUCED_UA": False,  # Emit full Chrome version for desktop/Android.
    "OS_OPTIONS": {"linux": ["X11; Linux x86_64"]},
})
```

| Setting | Default / purpose |
| --- | --- |
| `OS_OPTIONS` | Non-empty lists of OS strings keyed by canonical platform name |
| `WEBKIT_VERSION` | `537.36`, desktop/Android WebKit and Safari token |
| `IOS_WEBKIT_VERSION` | `605.1.15` |
| `IOS_SAFARI_VERSION` | `604.1` |
| `IOS_MOBILE_BUILD` | `15E148` |
| `CHROME_VERSION_API` | HTTPS stable versions URL with a `{platform}` placeholder |
| `FALLBACK_CHROME_VERSION` | `120.0.6099.109`, historical offline baseline |
| `CACHE_TTL` | `3600` seconds |
| `FAILURE_CACHE_TTL` | `60` seconds |
| `REQUEST_TIMEOUT` | `5` seconds (Requests timeout, not a total operation deadline) |
| `REDUCED_UA` | `True`; does not reduce the iOS version |

A literal legacy `CHROME_VERSION_API` URL still works, but will be used for every
platform. Use the placeholder to fetch each platform's own version.
Timing values must be finite and positive. Versions require four numeric
components. Configure an instance at construction time.

## Compatibility changes

Existing imports, the optional positional configuration dictionary, and
`generate_user_agent(os_type, custom_os)` remain available. The no-argument
`get_dynamic_chrome_version()` still defaults to Windows.

This update raises the supported Python minimum from the previously documented
3.6 to 3.10. Output now uses reduced desktop/Android UAs by default, fixes mobile
formatting, rejects invalid OS names instead of silently using Windows, and
replaces fabricated random fallback versions with a documented fixed value.
Set `REDUCED_UA=False` if full desktop/Android version output is required.
The old LRU decorator's `generate_user_agent.cache_clear()` is replaced by the
instance method `clear_cache()`.

## Development

```sh
python -m pip install .
python -m unittest discover -v
python test.py
```

Tests mock all HTTP calls and clocks. They cover platform formats, configuration,
offline/pinned generation, cache expiration, failure throttling, recovery,
malformed responses, and input validation. GitHub Actions runs them on Windows
and Linux with Python 3.10, 3.13, and 3.14 and checks the installed package outside
the checkout. `test.py` is an offline example, not the regression suite.
