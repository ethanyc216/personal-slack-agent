from __future__ import annotations

from importlib.resources import files
from pathlib import Path


DEFAULT_DEBUG_PORT = 9222
DEFAULT_DEBUG_PROBE_URL = "http://127.0.0.1:{0}/json/version".format(DEFAULT_DEBUG_PORT)
DEFAULT_LAUNCHER_APP_NAME = "Bob Chrome.app"


def default_launcher_app_path(home: Path | None = None) -> Path:
    root = Path.home() if home is None else Path(home)
    return root / "Applications" / DEFAULT_LAUNCHER_APP_NAME


def default_launcher_profile_path(home: Path | None = None) -> Path:
    root = Path.home() if home is None else Path(home)
    return root / ".cache" / "personal-slack-agent" / "chrome-profile"


def _applescript_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_launcher_applescript(home: Path | None = None) -> str:
    template = (
        files("personal_slack_agent.resources")
        .joinpath("bob_chrome.applescript")
        .read_text(encoding="utf-8")
    )
    return (
        template.replace("__DEBUG_PROBE_URL__", _applescript_escape(DEFAULT_DEBUG_PROBE_URL))
        .replace("__DEBUG_PORT__", str(DEFAULT_DEBUG_PORT))
        .replace("__PROFILE_DIR__", _applescript_escape(str(default_launcher_profile_path(home))))
    )
