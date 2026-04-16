from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import dump_config, load_config
from ..paths import default_config_file
from ..slack.playwright_adapter import PlaywrightSlackAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bob-init",
        description="Initialize local Bob config and state directories.",
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


def _starter_config(default_cwd: Path) -> str:
    return "\n".join(
        [
            "[defaults]",
            "",
            "[browser]",
            'slack_signin_url = "https://slack.com/signin?entry_point=nav_menu#/signin"',
            'browser_mode = "dedicated_browser"',
            'browser_url = "http://127.0.0.1:9222"',
            'cdp_url = "http://127.0.0.1:9222"',
            '# chrome_executable_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"',
            '# browser_user_data_dir = "~/.config/personal-slack-agent/chrome-profile"',
            "",
            "[runner]",
            '# bob_codex_home = "~/.local/share/personal-slack-agent/codex-home"',
            "codex_exec_timeout_seconds = 600",
            "",
            "[lifecycle]",
            "reminder_minutes = [30]",
            "auto_close_minutes = 120",
            "",
            "[orchestrator]",
            "max_concurrent_tasks = 1",
            "max_concurrent_per_thread = 1",
            "",
            "[watcher]",
            "root_batch_size = 50",
            "thread_batch_size = 200",
            "thread_reply_rate_limit_backoff_seconds = 60",
            "recent_terminal_thread_reconcile_limit = 6",
            "periodic_terminal_thread_reconcile_batch_size = 1",
            "historical_terminal_thread_reconcile_base_interval_seconds = 60",
            "historical_terminal_thread_reconcile_max_interval_seconds = 900",
            "",
            "# Optional workspace/channel overrides:",
            "# [[workspaces]]",
            '# name = "my-workspace"',
            '# slack_url = "https://app.slack.com/client/T12345678/C12345678"',
            '# slack_api_origin = "https://example.enterprise.slack.com"',
            '# slack_api_token = "xoxc-..."',
            "#",
            "# [workspaces.channel_defaults]",
            '# allowed_actor_ids = ["U01234567"]',
            '# default_cwd = "{0}"'.format(default_cwd),
            '# accept_root_bob_requests = true',
            '# codex_home_mode = "default"',
            "#",
            "# [[workspaces.channels]]",
            '# name = "your-private-channel"',
            "",
        ]
    )


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

    config_file.write_text(_starter_config(Path.home().resolve()), encoding="utf-8")
    print("Wrote starter config to {0}".format(config_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
