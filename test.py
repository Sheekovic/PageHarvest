"""Offline usage examples. Run the regression suite with unittest discover."""
from chrome_multi_os_ua import UserAgentGenerator


if __name__ == "__main__":
    generator = UserAgentGenerator(offline=True)
    for os_type in ("windows", "mac", "linux", "ios", "android"):
        print(f"{os_type}: {generator.generate_user_agent(os_type)}")
