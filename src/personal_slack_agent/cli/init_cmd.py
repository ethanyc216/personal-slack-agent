from __future__ import annotations

import argparse
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from ..chrome_launcher import default_launcher_profile_path
from ..config import dump_config, load_config
from ..models import (
    AppConfig,
    BrowserSettings,
    ChannelConfig,
    CODEX_HOME_MODE_DEFAULT,
    DefaultSettings,
    LifecycleSettings,
    OrchestratorSettings,
    PERSISTENT_MEMORY_MODE_DISABLED,
    PERSISTENT_MEMORY_MODE_OWNER_ONLY,
    RunnerSettings,
    SHARED_BROWSER_MODE,
    WatcherSettings,
    WorkspaceChannelDefaults,
    WorkspaceConfig,
)
from ..paths import default_config_file, default_state_dir
from ..slack.playwright_adapter import PlaywrightSlackAdapter

InputFn = Callable[[str], str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bob-init",
        description="Interactively initialize local Bob config and state directories.",
    )
    parser.add_argument(
        "--config",
        default=str(default_config_file()),
        help="Path to the Bob configuration file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing generated files.",
    )
    parser.add_argument(
        "--discover-slack-auth",
        action="store_true",
        help="Discover Slack Web API auth from the logged-in browser session and write it into config.",
    )
    parser.add_argument(
        "--workspace",
        help="Workspace name to update when using --discover-slack-auth.",
    )
    return parser


def _default_owner_preferred_name(owner_name: str) -> str:
    if owner_name.strip() == "Bob Owner":
        return "Owner"
    return owner_name.strip().split()[0] if owner_name.strip() else "Owner"


def _prompt_text(
    prompt: str,
    *,
    default: Optional[str] = None,
    required: bool = True,
    input_fn: Optional[InputFn] = None,
) -> str:
    reader = input if input_fn is None else input_fn
    suffix = " [{0}]".format(default) if default else ""
    while True:
        value = reader("{0}{1}: ".format(prompt, suffix)).strip()
        if not value and default is not None:
            return default
        if value or not required:
            return value
        print("{0} is required.".format(prompt), file=sys.stderr)


def _prompt_https_url(prompt: str, *, input_fn: Optional[InputFn] = None) -> str:
    while True:
        value = _prompt_text(prompt, input_fn=input_fn)
        parsed = urlparse(value)
        if parsed.scheme == "https" and parsed.netloc:
            return value
        print("{0} must be an https URL.".format(prompt), file=sys.stderr)


def _prompt_existing_directory(
    prompt: str,
    *,
    default: Path,
    input_fn: Optional[InputFn] = None,
) -> Path:
    while True:
        raw = _prompt_text(prompt, default=str(default), input_fn=input_fn)
        path = Path(raw).expanduser()
        if path.exists() and path.is_dir():
            return path.resolve()
        print("{0} must point to an existing directory.".format(prompt), file=sys.stderr)


