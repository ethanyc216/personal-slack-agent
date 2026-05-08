from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..chrome_launcher import (
    default_launcher_app_path,
    install_chrome_launcher,
    launcher_settings_from_config,
)
from ..codex_runner import SubprocessCodexRunner
from ..config import load_config
from ..models import (
    AppConfig,
    ChannelConfig,
    SessionStatus,
    WatcherSettings,
    WorkspaceConfig,
)
from ..paths import default_config_file, default_log_file, default_state_dir
from ..slack.playwright_adapter import PlaywrightSlackAdapter
from ..state import BobStateStore


@dataclass
class RuntimePaths:
    state_dir: Path
    log_file: Path
    lock_file: Path
    pid_file: Path
    config_file: Path
    stop_request_file: Path


def build_runtime_paths(
    state_dir: Optional[Path] = None, config_file: Optional[Path] = None
) -> RuntimePaths:
    resolved_state_dir = Path(state_dir).expanduser() if state_dir is not None else default_state_dir()
    return RuntimePaths(
        state_dir=resolved_state_dir,
        log_file=default_log_file() if state_dir is None else resolved_state_dir / "logs" / "bob.log",
        lock_file=resolved_state_dir / "bob.lock",
        pid_file=resolved_state_dir / "bob.pid",
        config_file=Path(config_file).expanduser() if config_file is not None else default_config_file(),
        stop_request_file=resolved_state_dir / "bob.stop",
    )


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _read_lock_pid(lock_file: Path) -> Optional[int]:
    if not lock_file.exists():
        return None
    raw = lock_file.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _log_writer_pids(log_file: Path) -> list[int]:
    if not log_file.exists():
        return []
    try:
        completed = subprocess.run(
            ["lsof", "-t", str(log_file)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if completed.returncode not in (0, 1):
        return []
    pids = []
    for line in (completed.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def _running_pids(paths: RuntimePaths) -> list[int]:
    candidates = []
    for pid in (
        _read_lock_pid(paths.pid_file),
        _read_lock_pid(paths.lock_file),
    ):
        if pid is not None:
            candidates.append(pid)
    candidates.extend(_log_writer_pids(paths.log_file))
    result = []
    seen = set()
    for pid in candidates:
        if pid in seen:
            continue
        seen.add(pid)
        if _is_pid_running(pid):
            result.append(pid)
    return result


def _marker_mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _heartbeat_age_seconds(paths: RuntimePaths) -> Optional[float]:
    mtimes = [
        mtime
        for mtime in (_marker_mtime(paths.pid_file), _marker_mtime(paths.lock_file))
        if mtime is not None
    ]
    if not mtimes:
        return None
    return max(0.0, time.time() - max(mtimes))


def _format_duration_ago(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    total_seconds = max(0, int(seconds))
    if total_seconds < 60:
        return "{0}s ago".format(total_seconds)
    total_minutes = total_seconds // 60
    if total_minutes < 60:
        return "{0}m ago".format(total_minutes)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if minutes == 0:
        return "{0}h ago".format(hours)
    return "{0}h {1}m ago".format(hours, minutes)


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def _heartbeat_stale_seconds(config: Optional[AppConfig]) -> float:
    if config is None:
        return WatcherSettings().heartbeat_stale_seconds
    return float(config.watcher.heartbeat_stale_seconds)


def _heartbeat_status_text(running: bool, stale: bool, age_seconds: Optional[float]) -> str:
    if not running:
        return "not running"
    if stale:
        return "running but stale: last loop heartbeat {0}".format(
            _format_duration_ago(age_seconds)
        )
    return "running: last loop heartbeat {0}".format(_format_duration_ago(age_seconds))


def _load_config_for_status(paths: RuntimePaths) -> Optional[AppConfig]:
    try:
        return load_config(paths.config_file)
    except Exception:
        return None


def _runtime_heartbeat_rows(
    paths: RuntimePaths,
    config: Optional[AppConfig],
) -> list[tuple[str, str]]:
    running = _running_pids(paths)
    pid = running[0] if running else (
        _read_lock_pid(paths.pid_file) or _read_lock_pid(paths.lock_file)
    )
    age_seconds = _heartbeat_age_seconds(paths)
    stale_seconds = _heartbeat_stale_seconds(config)
    stale = bool(running and age_seconds is not None and age_seconds > stale_seconds)
    rows = [
        ("heartbeat_status", _heartbeat_status_text(bool(running), stale, age_seconds)),
        ("heartbeat_running", "True" if bool(running) else "False"),
        ("heartbeat_stale", "True" if stale else "False"),
        ("heartbeat_stale_seconds", _format_number(stale_seconds)),
        ("heartbeat_age", _format_duration_ago(age_seconds)),
    ]
    if pid is not None:
        rows.append(("heartbeat_pid", str(pid)))
    return rows


def _is_cdp_reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/json/version", timeout=2.0) as response:
            return 200 <= int(getattr(response, "status", 0)) < 300
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def _wait_for_process_exit(
    paths: RuntimePaths,
    timeout_seconds: float,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _running_pids(paths):
            return True
        sleep_fn(0.1)
    return not _running_pids(paths)


def _terminate_pid(pid: int) -> None:
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bobctl",
        description="Control the local Bob launch agent and process state.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start Bob.")
    start_parser.add_argument(
        "--config",
        default=str(default_config_file()),
        help="Path to the Bob configuration file to pass to bob-agent.",
    )
    start_parser.add_argument(
        "--poll-interval-seconds",
        type=_positive_float,
        default=30.0,
        help="Polling interval for bob-agent in seconds (default: 30).",
    )
    restart_parser = subparsers.add_parser("restart", help="Restart Bob.")
    restart_parser.add_argument(
        "--config",
        default=str(default_config_file()),
        help="Path to the Bob configuration file to pass to bob-agent.",
    )
    restart_parser.add_argument(
        "--poll-interval-seconds",
        type=_positive_float,
        default=30.0,
        help="Polling interval for bob-agent in seconds (default: 30).",
    )
    restart_parser.add_argument(
        "--force",
        action="store_true",
        help="Force-stop bob-agent if cooperative restart does not complete in time.",
    )
    stop_parser = subparsers.add_parser("stop", help="Stop Bob.")
    stop_parser.add_argument(
        "--force",
        action="store_true",
        help="Force-stop bob-agent with SIGTERM if cooperative stop does not complete in time.",
    )
    subparsers.add_parser("status", help="Show Bob status.")
    install_launcher_parser = subparsers.add_parser(
        "install-chrome-launcher",
        help="Install the Bob Chrome Dock launcher app.",
    )
    install_launcher_parser.add_argument(
        "--output-app",
        default=str(default_launcher_app_path()),
        help="Where to write the compiled Bob Chrome.app bundle.",
    )
    install_launcher_parser.add_argument(
        "--config",
        default=str(default_config_file()),
        help="Path to the Bob configuration file used to render the launcher browser settings.",
    )
    install_launcher_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing launcher app at the output path.",
    )
    tail_parser = subparsers.add_parser("tail-log", help="Tail Bob logs.")
    tail_parser.add_argument(
        "--lines",
        type=int,
        default=40,
        help="Number of trailing lines to print (default: 40).",
    )
    show_config_parser = subparsers.add_parser(
        "show-config",
        help="Show resolved config path and contents.",
    )
    show_config_parser.add_argument(
        "--config",
        default=str(default_config_file()),
        help="Path to the Bob configuration file to inspect.",
    )
    doctor_parser = subparsers.add_parser("doctor", help="Run Bob diagnostics.")
    doctor_parser.add_argument(
        "--config",
        default=str(default_config_file()),
        help="Path to the Bob configuration file to diagnose.",
    )
    smoke_parser = subparsers.add_parser("smoke-test", help="Run a live Bob smoke test.")
    smoke_parser.add_argument("--workspace", help="Workspace name from bob.toml.")
    smoke_parser.add_argument("--channel", help="Channel name from bob.toml.")
    smoke_parser.add_argument(
        "--text",
        default="Bob, please reply with exactly smoke ok and nothing else.",
        help="Root Bob message to post for the smoke test.",
    )
    smoke_parser.add_argument(
        "--timeout-seconds",
        type=_positive_float,
        default=45.0,
        help="How long to wait for Bob to complete the smoke test (default: 45).",
    )
    smoke_parser.add_argument(
        "--poll-interval-seconds",
        type=_positive_float,
        default=1.0,
        help="How frequently to poll Bob state during the smoke test (default: 1).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = build_runtime_paths(
        config_file=Path(args.config) if getattr(args, "config", None) is not None else None
    )

    if args.command == "start":
        running = _running_pids(paths)
        if running:
            print("bob-agent is already running (pid {0}).".format(running[0]))
            return 0
        _remove_lock_file(paths.stop_request_file)
        pid = _read_lock_pid(paths.pid_file) or _read_lock_pid(paths.lock_file)
        if pid is not None and not _is_pid_running(pid):
            _remove_lock_file(paths.lock_file)
            _remove_lock_file(paths.pid_file)
            print("Removed stale lock pid {0} before start.".format(pid))

        cmd = [
            str(Path(sys.executable)),
            "-m",
            "personal_slack_agent.cli.agent",
            "--config",
            str(paths.config_file),
            "--poll-interval-seconds",
            str(float(args.poll_interval_seconds)),
        ]
        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            print("Failed to start bob-agent: {0}".format(exc), file=sys.stderr)
            return 1

        print("Started bob-agent in background (pid {0}).".format(process.pid))
        return 0

    if args.command == "restart":
        stop_argv = ["stop"]
        if bool(getattr(args, "force", False)):
            stop_argv.append("--force")
        stop_exit = main(stop_argv)
        if bool(getattr(args, "force", False)):
            if stop_exit != 0:
                return stop_exit
        elif stop_exit == 1:
            if _wait_for_process_exit(paths, timeout_seconds=float(args.poll_interval_seconds)):
                _remove_lock_file(paths.lock_file)
                _remove_lock_file(paths.pid_file)
                _remove_lock_file(paths.stop_request_file)
                print("Stopped bob-agent after waiting one poll interval.")
            else:
                running = _running_pids(paths)
                pid_text = " pid {0}".format(running[0]) if running else ""
                print(
                    "bob-agent is still running{0} after waiting one poll interval; "
                    "use `bobctl restart --force` to stop it immediately.".format(pid_text)
                )
                return 1
        elif stop_exit != 0:
            return stop_exit
        return main(
            [
                "start",
                "--config",
                str(paths.config_file),
                "--poll-interval-seconds",
                str(float(args.poll_interval_seconds)),
            ]
        )

    if args.command == "stop":
        running = _running_pids(paths)
        pid = running[0] if running else (_read_lock_pid(paths.pid_file) or _read_lock_pid(paths.lock_file))
        if pid is None:
            if paths.lock_file.exists():
                _remove_lock_file(paths.lock_file)
            if paths.pid_file.exists():
                _remove_lock_file(paths.pid_file)
                print("Removed stale pid/lock state with unreadable pid.")
            else:
                print("bob-agent is not running (no lock file).")
            return 0
        if not running and not _is_pid_running(pid):
            _remove_lock_file(paths.lock_file)
            _remove_lock_file(paths.pid_file)
            print("Removed stale lock pid {0}.".format(pid))
            return 0

        paths.stop_request_file.parent.mkdir(parents=True, exist_ok=True)
        paths.stop_request_file.write_text("stop\n", encoding="utf-8")

        if not _wait_for_process_exit(paths, timeout_seconds=3.0):
            if bool(getattr(args, "force", False)):
                _terminate_pid(pid)
                if _wait_for_process_exit(paths, timeout_seconds=2.0):
                    _remove_lock_file(paths.lock_file)
                    _remove_lock_file(paths.pid_file)
                    _remove_lock_file(paths.stop_request_file)
                    print("Force-stopped bob-agent pid {0}.".format(pid))
                    return 0
                print("Force-stop sent to bob-agent pid {0}, but it is still running.".format(pid))
                return 1
            print("Requested bob-agent pid {0} stop; it should exit on the next poll tick.".format(pid))
            return 1

        _remove_lock_file(paths.lock_file)
        _remove_lock_file(paths.pid_file)
        _remove_lock_file(paths.stop_request_file)
        print("Stopped bob-agent pid {0}.".format(pid))
        return 0

    if args.command == "status":
        running = _running_pids(paths)
        if running:
            config = _load_config_for_status(paths)
            age_seconds = _heartbeat_age_seconds(paths)
            stale_seconds = _heartbeat_stale_seconds(config)
            if age_seconds is not None and age_seconds > stale_seconds:
                print(
                    "bob-agent is running but stale: last loop heartbeat {0} (pid {1}).".format(
                        _format_duration_ago(age_seconds),
                        running[0],
                    )
                )
                return 0
            print("bob-agent is running (pid {0}).".format(running[0]))
            return 0
        pid = _read_lock_pid(paths.pid_file) or _read_lock_pid(paths.lock_file)
        if pid is None:
            print("bob-agent is not running (no lock file).")
            return 0
        print("bob-agent is not running (stale lock pid {0}).".format(pid))
        return 0

    if args.command == "install-chrome-launcher":
        try:
            config = load_config(paths.config_file)
            installed_path = install_chrome_launcher(
                output_app=Path(args.output_app),
                force=bool(args.force),
                launcher_settings=launcher_settings_from_config(config),
            )
        except (RuntimeError, OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("Installed Bob Chrome launcher at {0}.".format(installed_path))
        return 0

    if args.command == "doctor":
        print("bobctl doctor")
        print("state_dir: {0}".format(paths.state_dir))
        print("log_file: {0}".format(paths.log_file))
        print("log_file_exists: {0}".format(paths.log_file.exists()))
        print("lock_file: {0}".format(paths.lock_file))
        print("lock_file_exists: {0}".format(paths.lock_file.exists()))
        print("pid_file: {0}".format(paths.pid_file))
        print("pid_file_exists: {0}".format(paths.pid_file.exists()))
        print("stop_request_file: {0}".format(paths.stop_request_file))
        print("stop_request_file_exists: {0}".format(paths.stop_request_file.exists()))
        print("config_file: {0}".format(paths.config_file))
        print("config_file_exists: {0}".format(paths.config_file.exists()))
        try:
            config = load_config(paths.config_file)
        except Exception as exc:
            print("config_loaded: False")
            print("config_error: {0}".format(exc))
            return 0
        print("config_loaded: True")
        print("cdp_url: {0}".format(config.browser.cdp_url))
        print("cdp_reachable: {0}".format(_is_cdp_reachable(config.browser.cdp_url)))
        print("workspace_count: {0}".format(len(config.workspaces)))
        channel_names = [
            "{0}:{1}".format(workspace.name, channel.name)
            for workspace in config.workspaces
            for channel in workspace.channels
        ]
        print("channel_count: {0}".format(len(channel_names)))
        for item in channel_names:
            print("channel: {0}".format(item))
        _print_doctor_probe_results(
            _collect_doctor_probe_results(paths=paths, config=config)
        )
        return 0

    if args.command == "smoke-test":
        result = _run_smoke_test(
            paths=paths,
            workspace_name=args.workspace,
            channel_name=args.channel,
            text=args.text,
            timeout_seconds=float(args.timeout_seconds),
            poll_interval_seconds=float(args.poll_interval_seconds),
        )
        print("Smoke test passed.")
        print("thread_ts: {0}".format(result["thread_ts"]))
        print("session_id: {0}".format(result["session_id"]))
        print("final_message: {0}".format(result["final_message"]))
        return 0

    if args.command == "tail-log":
        if args.lines <= 0:
            print("--lines must be a positive integer.", file=sys.stderr)
            return 2
        if not paths.log_file.exists():
            print("No log file found at {0}.".format(paths.log_file))
            return 0
        lines = paths.log_file.read_text(encoding="utf-8").splitlines()
        if not lines:
            print("Log file is empty: {0}".format(paths.log_file))
            return 0
        print("\n".join(lines[-args.lines:]))
        return 0

    if args.command == "show-config":
        print("config_file: {0}".format(paths.config_file))
        if not paths.config_file.exists():
            print("Config file not found.")
            return 0
        content = paths.config_file.read_text(encoding="utf-8")
        if not content:
            print("(empty file)")
            return 0
        redacted = []
        for line in content.splitlines():
            if "slack_api_token" in line and "=" in line:
                key, _sep, _value = line.partition("=")
                line = key.rstrip() + ' = "***REDACTED***"'
            redacted.append(line)
        rendered = "\n".join(redacted)
        print(rendered, end="" if rendered.endswith("\n") else "\n")
        return 0

    print(f"bobctl {args.command} is not implemented yet.", file=sys.stderr)
    return 2


def _run_smoke_test(
    *,
    paths: RuntimePaths,
    workspace_name: Optional[str],
    channel_name: Optional[str],
    text: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> dict:
    config = load_config(paths.config_file)
    workspace, channel = _resolve_smoke_target(config, workspace_name, channel_name)
    browser = _build_browser(config)
    try:
        thread_ts = browser.post_root_message(workspace.name, channel.name, text)
    finally:
        browser.close()
    _request_workspace_reconcile(paths.state_dir / "bob.reconcile", workspace.name)
    return _wait_for_smoke_result(
        paths=paths,
        workspace_name=workspace.name,
        channel_name=channel.name,
        thread_ts=thread_ts,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def _resolve_smoke_target(
    config: AppConfig,
    workspace_name: Optional[str],
    channel_name: Optional[str],
) -> tuple[WorkspaceConfig, ChannelConfig]:
    if not config.workspaces:
        raise RuntimeError("No workspaces are configured.")

    workspace = None
    if workspace_name is None:
        workspace = config.workspaces[0]
    else:
        for item in config.workspaces:
            if item.name == workspace_name:
                workspace = item
                break
    if workspace is None:
        raise RuntimeError("Configured workspace not found: {0}".format(workspace_name))
    if not workspace.channels:
        raise RuntimeError("Workspace has no configured channels: {0}".format(workspace.name))

    channel = None
    if channel_name is None:
        channel = workspace.channels[0]
    else:
        for item in workspace.channels:
            if item.name == channel_name:
                channel = item
                break
    if channel is None:
        raise RuntimeError(
            "Configured channel not found: {0}:{1}".format(workspace.name, channel_name)
        )
    return workspace, channel


def _build_browser(
    config: AppConfig,
    reauth_state_path: Optional[Path] = None,
) -> PlaywrightSlackAdapter:
    browser = PlaywrightSlackAdapter(
        browser_mode=config.browser.browser_mode,
        cdp_url=config.browser.cdp_url,
        slack_signin_url=config.browser.slack_signin_url,
        chrome_executable_path=config.browser.chrome_executable_path,
        browser_user_data_dir=config.browser.browser_user_data_dir,
        reauth_state_path=reauth_state_path or default_state_dir() / "slack-reauth.json",
        slack_reauth_cooldown_seconds=config.browser.slack_reauth_cooldown_seconds,
    )
    browser.set_workspace_urls(
        {workspace.name: workspace.slack_url for workspace in config.workspaces if workspace.slack_url}
    )
    browser.set_workspace_api_contexts(
        {
            workspace.name: (workspace.slack_api_token, workspace.slack_api_origin)
            for workspace in config.workspaces
            if workspace.slack_api_token and workspace.slack_api_origin
        }
    )
    channel_urls = {}
    for workspace in config.workspaces:
        team_id = _workspace_team_id(workspace.slack_url)
        if not team_id:
            continue
        for channel in workspace.channels:
            channel_id = channel.effective_slack_channel_id or channel.slack_channel_id
            if not channel_id:
                continue
            channel_urls[(workspace.name, channel.name)] = "https://app.slack.com/client/{0}/{1}".format(
                team_id,
                channel_id,
            )
    browser.set_channel_urls(channel_urls)
    return browser


def _doctor_probe(label: str, ok: bool, detail: Optional[str] = None) -> list[tuple[str, str]]:
    rows = [(label, "True" if ok else "False")]
    if detail:
        rows.append((label + "_error", detail))
    return rows


def _print_doctor_probe_results(rows: list[tuple[str, str]]) -> None:
    for key, value in rows:
        print("{0}: {1}".format(key, value))


def _collect_doctor_probe_results(*, paths: RuntimePaths, config: AppConfig) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    rows.extend(_runtime_heartbeat_rows(paths, config))
    db_path = paths.state_dir / "bob.sqlite3"
    rows.append(("db_path", str(db_path)))
    try:
        store = BobStateStore(db_path)
        store.initialize()
    except Exception as exc:
        rows.extend(_doctor_probe("db_ready", False, str(exc)))
        return rows
    rows.extend(_doctor_probe("db_ready", True))

    try:
        workspace_name, channel_name = _resolve_doctor_terminal_target(config)
        rows.append(("terminal_default_target", "{0}:{1}".format(workspace_name, channel_name)))
        _workspace, channel = _resolve_smoke_target(config, workspace_name, channel_name)
        rows.extend(
            _collect_doctor_codex_exec_probe_results(
                paths=paths,
                config=config,
                channel=channel,
            )
        )
    except Exception as exc:
        rows.append(("terminal_default_target", "False"))
        rows.append(("terminal_default_target_error", str(exc)))

    if not _is_cdp_reachable(config.browser.cdp_url):
        return rows

    browser = _build_browser(config)
    try:
        try:
            browser.connect()
        except Exception as exc:
            rows.extend(_doctor_probe("browser_attach", False, str(exc)))
            return rows
        rows.extend(_doctor_probe("browser_attach", True))

        for workspace in config.workspaces:
            workspace_label = "workspace[{0}]".format(workspace.name)
            tab_ok = False
            try:
                page = browser.select_bob_tab(workspace.slack_url)
                tab_ok = True
                rows.extend(_doctor_probe(workspace_label + ".slack_tab", True))
                rows.append((workspace_label + ".slack_tab_url", str(getattr(page, "url", ""))))
            except Exception as exc:
                rows.extend(_doctor_probe(workspace_label + ".slack_tab", False, str(exc)))

            if tab_ok:
                try:
                    _token, origin = browser.discover_api_session(workspace.name)
                    rows.extend(_doctor_probe(workspace_label + ".api_session", True))
                    rows.append((workspace_label + ".api_origin", origin))
                except Exception as exc:
                    rows.extend(_doctor_probe(workspace_label + ".api_session", False, str(exc)))

                try:
                    payload = browser.api_test(workspace.name)
                    ok = bool(payload.get("ok"))
                    detail = None
                    if not ok:
                        detail = str(payload.get("error") or "api.test failed")
                        if payload.get("detail"):
                            detail = "{0}: {1}".format(detail, payload["detail"])
                    rows.extend(_doctor_probe(workspace_label + ".api_test", ok, detail))
                except Exception as exc:
                    rows.extend(_doctor_probe(workspace_label + ".api_test", False, str(exc)))

                try:
                    browser.subscribe_to_realtime_frames(workspace.name, lambda _frame: None, lambda: None)
                    rows.extend(_doctor_probe(workspace_label + ".socket_subscribe", True))
                except Exception as exc:
                    rows.extend(_doctor_probe(workspace_label + ".socket_subscribe", False, str(exc)))

            for channel in workspace.channels:
                channel_label = "channel[{0}:{1}].channel_id".format(workspace.name, channel.name)
                try:
                    channel_id = browser.get_channel_id(workspace.name, channel.name)
                    rows.append((channel_label, channel_id))
                except Exception as exc:
                    rows.append((channel_label, "False"))
                    rows.append((channel_label + "_error", str(exc)))
    finally:
        browser.close()
    return rows


def _doctor_codex_probe_prompt() -> str:
    return (
        "Doctor probe: use the shell tool to run the command `pwd` exactly once. "
        "If the command succeeds, reply with exactly `doctor exec ok` and nothing else. "
        "If the command fails, reply with the exact failure text only."
    )


def _doctor_exec_timeout_seconds(config: AppConfig) -> float:
    configured = config.runner.codex_exec_timeout_seconds
    if configured is None:
        return 45.0
    return min(float(configured), 45.0)


def _doctor_codex_exec_command(base_runner: SubprocessCodexRunner):
    def _run(
        command: list[str],
        cwd: Optional[str] = None,
        input_text: Optional[str] = None,
    ) -> str:
        adjusted_command = list(command[:2]) + ["-c", 'model_reasoning_effort="low"'] + list(command[2:])
        return base_runner._default_exec_command(adjusted_command, cwd, input_text)

    return _run


def _build_doctor_codex_runner(
    *,
    paths: RuntimePaths,
    config: AppConfig,
    channel: ChannelConfig,
) -> SubprocessCodexRunner:
    kwargs = {"exec_timeout_seconds": _doctor_exec_timeout_seconds(config)}
    if channel.effective_codex_home_mode == "isolated":
        bob_codex_home = (
            Path(config.runner.bob_codex_home)
            if config.runner.bob_codex_home is not None
            else paths.state_dir / "codex-home"
        )
        kwargs["env_overrides"] = {"CODEX_HOME": str(bob_codex_home)}
    base_runner = SubprocessCodexRunner(**kwargs)
    return SubprocessCodexRunner(exec_command=_doctor_codex_exec_command(base_runner))


def _collect_doctor_codex_exec_probe_results(
    *,
    paths: RuntimePaths,
    config: AppConfig,
    channel: ChannelConfig,
) -> list[tuple[str, str]]:
    runner = _build_doctor_codex_runner(paths=paths, config=config, channel=channel)
    run_result = runner.run_new_session(
        prompt=_doctor_codex_probe_prompt(),
        cwd=channel.effective_default_cwd or config.defaults.default_cwd or "",
        additional_roots=list(channel.effective_additional_roots),
        sandbox_mode=channel.effective_codex_sandbox_mode,
        workspace_write_writable_roots=channel.effective_codex_workspace_write_writable_roots,
    )
    if run_result.final_output == "doctor exec ok":
        rows = _doctor_probe("terminal_codex_exec", True)
        if run_result.session_id:
            rows.append(("terminal_codex_exec_session", run_result.session_id))
        return rows

    detail = (
        run_result.failure_text
        or run_result.final_output
        or run_result.wait_message
        or "unexpected doctor codex execution result"
    )
    rows = _doctor_probe("terminal_codex_exec", False, detail)
    if run_result.session_id:
        rows.append(("terminal_codex_exec_session", run_result.session_id))
    return rows


def _resolve_doctor_terminal_target(config: AppConfig) -> tuple[str, str]:
    candidates = [
        (workspace.name, channel.name)
        for workspace in config.workspaces
        for channel in workspace.channels
        if channel.effective_post_terminal_threads_here
    ]
    if not candidates:
        raise RuntimeError(
            "No channel is configured for terminal Bob requests. Set post_terminal_threads_here = true."
        )
    if len(candidates) > 1:
        raise RuntimeError(
            "Multiple terminal Bob channels are configured. Specify --workspace and --channel."
        )
    return candidates[0]


def _workspace_team_id(workspace_url: Optional[str]) -> Optional[str]:
    if not workspace_url:
        return None
    prefix = "https://app.slack.com/client/"
    if not workspace_url.startswith(prefix):
        return None
    suffix = workspace_url[len(prefix):].split("?", 1)[0].strip("/")
    parts = suffix.split("/")
    if len(parts) < 2 or not parts[0]:
        return None
    return parts[0]


def _wait_for_smoke_result(
    *,
    paths: RuntimePaths,
    workspace_name: str,
    channel_name: str,
    thread_ts: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    sleep_fn=time.sleep,
) -> dict:
    deadline = time.time() + timeout_seconds
    store = BobStateStore(paths.state_dir / "bob.sqlite3")
    store.initialize()
    last_status = None
    while time.time() < deadline:
        record = store.get_by_thread(workspace_name, channel_name, thread_ts)
        if record is not None:
            last_status = record.status
            intents = store.list_outbound_intents_for_thread(workspace_name, channel_name, thread_ts)
            final_messages = [
                intent.text
                for intent in intents
                if intent.delivery_state == "delivered" and intent.intent_key.startswith("final-")
            ]
            if final_messages:
                return {
                    "thread_ts": thread_ts,
                    "session_id": record.codex_session_id,
                    "final_message": final_messages[-1],
                }
            if record.status is SessionStatus.FAILED:
                raise RuntimeError("Smoke test failed in Bob session: {0}".format(record.codex_session_id))
        sleep_fn(poll_interval_seconds)
    raise RuntimeError(
        "Smoke test timed out waiting for Bob. Last status: {0}".format(last_status or "missing")
    )


def _request_workspace_reconcile(reconcile_request_path: Path, workspace_name: str) -> None:
    reconcile_request_path.parent.mkdir(parents=True, exist_ok=True)
    reconcile_request_path.write_text("{0}\n".format(workspace_name), encoding="utf-8")


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def _remove_lock_file(lock_file: Path) -> None:
    try:
        lock_file.unlink()
    except FileNotFoundError:
        return


if __name__ == "__main__":
    raise SystemExit(main())
