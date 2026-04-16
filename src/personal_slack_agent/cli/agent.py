from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
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
            channel_id = channel.effective_slack_channel_id or channel.slack_channel_id
            if not channel_id:
                continue
            channel_urls[(workspace.name, channel.name)] = "https://app.slack.com/client/{0}/{1}".format(
                team_id,
                channel_id,
            )
    browser.set_channel_urls(channel_urls)


def _prepare_bob_codex_home(target_home: Path) -> Path:
    source_home = Path.home() / ".codex"
    bob_home = target_home
    bob_home.mkdir(parents=True, exist_ok=True)
    if not source_home.exists():
        _normalize_bob_codex_home_runtime_paths(bob_home)
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
            if target_path.is_symlink() or target_path.is_file():
                target_path.unlink()
            elif target_path.is_dir():
                shutil.rmtree(target_path)
        target_path.symlink_to(source_path, target_is_directory=source_path.is_dir())

    target_hooks = bob_home / "hooks.json"
    if target_hooks.exists() or target_hooks.is_symlink():
        target_hooks.unlink()

    _normalize_bob_codex_home_runtime_paths(bob_home)
    return bob_home


_CODEX_HOME_ENV_RE = re.compile(r"(?m)^export CODEX_HOME=(/.+)$")


def _normalize_bob_codex_home_runtime_paths(target_home: Path) -> None:
    current_home = str(target_home)
    current_variants = _equivalent_codex_home_paths(current_home)
    stale_homes = sorted(
        {
            old_home
            for old_home in _discover_runtime_codex_homes(target_home)
            if old_home not in current_variants
        },
        key=len,
        reverse=True,
    )
    replacements = {old_home: current_home for old_home in stale_homes}
    if not replacements:
        return

    _rewrite_runtime_state_db_paths(target_home / "state_5.sqlite", replacements)

    _rewrite_history_jsonl_paths(target_home / "history.jsonl", replacements)
    _rewrite_shell_snapshot_paths(target_home / "shell_snapshots", replacements)


def _discover_runtime_codex_homes(target_home: Path) -> set[str]:
    homes = set()
    homes.update(
        _discover_codex_homes_from_state_db(
            state_db=target_home / "state_5.sqlite",
            target_home=target_home,
        )
    )
    homes.update(_discover_codex_homes_from_text_tree(target_home / "shell_snapshots", "*.sh"))
    homes.update(_discover_codex_homes_from_text_file(target_home / "history.jsonl"))
    return homes


def _discover_codex_homes_from_state_db(state_db: Path, target_home: Path) -> set[str]:
    if not state_db.exists():
        return set()

    try:
        connection = sqlite3.connect(str(state_db))
    except sqlite3.DatabaseError:
        return set()

    homes: set[str] = set()
    try:
        tables = _list_sqlite_tables(connection)
        if "threads" in tables:
            thread_columns = _list_sqlite_table_columns(connection, "threads")
            selected_columns = [
                column for column in ("rollout_path", "sandbox_policy") if column in thread_columns
            ]
            if selected_columns:
                cursor = connection.execute(
                    "SELECT {0} FROM threads".format(", ".join(selected_columns))
                )
                for row in cursor.fetchall():
                    for column, value in zip(selected_columns, row):
                        if column == "sandbox_policy":
                            homes.update(
                                _extract_codex_home_paths_from_sandbox_policy(
                                    value, target_home
                                )
                            )
                            continue
                        homes.update(_extract_codex_home_paths(value))
        if "agent_jobs" in tables:
            agent_job_columns = _list_sqlite_table_columns(connection, "agent_jobs")
            selected_columns = [
                column for column in ("input_csv_path", "output_csv_path") if column in agent_job_columns
            ]
            if selected_columns:
                cursor = connection.execute(
                    "SELECT {0} FROM agent_jobs".format(", ".join(selected_columns))
                )
                for row in cursor.fetchall():
                    for value in row:
                        homes.update(_extract_codex_home_paths_from_temp_path(value))
    except sqlite3.DatabaseError:
        return set()
    finally:
        connection.close()
    return homes


def _discover_codex_homes_from_text_tree(root: Path, pattern: str) -> set[str]:
    if not root.exists():
        return set()

    homes: set[str] = set()
    for file_path in root.rglob(pattern):
        homes.update(_discover_codex_homes_from_text_file(file_path))
    return homes


def _discover_codex_homes_from_text_file(file_path: Path) -> set[str]:
    if not file_path.exists():
        return set()
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    return _extract_codex_home_paths(text)


def _extract_codex_home_paths(text: str | None) -> set[str]:
    if not text:
        return set()
    homes = {match.group(1).strip() for match in _CODEX_HOME_ENV_RE.finditer(text)}
    homes.update(_extract_codex_home_path_prefixes(text, "/sessions/"))
    homes.update(_extract_codex_home_path_prefixes(text, "/shell_snapshots/"))
    return homes


