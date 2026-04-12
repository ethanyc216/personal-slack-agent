from __future__ import annotations

import argparse
import os
import sys
import time
from logging import Logger
from pathlib import Path
from typing import Callable

from ..config import load_config
from ..codex_runner import SubprocessCodexRunner
from ..lock import SingleInstanceLockError, acquire_single_instance_lock
from ..logging_utils import setup_logging
from ..orchestrator import BobOrchestrator
from .ctl import build_runtime_paths
from ..paths import default_config_file
from ..slack.playwright_adapter import PlaywrightSlackAdapter
from ..slack.watcher import SlackWatcher
from ..state import BobStateStore


def _seed_channel_urls(browser: PlaywrightSlackAdapter, config) -> None:
    channel_urls = {}
    for workspace in config.workspaces:
        if not workspace.slack_url:
            continue
        team_id = _workspace_team_id(workspace.slack_url)
        if not team_id:
            continue
        for channel in workspace.channels:
            if not channel.slack_channel_id:
                continue
            channel_urls[(workspace.name, channel.name)] = "https://app.slack.com/client/{0}/{1}".format(
                team_id,
                channel.slack_channel_id,
            )
    browser.set_channel_urls(channel_urls)


def _prepare_bob_codex_home(target_home: Path) -> Path:
    source_home = Path.home() / ".codex"
    bob_home = target_home
    bob_home.mkdir(parents=True, exist_ok=True)
    if not source_home.exists():
        return bob_home

    excluded = {
        "hooks.json",
        "sessions",
        "history.jsonl",
        "log",
        "logs_1.sqlite",
        "logs_1.sqlite-shm",
        "logs_1.sqlite-wal",
        "logs_2.sqlite",
        "logs_2.sqlite-shm",
        "logs_2.sqlite-wal",
        "sqlite",
        "state_5.sqlite",
        "state_5.sqlite-shm",
        "state_5.sqlite-wal",
        "shell_snapshots",
        "tmp",
        ".tmp",
        "active",
        ".git",
        ".worktrees",
        ".omx",
    }

    for source_path in source_home.iterdir():
        if source_path.name in excluded:
            continue
        target_path = bob_home / source_path.name
        if target_path.exists() or target_path.is_symlink():
            if target_path.is_dir() and not target_path.is_symlink():
                for child in target_path.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        continue
                # Fall through to unlink for symlink/file cases only.
            if target_path.is_symlink() or target_path.is_file():
                target_path.unlink()
            elif target_path.is_dir():
                continue
        target_path.symlink_to(source_path, target_is_directory=source_path.is_dir())

    target_hooks = bob_home / "hooks.json"
    if target_hooks.exists() or target_hooks.is_symlink():
        target_hooks.unlink()

    return bob_home


def _workspace_team_id(workspace_url: str) -> str | None:
    prefix = "https://app.slack.com/client/"
    if not workspace_url.startswith(prefix):
        return None
    suffix = workspace_url[len(prefix):].split("?", 1)[0].strip("/")
    parts = suffix.split("/")
    if len(parts) < 2 or not parts[0]:
        return None
    return parts[0]


def run_poll_cycle(
    watcher: SlackWatcher,
    orchestrator: BobOrchestrator,
    reconcile_request_path: Path | None = None,
    logger: Logger = None,
) -> None:
    del logger
    _drain_reconcile_requests(watcher, reconcile_request_path)
    watcher.run_cycle()
    orchestrator.process_scheduled_actions()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bob-agent", description="Run the Bob agent loop.")
    parser.add_argument(
        "--config",
        default=str(default_config_file()),
        help="Path to the Bob configuration file.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one startup cycle (config/load/lock/logging) and exit.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=_positive_float,
        default=_default_poll_interval_seconds(),
        help=(
            "Idle interval between watcher cycles in seconds "
            "(env: BOB_POLL_INTERVAL_SECONDS, default: 30)."
        ),
    )
    return parser


def run_once(config_path: Path) -> int:
    return _run_runtime(config_path=config_path, once=True, poll_interval_seconds=30.0)


def _write_pid_file(pid_file: Path) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def _refresh_runtime_markers(lock_file: Path, pid_file: Path) -> None:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    _write_pid_file(pid_file)


def _remove_pid_file(pid_file: Path) -> None:
    try:
        pid_file.unlink()
    except FileNotFoundError:
        return


