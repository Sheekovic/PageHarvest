# ChromeMultiOSUA compatibility

The original `chrome_multi_os_ua` module is still packaged with PageHarvest.
New projects should use `pageharvest`; existing imports keep working.

```python
from chrome_multi_os_ua import UserAgentGenerator

generator = UserAgentGenerator(
    {"CACHE_TTL": 1800, "REQUEST_TIMEOUT": 3},
    offline=False,
    chrome_version=None,
    cache_dir=".pageharvest-cache",
)
print(generator.generate_user_agent("android"))
identity = generator.generate_profile("android")
print(identity.chrome_version, identity.version_source, identity.is_stale)
print(identity.headers(client_hints=True))
generator.clear_cache()
```

Configuration dictionaries are copied and merged with defaults. `OS_OPTIONS`
merges individual platform entries; each entry is a non-empty list of printable
ASCII OS strings. Canonical names are windows/mac/linux/android/ios; calls also
accept win/macos/osx/iphone aliases. Unknown platform names raise ValueError.

| Setting | Default |
| --- | --- |
| WEBKIT_VERSION | 537.36 |
| IOS_WEBKIT_VERSION | 605.1.15 |
| IOS_SAFARI_VERSION | 604.1 |
| IOS_MOBILE_BUILD | 15E148 |
| CACHE_TTL | 3600 seconds |
| FAILURE_CACHE_TTL | 60 seconds |
| REQUEST_TIMEOUT | 5 seconds |
| REDUCED_UA | True |
| CHROME_VERSION_API | Google's stable versions URL with a {platform} placeholder |

When explicitly supplied, `FALLBACK_CHROME_VERSION` overrides the bundled
snapshot during offline generation or lookup failure, and reports source
`fallback`, with no verification timestamp and is_stale=True. The legacy CONFIG
entry remains available but only an explicit constructor override enables it.

`chrome_version` pins a four-component version and makes no network requests.
`offline=True` uses persisted or bundled versions. Construction does not access
the network or create cache directories. Successful per-platform lookups expire;
failed lookups temporarily reuse the last known value or fallback.

`get_dynamic_chrome_version(os_type="windows")` returns the full version.
`generate_user_agent(os_type="windows", custom_os=None)` returns a string.
Custom OS strings replace the OS section; os_type still selects the browser
format and version endpoint. Custom profiles omit Client Hints.

`clear_cache()` clears memory and bypasses previously persisted data for that
instance. It does not delete files. The next online call refreshes the API.
The old LRU decorator's `generate_user_agent.cache_clear()` no longer exists.
Version operations are synchronized; do not mutate instance configuration while
it is in use. Profiles are immutable snapshots of metadata at generation time.

Desktop/Android output uses reduced versions by default. Set REDUCED_UA=False
for full version tokens. Windows 10/11 share the same OS token. iOS uses CriOS
and a fixed configurable iPhone profile. Python 3.10+ is required.
