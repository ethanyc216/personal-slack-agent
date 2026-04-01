from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..paths import default_config_file, default_log_file, default_state_dir


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
    subparsers.add_parser("stop", help="Stop Bob.")
    subparsers.add_parser("status", help="Show Bob status.")
    tail_parser = subparsers.add_parser("tail-log", help="Tail Bob logs.")
    tail_parser.add_argument(
        "--lines",
        type=int,
        default=40,
        help="Number of trailing lines to print (default: 40).",
    )
    subparsers.add_parser("show-config", help="Show resolved config path and contents.")
    subparsers.add_parser("doctor", help="Run Bob diagnostics.")

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
        stop_exit = main(["stop"])
        if stop_exit not in (0, 1):
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

        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not _running_pids(paths):
                break
            time.sleep(0.1)

        if _running_pids(paths):
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
            print("bob-agent is running (pid {0}).".format(running[0]))
            return 0
        pid = _read_lock_pid(paths.pid_file) or _read_lock_pid(paths.lock_file)
        if pid is None:
            print("bob-agent is not running (no lock file).")
            return 0
        print("bob-agent is not running (stale lock pid {0}).".format(pid))
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