def run_poll_loop(
    watcher: SlackWatcher,
    orchestrator: BobOrchestrator,
    poll_interval_seconds: float,
    lock_file: Path,
    pid_file: Path,
    stop_request_path: Path,
    reconcile_request_path: Path | None = None,
    logger: Logger = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    try:
        while True:
            _refresh_runtime_markers(lock_file, pid_file)
            if stop_request_path.exists():
                return
            try:
                run_poll_cycle(
                    watcher=watcher,
                    orchestrator=orchestrator,
                    reconcile_request_path=reconcile_request_path,
                    logger=logger,
                )
            except KeyboardInterrupt:
                raise
            except Exception:
                if logger is not None:
                    logger.exception(
                        "bob-agent poll cycle failed; continuing after %.3fs",
                        poll_interval_seconds,
                    )
            deadline = time.time() + poll_interval_seconds
            while time.time() < deadline:
                if stop_request_path.exists():
                    return
                sleep_fn(min(1.0, max(0.0, deadline - time.time())))
    except KeyboardInterrupt:
        return


def _run_runtime(config_path: Path, once: bool, poll_interval_seconds: float) -> int:
    paths = build_runtime_paths(config_file=config_path)
    try:
        config = load_config(config_path)
    except Exception as exc:
        print("bob-agent failed to load config: {0}".format(exc), file=sys.stderr)
        return 1

    logger = setup_logging(paths.log_file)
    try:
        lock_handle = acquire_single_instance_lock(paths.lock_file)
    except SingleInstanceLockError as exc:
        logger.error("bob-agent startup aborted: %s", exc)
        print(str(exc), file=sys.stderr)
        return 1

    try:
        _write_pid_file(paths.pid_file)
        try:
            logger.info(
                "bob-agent startup initialized config=%s workspaces=%d once=%s poll_interval_seconds=%.3f",
                paths.config_file,
                len(config.workspaces),
                once,
                poll_interval_seconds,
            )
            state_store = BobStateStore(paths.state_dir / "bob.sqlite3")
            state_store.initialize()
            browser = PlaywrightSlackAdapter(
                browser_mode=config.defaults.browser_mode,
                cdp_url=config.defaults.cdp_url,
                slack_signin_url=config.defaults.slack_signin_url,
                chrome_executable_path=config.defaults.chrome_executable_path,
                browser_user_data_dir=config.defaults.browser_user_data_dir,
            )
            browser.set_workspace_urls(
                {
                    workspace.name: workspace.slack_url
                    for workspace in config.workspaces
                    if workspace.slack_url
                }
            )
            browser.set_workspace_api_contexts(
                {
                    workspace.name: (workspace.slack_api_token, workspace.slack_api_origin)
                    for workspace in config.workspaces
                    if workspace.slack_api_token and workspace.slack_api_origin
                }
            )
            _seed_channel_urls(browser, config)
            bob_codex_home = _prepare_bob_codex_home(
                Path(config.defaults.bob_codex_home)
                if config.defaults.bob_codex_home is not None
                else paths.state_dir / "codex-home"
            )
            codex_runner = SubprocessCodexRunner()
            isolated_codex_runner = SubprocessCodexRunner(
                env_overrides={"CODEX_HOME": str(bob_codex_home)}
            )
            orchestrator = BobOrchestrator(
                browser=browser,
                state_store=state_store,
                codex_runner=codex_runner,
                isolated_codex_runner=isolated_codex_runner,
                config=config,
            )
            watcher = SlackWatcher(
                browser=browser,
                orchestrator=orchestrator,
                state_store=state_store,
                config=config,
                logger=logger,
            )
            try:
                _run_agent_cycles(
                    watcher=watcher,
                    orchestrator=orchestrator,
                    once=once,
                    poll_interval_seconds=poll_interval_seconds,
                    stop_request_path=paths.stop_request_file,
                    reconcile_request_path=paths.state_dir / "bob.reconcile",
                    logger=logger,
                )
            finally:
                browser.close()
        except Exception:
            logger.exception("bob-agent runtime failed")
            raise
    finally:
        _remove_pid_file(paths.pid_file)
        lock_handle.close()
    return 0


def _run_agent_cycles(
    watcher: SlackWatcher,
    orchestrator: BobOrchestrator,
    once: bool,
    poll_interval_seconds: float,
    stop_request_path: Path,
    reconcile_request_path: Path | None,
    logger: Logger,
) -> None:
    if once:
        run_poll_cycle(
            watcher=watcher,
            orchestrator=orchestrator,
            reconcile_request_path=reconcile_request_path,
            logger=logger,
        )
        return
    run_poll_loop(
        watcher=watcher,
        orchestrator=orchestrator,
        poll_interval_seconds=poll_interval_seconds,
        lock_file=stop_request_path.parent / "bob.lock",
        pid_file=stop_request_path.parent / "bob.pid",
        stop_request_path=stop_request_path,
        reconcile_request_path=reconcile_request_path,
        logger=logger,
    )
    logger.info("bob-agent interrupted, shutting down.")


def _drain_reconcile_requests(watcher: SlackWatcher, reconcile_request_path: Path | None) -> None:
    if reconcile_request_path is None or not reconcile_request_path.exists():
        return
    try:
        raw = reconcile_request_path.read_text(encoding="utf-8")
    except OSError:
        return
    try:
        reconcile_request_path.unlink()
    except FileNotFoundError:
        pass
    for line in raw.splitlines():
        workspace_name = line.strip()
        if not workspace_name:
            continue
        watcher.request_workspace_reconcile(workspace_name)


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def _default_poll_interval_seconds() -> float:
    raw = os.getenv("BOB_POLL_INTERVAL_SECONDS")
    if raw is None:
        return 30.0
    try:
        parsed = float(raw)
    except ValueError:
        return 30.0
    if parsed <= 0:
        return 30.0
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.once:
        return run_once(Path(args.config))
    return _run_runtime(
        config_path=Path(args.config),
        once=False,
        poll_interval_seconds=float(args.poll_interval_seconds),
    )


if __name__ == "__main__":
    raise SystemExit(main())
