"""Run after installing PageHarvest. This example never accesses the network."""
from pageharvest import profile


def main():
    for browser, os_type in (("chrome", "android"), ("edge", "windows"),
                             ("firefox", "linux"), ("safari", "mac")):
        identity = profile(os_type, browser=browser, offline=True)
        print(f"{browser} / {os_type}: {identity.user_agent}")
        print(f"  source={identity.version_source}, stale={identity.is_stale}")


if __name__ == "__main__":
    main()