def _extract_codex_home_paths_from_sandbox_policy(
    sandbox_policy: str | None,
    target_home: Path,
) -> set[str]:
    if not sandbox_policy:
        return set()
    try:
        policy = json.loads(sandbox_policy)
    except json.JSONDecodeError:
        return set()
    writable_roots = policy.get("writable_roots")
    if not isinstance(writable_roots, list):
        return set()

    homes = set()
    for root in writable_roots:
        if not isinstance(root, str) or not root.endswith("/memories"):
            continue
        candidate_path = Path(root[: -len("/memories")])
        if not _is_same_bob_home_family(candidate_path, target_home):
            continue
        homes.add(str(candidate_path))
    return homes


def _is_same_bob_home_family(candidate: Path, target_home: Path) -> bool:
    if candidate.name == target_home.name and (
        _is_tmp_like_path(candidate) or _is_tmp_like_path(target_home)
    ):
        return True
    if candidate.parent == target_home.parent and target_home.name.startswith(candidate.name):
        suffix = target_home.name[len(candidate.name):]
        if suffix and suffix[0] in "-_0123456789":
            return True
    return False


def _is_tmp_like_path(path: Path) -> bool:
    raw = str(path)
    if raw.startswith("/tmp/"):
        return True
    if raw.startswith("/private/tmp/"):
        return True
    return False


def _extract_codex_home_path_prefixes(text: str, suffix: str) -> set[str]:
    pattern = re.compile(r"(/[^\"'\s]+?)(?={0})".format(re.escape(suffix)))
    return {match.group(1) for match in pattern.finditer(text)}


def _extract_codex_home_paths_from_temp_path(path: str | None) -> set[str]:
    if not path:
        return set()
    homes = set()
    for marker in ("/.tmp/", "/tmp/"):
        if marker not in path:
            continue
        prefix = path.rsplit(marker, 1)[0]
        if prefix.startswith("/"):
            homes.add(prefix)
    return homes


def _list_sqlite_tables(connection: sqlite3.Connection) -> set[str]:
    cursor = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )
    return {row[0] for row in cursor.fetchall()}


def _list_sqlite_table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    cursor = connection.execute("PRAGMA table_info({0})".format(table_name))
    return {row[1] for row in cursor.fetchall()}


def _rewrite_runtime_state_db_paths(state_db: Path, replacements: dict[str, str]) -> None:
    if not state_db.exists() or not replacements:
        return

    try:
        connection = sqlite3.connect(str(state_db))
    except sqlite3.DatabaseError:
        return

    try:
        tables = _list_sqlite_tables(connection)
        if "threads" in tables:
            thread_columns = tuple(
                column
                for column in ("rollout_path", "sandbox_policy")
                if column in _list_sqlite_table_columns(connection, "threads")
            )
            _rewrite_runtime_state_table_paths(
                connection=connection,
                table_name="threads",
                columns=thread_columns,
                replacements=replacements,
            )
        if "agent_jobs" in tables:
            agent_job_columns = tuple(
                column
                for column in ("input_csv_path", "output_csv_path")
                if column in _list_sqlite_table_columns(connection, "agent_jobs")
            )
            _rewrite_runtime_state_table_paths(
                connection=connection,
                table_name="agent_jobs",
                columns=agent_job_columns,
                replacements=replacements,
            )
    except sqlite3.DatabaseError:
        return
    finally:
        connection.close()


def _rewrite_runtime_state_table_paths(
    connection: sqlite3.Connection,
    table_name: str,
    columns: tuple[str, ...],
    replacements: dict[str, str],
) -> None:
    if not columns:
        return
    selected_columns = ["rowid", *columns]
    cursor = connection.execute(
        "SELECT {0} FROM {1}".format(", ".join(selected_columns), table_name)
    )
    for row in cursor.fetchall():
        rowid = row[0]
        updated_values = {}
        for column, value in zip(columns, row[1:]):
            updated_value = _rewrite_runtime_state_value(
                column=column,
                value=value,
                replacements=replacements,
            )
            if updated_value == value:
                continue
            updated_values[column] = updated_value
        if not updated_values:
            continue
        assignments = ", ".join("{0} = ?".format(column) for column in updated_values)
        parameters = [*updated_values.values(), rowid]
        try:
            with connection:
                connection.execute(
                    "UPDATE {0} SET {1} WHERE rowid = ?".format(table_name, assignments),
                    parameters,
                )
        except sqlite3.DatabaseError:
            continue


