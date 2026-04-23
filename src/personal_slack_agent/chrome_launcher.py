from __future__ import annotations

from importlib.resources import files
from pathlib import Path
import shutil
import subprocess
import tempfile


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


def install_chrome_launcher(
    output_app: Path | None = None,
    *,
    force: bool = False,
    home: Path | None = None,
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
        source_path.write_text(render_launcher_applescript(home=home), encoding="utf-8")
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
            detail = str(exc)
            if restore_error is not None:
                detail = "{0}; restore failed: {1}; backup preserved at {2}".format(
                    detail, restore_error, backup_path
                )
            raise RuntimeError("Failed to install launcher app: {0}".format(detail)) from exc

        if backup_path.exists():
            if backup_path.is_dir():
                shutil.rmtree(backup_path)
            else:
                backup_path.unlink()

    return target
