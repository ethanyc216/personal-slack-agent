from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..paths import default_config_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bob-init",
        description="Initialize local Bob config and state directories.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing generated files.",
    )
    return parser


def _starter_config(default_cwd: Path) -> str:
    return "\n".join(
        [
            "[defaults]",
            'default_cwd = "{0}"'.format(default_cwd),
            'allowed_actor_ids = ["U01234567"]',
            "accept_root_bob_requests = true",
            'slack_signin_url = "https://slack.com/signin?entry_point=nav_menu#/signin"',
            'browser_mode = "dedicated_browser"',
            'browser_url = "http://127.0.0.1:9222"',
            'cdp_url = "http://127.0.0.1:9222"',
            '# chrome_executable_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"',
            '# browser_user_data_dir = "~/.config/personal-slack-agent/chrome-profile"',
            "reminder_minutes = [30]",
            "auto_close_minutes = 120",
            "",
            "# Optional workspace/channel overrides:",
            "# [[workspaces]]",
            '# name = "my-workspace"',
            '# slack_url = "https://app.slack.com/client/T12345678/C12345678"',
            '# slack_api_origin = "https://example.enterprise.slack.com"',
            '# slack_api_token = "xoxc-..."',
            "#",
            "# [[workspaces.channels]]",
            '# name = "your-private-channel"',
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_file = default_config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)

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