def _rewrite_runtime_state_value(
    column: str,
    value: str | None,
    replacements: dict[str, str],
) -> str | None:
    if value is None:
        return None
    if column == "rollout_path":
        return _rewrite_path_with_prefix(value, replacements, ("/sessions/",))
    if column in {"input_csv_path", "output_csv_path"}:
        return _rewrite_path_with_prefix(value, replacements, ("/tmp/", "/.tmp/"))
    if column == "sandbox_policy":
        return _rewrite_sandbox_policy(value, replacements)
    return value


def _rewrite_path_with_prefix(
    value: str,
    replacements: dict[str, str],
    suffixes: tuple[str, ...],
) -> str:
    for old_home, new_home in replacements.items():
        for suffix in suffixes:
            prefix = old_home + suffix
            if not value.startswith(prefix):
                continue
            return new_home + value[len(old_home):]
    return value


def _rewrite_sandbox_policy(sandbox_policy: str, replacements: dict[str, str]) -> str:
    try:
        policy = json.loads(sandbox_policy)
    except json.JSONDecodeError:
        return sandbox_policy
    writable_roots = policy.get("writable_roots")
    if not isinstance(writable_roots, list):
        return sandbox_policy

    updated_roots = []
    changed = False
    for root in writable_roots:
        if not isinstance(root, str):
            updated_roots.append(root)
            continue
        updated_root = root
        for old_home, new_home in replacements.items():
            if root != old_home + "/memories":
                continue
            updated_root = new_home + "/memories"
            changed = True
            break
        updated_roots.append(updated_root)
    if not changed:
        return sandbox_policy
    policy["writable_roots"] = updated_roots
    return json.dumps(policy, separators=(",", ":"))


def _rewrite_history_jsonl_paths(file_path: Path, replacements: dict[str, str]) -> None:
    if not file_path.exists() or not replacements:
        return
    try:
        original = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    updated_lines = []
    changed = False
    for line in original.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if not stripped:
            updated_lines.append(line)
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            updated_lines.append(line)
            continue
        if isinstance(payload, dict):
            rollout_path = payload.get("rollout_path")
            if isinstance(rollout_path, str):
                new_rollout_path = _rewrite_path_with_prefix(
                    rollout_path,
                    replacements,
                    ("/sessions/",),
                )
                if new_rollout_path != rollout_path:
                    payload["rollout_path"] = new_rollout_path
                    changed = True
                    updated_lines.append(json.dumps(payload, separators=(",", ":")) + "\n")
                    continue
        updated_lines.append(line)

    if not changed:
        return

    file_path.write_text("".join(updated_lines), encoding="utf-8")


def _rewrite_shell_snapshot_paths(root: Path, replacements: dict[str, str]) -> None:
    if not root.exists() or not replacements:
        return
    for file_path in root.rglob("*.sh"):
        try:
            original = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        updated_lines = []
        changed = False
        for line in original.splitlines(keepends=True):
            if line.startswith("export CODEX_HOME="):
                current_home = line[len("export CODEX_HOME="):].rstrip("\n")
                updated_home = current_home
                for old_home, new_home in replacements.items():
                    if current_home != old_home:
                        continue
                    updated_home = new_home
                    break
                updated_line = "export CODEX_HOME={0}\n".format(updated_home)
                if updated_line != line:
                    changed = True
                updated_lines.append(updated_line)
                continue
            updated_lines.append(line)
        if not changed:
            continue
        file_path.write_text("".join(updated_lines), encoding="utf-8")


def _equivalent_codex_home_paths(path: str) -> set[str]:
    variants = {path}
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        resolved = path
    variants.add(resolved)
    for variant in list(variants):
        if variant.startswith("/private/tmp/"):
            variants.add(variant[len("/private"):])
        if variant.startswith("/tmp/"):
            variants.add("/private" + variant)
    return variants


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
                browser_mode=config.browser.browser_mode,
                cdp_url=config.browser.cdp_url,
                slack_signin_url=config.browser.slack_signin_url,
                chrome_executable_path=config.browser.chrome_executable_path,
                browser_user_data_dir=config.browser.browser_user_data_dir,
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
                Path(config.runner.bob_codex_home)
                if config.runner.bob_codex_home is not None
                else paths.state_dir / "codex-home"
            )
            codex_runner = SubprocessCodexRunner(
                exec_timeout_seconds=config.runner.codex_exec_timeout_seconds
            )
            isolated_codex_runner = SubprocessCodexRunner(
                env_overrides={"CODEX_HOME": str(bob_codex_home)},
                exec_timeout_seconds=config.runner.codex_exec_timeout_seconds,
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
                close_orchestrator = getattr(orchestrator, "close", None)
                if callable(close_orchestrator):
                    close_orchestrator()
                shutdown_browser = getattr(browser, "shutdown", None)
                if callable(shutdown_browser):
                    shutdown_browser()
                else:
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