def _parse_actor_ids(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _prompt_memory_mode(*, input_fn: Optional[InputFn] = None) -> str:
    while True:
        value = _prompt_text(
            "Persistent memory mode (disabled or owner_only)",
            default=PERSISTENT_MEMORY_MODE_DISABLED,
            input_fn=input_fn,
        ).lower()
        if value in ("disabled", "disable", "none", "no"):
            return PERSISTENT_MEMORY_MODE_DISABLED
        if value in ("owner_only", "owner-only", "owner", "yes"):
            return PERSISTENT_MEMORY_MODE_OWNER_ONLY
        print(
            "Persistent memory mode must be disabled or owner_only.",
            file=sys.stderr,
        )


def _prompt_yes_no(
    prompt: str,
    *,
    default: bool,
    input_fn: Optional[InputFn] = None,
) -> bool:
    default_text = "Y" if default else "n"
    while True:
        value = _prompt_text(
            prompt,
            default=default_text,
            required=False,
            input_fn=input_fn,
        ).lower()
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print("{0} must be yes or no.".format(prompt), file=sys.stderr)


def _run_setup_wizard(*, input_fn: Optional[InputFn] = None) -> AppConfig:
    print("Bob config setup")
    print("Press Enter to accept a default value shown in brackets.")

    owner_name = _prompt_text(
        "Owner full name",
        default="Bob Owner",
        input_fn=input_fn,
    )
    owner_preferred_name = _prompt_text(
        "Owner preferred name",
        default=_default_owner_preferred_name(owner_name),
        input_fn=input_fn,
    )
    workspace_name = _prompt_text("Workspace name", input_fn=input_fn)
    slack_url = _prompt_https_url("Slack workspace URL", input_fn=input_fn)
    allowed_actor_ids = _parse_actor_ids(
        _prompt_text(
            "Allowed Slack actor IDs, comma-separated (blank allows any actor)",
            required=False,
            input_fn=input_fn,
        )
    )
    channel_name = _prompt_text("Channel name", input_fn=input_fn)
    slack_channel_id = _prompt_text(
        "Slack channel ID (optional)",
        required=False,
        input_fn=input_fn,
    )
    default_cwd = _prompt_existing_directory(
        "Default working directory",
        default=Path("."),
        input_fn=input_fn,
    )
    memory_mode = _prompt_memory_mode(input_fn=input_fn)
    persistent_memory_owner = None
    if memory_mode == PERSISTENT_MEMORY_MODE_OWNER_ONLY:
        persistent_memory_owner = _prompt_text(
            "Persistent memory owner handle",
            default="bob_owner_handle",
            input_fn=input_fn,
        )
    post_terminal_threads_here = _prompt_yes_no(
        "Use this channel for terminal `bob` requests",
        default=True,
        input_fn=input_fn,
    )

    return AppConfig(
        defaults=_default_settings(owner_name, owner_preferred_name),
        browser=BrowserSettings(
            browser_mode=SHARED_BROWSER_MODE,
            browser_user_data_dir=str(default_launcher_profile_path()),
        ),
        runner=RunnerSettings(
            bob_codex_home=str(default_state_dir() / "codex-home"),
            codex_exec_timeout_seconds=600.0,
        ),
        lifecycle=LifecycleSettings(reminder_minutes=[30], auto_close_minutes=120),
        orchestrator=OrchestratorSettings(
            max_concurrent_tasks=1,
            max_concurrent_per_thread=1,
        ),
        watcher=WatcherSettings(),
        workspaces=[
            WorkspaceConfig(
                name=workspace_name,
                slack_url=slack_url,
                channel_defaults=WorkspaceChannelDefaults(
                    allowed_actor_ids=allowed_actor_ids,
                    default_cwd=str(default_cwd),
                    additional_roots=[],
                    accept_root_bob_requests=True,
                    codex_home_mode=CODEX_HOME_MODE_DEFAULT,
                ),
                channels=[
                    ChannelConfig(
                        name=channel_name,
                        post_terminal_threads_here=post_terminal_threads_here,
                        persistent_memory_mode=memory_mode,
                        persistent_memory_owner=persistent_memory_owner,
                        slack_channel_id=slack_channel_id or None,
                    )
                ],
            )
        ],
    )


def _default_settings(owner_name: str, owner_preferred_name: str) -> DefaultSettings:
    return DefaultSettings(
        owner_name=owner_name,
        owner_preferred_name=owner_preferred_name,
    )


def _write_config(config_file: Path, config: AppConfig) -> None:
    rendered = dump_config(config)
    validation_file = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(config_file.parent),
            prefix=".{0}.".format(config_file.name),
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(rendered)
            validation_file = Path(temp_file.name)
        load_config(validation_file)
        validation_file.replace(config_file)
    finally:
        if validation_file is not None and validation_file.exists():
            validation_file.unlink()


def _print_next_steps(config_file: Path, config: AppConfig) -> None:
    workspace_name = config.workspaces[0].name if config.workspaces else "<workspace>"
    config_arg = shlex.quote(str(config_file))
    workspace_arg = shlex.quote(workspace_name)
    print("Wrote Bob config to {0}".format(config_file))
    print("Review or edit it later with:")
    print("  bobctl show-config --config {0}".format(config_arg))
    print("After Chrome is open and logged into Slack, capture Slack auth with:")
    print(
        "  bob-init --discover-slack-auth --workspace {0} --config {1}".format(
            workspace_arg,
            config_arg,
        )
    )
    print("Then validate with:")
    print("  bobctl doctor --config {0}".format(config_arg))
    print("For field-by-field config details, see docs/bob-config-setup.md.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_file = Path(args.config).expanduser()
    config_file.parent.mkdir(parents=True, exist_ok=True)

    if args.discover_slack_auth:
        if not args.workspace:
            print("--workspace is required with --discover-slack-auth.", file=sys.stderr)
            return 2
        if not config_file.exists():
            print(
                "Config file does not exist at {0}. Run bob-init first.".format(config_file),
                file=sys.stderr,
            )
            return 1
        config = load_config(config_file)
        workspace = next((item for item in config.workspaces if item.name == args.workspace), None)
        if workspace is None:
            print("Workspace not found in config: {0}".format(args.workspace), file=sys.stderr)
            return 1
        adapter = PlaywrightSlackAdapter(
            browser_mode=config.browser.browser_mode,
            cdp_url=config.browser.cdp_url,
            slack_signin_url=config.browser.slack_signin_url,
            chrome_executable_path=config.browser.chrome_executable_path,
            browser_user_data_dir=config.browser.browser_user_data_dir,
            reauth_state_path=default_state_dir() / "slack-reauth.json",
            slack_reauth_cooldown_seconds=config.browser.slack_reauth_cooldown_seconds,
        )
        adapter.set_workspace_urls(
            {
                item.name: item.slack_url
                for item in config.workspaces
                if item.slack_url
            }
        )
        try:
            token, origin = adapter.discover_api_session(args.workspace)
        finally:
            adapter.close()
        workspace.slack_api_origin = origin
        workspace.slack_api_token = token
        config_file.write_text(dump_config(config), encoding="utf-8")
        print("Updated Slack API auth for workspace {0} in {1}".format(args.workspace, config_file))
        return 0

    if config_file.exists() and not args.force:
        print(
            "Config file already exists at {0}. Re-run with --force to overwrite.".format(config_file),
            file=sys.stderr,
        )
        return 1

    try:
        config = _run_setup_wizard()
    except (EOFError, KeyboardInterrupt):
        print("Bob config setup canceled.", file=sys.stderr)
        return 1

    try:
        _write_config(config_file, config)
    except Exception as exc:
        print("Generated config failed validation: {0}".format(exc), file=sys.stderr)
        return 1
    _print_next_steps(config_file, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
