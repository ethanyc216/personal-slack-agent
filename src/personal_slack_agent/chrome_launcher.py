from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit


DEFAULT_DEBUG_PORT = 9222
DEFAULT_DEBUG_PROBE_URL = "http://127.0.0.1:{0}/json/version".format(DEFAULT_DEBUG_PORT)
DEFAULT_LAUNCHER_APP_NAME = "Bob Chrome.app"
DEFAULT_CHROME_APPLICATION = "Google Chrome"


@dataclass(frozen=True)
class LauncherSettings:
    chrome_application: str
    debug_probe_url: str
    debug_port: int
    profile_dir: str


def default_launcher_app_path(home: Path | None = None) -> Path:
    root = Path.home() if home is None else Path(home)
    return root / "Applications" / DEFAULT_LAUNCHER_APP_NAME


def default_launcher_profile_path(home: Path | None = None) -> Path:
    root = Path.home() if home is None else Path(home)
    return root / ".cache" / "personal-slack-agent" / "chrome-profile"


def _applescript_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _browser_config_value(config_like: object | None, key: str) -> Any:
    if config_like is None:
        return None
    browser = getattr(config_like, "browser", config_like)
    if isinstance(browser, Mapping):
        return browser.get(key)
    return getattr(browser, key, None)


def _netloc_with_port(parsed_split_result, port: int) -> str:
    if not parsed_split_result.hostname:
        return ""
    hostname = parsed_split_result.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = "[{0}]".format(hostname)
    return "{0}:{1}".format(hostname, port)


def _launcher_url_parts(cdp_url: str | None) -> tuple[str, str, int]:
    if not cdp_url:
        return ("http", "127.0.0.1:9222", DEFAULT_DEBUG_PORT)
    parsed = urlsplit(cdp_url)
    scheme = parsed.scheme or "http"
    try:
        port = parsed.port if parsed.port is not None else DEFAULT_DEBUG_PORT
    except ValueError:
        return ("http", "127.0.0.1:9222", DEFAULT_DEBUG_PORT)
    netloc = _netloc_with_port(parsed, port)
    if not netloc:
        return ("http", "127.0.0.1:9222", DEFAULT_DEBUG_PORT)
    return (scheme, netloc, port)


def _launcher_probe_url(cdp_url: str | None) -> str:
    scheme, netloc, _port = _launcher_url_parts(cdp_url)
    if scheme == "ws":
        scheme = "http"
    elif scheme == "wss":
        scheme = "https"
    return urlunsplit((scheme, netloc, "/json/version", "", ""))


def _launcher_debug_port(cdp_url: str | None) -> int:
    _scheme, _netloc, port = _launcher_url_parts(cdp_url)
    return port


def _launcher_profile_dir(browser_user_data_dir: str | None, home: Path | None = None) -> str:
    if browser_user_data_dir:
        return str(Path(browser_user_data_dir).expanduser())
    return str(default_launcher_profile_path(home))


def _launcher_application_target(chrome_executable_path: str | None) -> str:
    if not chrome_executable_path:
        return DEFAULT_CHROME_APPLICATION
    path = Path(chrome_executable_path).expanduser()
    if path.name.endswith(".app"):
        return str(path)
    for parent in path.parents:
        if parent.name.endswith(".app"):
            return str(parent)
    raise RuntimeError(
        "Bob Chrome launcher requires browser.chrome_executable_path to be a macOS app bundle path "
        "or an executable inside <App>.app/Contents/MacOS/; got {0}.".format(path)
    )


def launcher_settings_from_config(
    config_like: object | None = None,
    *,
    home: Path | None = None,
) -> LauncherSettings:
    cdp_url = _browser_config_value(config_like, "cdp_url")
    browser_user_data_dir = _browser_config_value(config_like, "browser_user_data_dir")
    chrome_executable_path = _browser_config_value(config_like, "chrome_executable_path")
    return LauncherSettings(
        chrome_application=_launcher_application_target(chrome_executable_path),
        debug_probe_url=_launcher_probe_url(cdp_url),
        debug_port=_launcher_debug_port(cdp_url),
        profile_dir=_launcher_profile_dir(browser_user_data_dir, home=home),
    )


def render_launcher_applescript(
    home: Path | None = None,
    *,
    launcher_settings: LauncherSettings | None = None,
) -> str:
    settings = launcher_settings_from_config(home=home) if launcher_settings is None else launcher_settings
    template = (
        files("personal_slack_agent.resources")
        .joinpath("bob_chrome.applescript")
        .read_text(encoding="utf-8")
    )
    return (
        template.replace("__CHROME_APPLICATION__", _applescript_escape(settings.chrome_application))
        .replace("__DEBUG_PROBE_URL__", _applescript_escape(settings.debug_probe_url))
        .replace("__DEBUG_PORT__", str(settings.debug_port))
        .replace("__PROFILE_DIR__", _applescript_escape(settings.profile_dir))
    )


def install_chrome_launcher(
    output_app: Path | None = None,
    *,
    force: bool = False,
    home: Path | None = None,
    launcher_settings: LauncherSettings | None = None,
) -> Path:
    target = default_launcher_app_path(home) if output_app is None else Path(output_app).expanduser()
    backup_path = target.parent / "{0}.backup".format(target.name)
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not force:
        raise RuntimeError(
            "Launcher app already exists at {0}. Re-run with --force to replace it.".format(target)
        )
    if backup_path.exists():
        raise RuntimeError(
            "Launcher backup already exists at {0}. Recover or remove it before reinstalling.".format(
                backup_path
            )
        )

    with tempfile.TemporaryDirectory(prefix="bob-chrome-launcher-") as temp_dir:
        source_path = Path(temp_dir) / "Bob Chrome.applescript"
        compiled_app_path = Path(temp_dir) / DEFAULT_LAUNCHER_APP_NAME
        source_path.write_text(
            render_launcher_applescript(home=home, launcher_settings=launcher_settings),
            encoding="utf-8",
        )
        try:
            completed = subprocess.run(
                ["osacompile", "-o", str(compiled_app_path), str(source_path)],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise RuntimeError("Failed to compile launcher app: {0}".format(exc)) from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(
                "Failed to compile launcher app: {0}".format(detail or completed.returncode)
            )

        try:
            if target.exists():
                shutil.move(str(target), str(backup_path))
            shutil.move(str(compiled_app_path), str(target))
        except OSError as exc:
            restore_error = None
            cleanup_error = None
            if backup_path.exists():
                try:
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                    shutil.move(str(backup_path), str(target))
                except OSError as restore_exc:
                    restore_error = restore_exc
            elif target.exists():
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                except OSError as cleanup_exc:
                    cleanup_error = cleanup_exc
            detail = str(exc)
            if restore_error is not None:
                detail = "{0}; restore failed: {1}; backup preserved at {2}".format(
                    detail, restore_error, backup_path
                )
            elif cleanup_error is not None:
                detail = "{0}; cleanup failed: {1}; partial target left at {2}".format(
                    detail, cleanup_error, target
                )
            raise RuntimeError("Failed to install launcher app: {0}".format(detail)) from exc

        if backup_path.exists():
            if backup_path.is_dir():
                shutil.rmtree(backup_path)
            else:
                backup_path.unlink()

    return target
