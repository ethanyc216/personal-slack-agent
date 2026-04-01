from __future__ import annotations

import argparse
import os
import sys
import time
from logging import Logger
from pathlib import Path
from typing import Callable, Protocol

from ..config import load_config
from ..codex_runner import SubprocessCodexRunner
from ..lock import SingleInstanceLockError, acquire_single_instance_lock
from ..logging_utils import setup_logging
from ..models import AppConfig, SessionStatus
from ..orchestrator import BobOrchestrator
from .ctl import build_runtime_paths
from ..paths import default_config_file
from ..slack import SlackBrowserAdapter
from ..slack.playwright_adapter import PlaywrightSlackAdapter
from ..state import BobStateStore


class OrchestratorAdapter(Protocol):
    def handle_new_root_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
        author_actor_id: str,
        text: str,
    ) -> None:
        ...

    def handle_thread_reply(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        author_actor_id: str,
        text: str,
    ) -> None:
        ...


def run_poll_cycle(
    browser: SlackBrowserAdapter,
    orchestrator: OrchestratorAdapter,
    state_store: BobStateStore,
    config: AppConfig,
    logger: Logger = None,
) -> None:
    for workspace in config.workspaces:
        for channel in workspace.channels:
            root_messages = browser.list_root_messages(
                workspace_name=workspace.name,
                channel_name=channel.name,
            )
            if logger is not None:
                logger.info(
                    "poll roots workspace=%s channel=%s count=%d latest=%s",
                    workspace.name,
                    channel.name,
                    len(root_messages),
                    root_messages[-1].message_ts if root_messages else "",
                )
            for root_message in root_messages:
                orchestrator.handle_new_root_message(
                    workspace_name=root_message.workspace_name,
                    channel_name=root_message.channel_name,
                    message_ts=root_message.message_ts,
                    author_actor_id=root_message.author_actor_id,
                    text=root_message.text,
                )

            tracked_sessions = state_store.list_sessions(
                workspace_name=workspace.name,
                channel_name=channel.name,
            )
            if logger is not None:
                logger.info(
                    "poll sessions workspace=%s channel=%s count=%d",
                    workspace.name,
                    channel.name,
                    len(tracked_sessions),
                )
            for session in tracked_sessions:
                if session.status is SessionStatus.RUNNING:
                    continue
                delivered_timestamps = set(
                    state_store.list_delivered_outbound_message_timestamps(
                        workspace_name=workspace.name,
                        channel_name=channel.name,
                        thread_ts=session.thread_ts,
                    )
                )
                replies = browser.list_thread_replies(
                    workspace_name=workspace.name,
                    channel_name=channel.name,
                    thread_ts=session.thread_ts,
                )
                if logger is not None:
                    logger.info(
                        "poll replies workspace=%s channel=%s thread=%s count=%d",
                        workspace.name,
                        channel.name,
                        session.thread_ts,
                        len(replies),
                    )
                for reply in replies:
                    if not _is_fresh_user_reply(reply.message_ts, session.created_at):
                        continue
                    if reply.message_ts in delivered_timestamps:
                        continue
                    if _is_bob_generated_reply_text(reply.text):
                        continue
                    orchestrator.handle_thread_reply(
                        workspace_name=reply.workspace_name,
                        channel_name=reply.channel_name,
                        thread_ts=reply.thread_ts,
                        message_ts=reply.message_ts,
                        author_actor_id=reply.author_actor_id,
                        text=reply.text,
                    )


def _is_fresh_user_reply(message_ts: str, session_created_at: int) -> bool:
    try:
        message_epoch = float(message_ts)
    except (TypeError, ValueError):
        return False
    return message_epoch > float(session_created_at)


def _is_bob_generated_reply_text(text: str) -> bool:
    normalized = text.strip()
    return normalized.startswith("codex Bob:") or normalized.startswith("Bob ")


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
            "Polling interval between Slack cycles in seconds "
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
    browser: SlackBrowserAdapter,
    orchestrator: OrchestratorAdapter,
    state_store: BobStateStore,
    config: AppConfig,
    poll_interval_seconds: float,
    lock_file: Path,
    pid_file: Path,
    stop_request_path: Path,
    logger: Logger = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    try:
        while True:
            _refresh_runtime_markers(lock_file, pid_file)
            if stop_request_path.exists():
                return
            run_poll_cycle(
                browser=browser,
                orchestrator=orchestrator,
                state_store=state_store,
                config=config,
                logger=logger,
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
            codex_runner = SubprocessCodexRunner()
            orchestrator = BobOrchestrator(
                browser=browser,
                state_store=state_store,
                codex_runner=codex_runner,
                config=config,
            )
            try:
                _run_agent_cycles(
                    browser=browser,
                    orchestrator=orchestrator,
                    state_store=state_store,
                    config=config,
                    once=once,
                    poll_interval_seconds=poll_interval_seconds,
                    stop_request_path=paths.stop_request_file,
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
    browser: SlackBrowserAdapter,
    orchestrator: OrchestratorAdapter,
    state_store: BobStateStore,
    config: AppConfig,
    once: bool,
    poll_interval_seconds: float,
    stop_request_path: Path,
    logger: Logger,
) -> None:
    if once:
        run_poll_cycle(
            browser=browser,
            orchestrator=orchestrator,
            state_store=state_store,
            config=config,
            logger=logger,
        )
        return
    run_poll_loop(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state_store,
        config=config,
        poll_interval_seconds=poll_interval_seconds,
        lock_file=stop_request_path.parent / "bob.lock",
        pid_file=stop_request_path.parent / "bob.pid",
        stop_request_path=stop_request_path,
        logger=logger,
    )
    logger.info("bob-agent interrupted, shutting down.")


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
