from __future__ import annotations

import argparse
import sys

from ..callsign import match_assistant_invocation
from ..config import load_config
from .ctl import _resolve_smoke_target, _run_smoke_test, build_runtime_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bob",
        description="Launch a terminal-originated Bob request through Slack.",
    )
    parser.add_argument("--workspace", help="Workspace name from bob.toml.")
    parser.add_argument("--channel", help="Channel name from bob.toml.")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=1800.0,
        help="How long to wait for Bob to complete the request (default: 1800).",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=1.0,
        help="How frequently to poll Bob state while waiting (default: 1).",
    )
    parser.add_argument("prompt", nargs="+", help="Prompt to send to Bob.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = build_runtime_paths()
    config = load_config(paths.config_file)
    workspace_name, channel_name = _resolve_terminal_target(
        config=config,
        workspace_name=args.workspace,
        channel_name=args.channel,
    )
    text = _ensure_assistant_prefix(" ".join(args.prompt), config.defaults.assistant_names)
    result = _run_smoke_test(
        paths=paths,
        workspace_name=workspace_name,
        channel_name=channel_name,
        text=text,
        timeout_seconds=float(args.timeout_seconds),
        poll_interval_seconds=float(args.poll_interval_seconds),
    )
    print("Bob request completed.")
    print("thread_ts: {0}".format(result["thread_ts"]))
    print("session_id: {0}".format(result["session_id"]))
    print("final_message: {0}".format(result["final_message"]))
    return 0


def _resolve_terminal_target(config, workspace_name: str | None, channel_name: str | None) -> tuple[str, str]:
    if workspace_name is not None or channel_name is not None:
        workspace, channel = _resolve_smoke_target(config, workspace_name, channel_name)
        return workspace.name, channel.name

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


def _ensure_assistant_prefix(prompt: str, assistant_names: list[str]) -> str:
    stripped = prompt.strip()
    if match_assistant_invocation(stripped, assistant_names) is not None:
        return stripped
    return "{0}, {1}".format(assistant_names[0], stripped)


if __name__ == "__main__":
    raise SystemExit(main())
