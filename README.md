# personal-slack-agent

[![CI](https://github.com/ethanyc216/personal-slack-agent/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ethanyc216/personal-slack-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Setup](docs/setup.md) |
[How It Works](docs/how-it-works.md) |
[Commands](docs/command-reference.md) |
[Configuration](docs/bob-config-setup.md) |
[Publishing](docs/publishing.md) |
[Latest GitHub Release](https://github.com/ethanyc216/personal-slack-agent/releases/latest)

`personal-slack-agent` is a local Slack-to-Codex bridge. It runs a background
agent named `Bob` on your machine, watches approved Slack conversations, starts
or resumes local Codex sessions, and posts status plus results back into Slack
threads.

![Slack thread showing Bob working in a Slack conversation](docs/assets/bob-slack-interaction.png)

## Why Bob Exists

Some company environments restrict direct integrations between AI coding tools
and internal messaging systems. A team may not be able to install a Slack app,
grant OAuth scopes to a hosted connector, expose internal messages to a third
party service, or run a cloud bot that talks to Codex on the user's behalf.

Bob is built for that constraint. Instead of acting as a hosted Slack app, Bob
runs locally beside Codex and uses a browser-authenticated Slack Web session that
the user already controls. Work still happens on the user's machine, while Slack
becomes the coordination surface: prompts, progress, waiting states, approvals,
and final answers remain visible in the approved company messaging system.

The goal is not to replace Codex. The goal is to make Codex work trackable from
Slack when Slack is where the team already coordinates.

## What Bob Does

- Watches configured Slack channels or explicitly allowed runtime conversations.
- Accepts messages that invoke a configured Slack callsign such as `Bob` or `bob`.
- Starts a new local Codex session for a new Slack thread.
- Resumes the same local Codex session when someone replies in that thread.
- Posts working, waiting, approval, error, and final messages back to Slack.
- Lets terminal-originated requests use the same Slack-thread-backed workflow.
- Keeps per-channel memory policy explicit so shared channels do not update
  personal durable notes by accident.

## How It Works

At a high level:

1. Bob attaches to a local Chrome session that is already logged into Slack.
2. Bob watches Slack Web realtime events and targeted Slack Web API responses.
3. When an allowed user invokes Bob, Bob creates or resumes local Codex work.
4. Bob posts lifecycle updates and final output back into the Slack thread.

The Slack thread is the human-readable work log. The local state database maps
Slack threads to Codex session ids so later Slack replies can continue the same
conversation.

For a deeper architecture walkthrough, see [docs/how-it-works.md](docs/how-it-works.md).

## Current Status

The project is functional but still experimental.

Working pieces include:

- package install and CLI entry points
- config generation and validation
- background watcher loop
- websocket-first Slack event detection
- targeted Slack API hydration for channel roots and thread replies
- thread/session mapping to local Codex sessions
- thread reply resume for existing sessions
- waiting-state reminders and auto-close handling
- manual `<callsign> close` thread closure with later resume support
- cleanup of obsolete waiting prompts after resolution
- local process control with `bobctl start|stop|restart|status|tail-log|show-config|doctor`
- GitHub Actions CI, generated GitHub Releases, manual TestPyPI publishing, and
  PyPI publishing from generated release artifacts

Current constraints:

- macOS only
- Chrome or Chromium required
- Slack integration uses Slack Web realtime sockets plus browser-session-backed
  Slack Web requests, not an official Slack app install
- broad Slack message edit/delete flows are still limited beyond targeted
  waiting-prompt cleanup
- browser/web request behavior may need adjustment if Slack changes private web
  client APIs or websocket behavior

## Quick Start

Install Bob from PyPI:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install personal-slack-agent
```

Generate local config:

```bash
.venv/bin/bob-init
```

Start Bob:

```bash
.venv/bin/bobctl start --config ~/.config/personal-slack-agent/bob.toml --poll-interval-seconds 10
```

Trigger Bob from Slack with a configured callsign:

```text
Bob, summarize this repo
```

Changing `defaults.assistant_names` only changes Slack-facing callsigns and
reply labels. The local command names remain fixed: `bob`, `bobctl`,
`bob-agent`, and `bob-init`.

For Chrome setup, Slack auth discovery, config review, and smoke testing, use
[docs/setup.md](docs/setup.md).

## Docs

- [Setup guide](docs/setup.md): install, Chrome setup, first config, smoke test.
- [How it works](docs/how-it-works.md): motivation, architecture, and message flow.
- [Config guide](docs/bob-config-setup.md): field-by-field `bob.toml` reference.
- [Command reference](docs/command-reference.md): `bob`, `bob-agent`, `bob-init`, and `bobctl`.
- [Development guide](docs/development.md): editable install and test commands for repo work.
- [Publishing guide](docs/publishing.md): GitHub Releases, TestPyPI, PyPI options, and package exposure.
- [Slack client findings](docs/slack-client-findings.md): implementation notes from Slack Web inspection.
- [Sample config](config/bob.sample.toml): committed anonymized config template.

## Security Notes

- `slack_api_token` is sensitive and should not be committed.
- Do not publish personal `bob.toml` files.
- Do not share the Chrome profile used for Slack browser auth.
- Treat GitHub Releases, TestPyPI, and PyPI as public distribution channels.
- Published Python wheels contain readable `.py` source files.

## Project Layout

- `src/personal_slack_agent/`: package source
- `tests/`: automated tests
- `config/bob.sample.toml`: anonymized sample config
- `docs/`: setup, operation, architecture, and publishing docs
- `.github/workflows/`: CI, release, TestPyPI, and PyPI workflows
- `pyproject.toml`: package metadata

## Development Setup And Testing

Use an editable install when working from a repo checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[dev]'
```

Run the full test suite:

```bash
.venv/bin/python -m pytest -q
```

For more repo-development notes, see [docs/development.md](docs/development.md).

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
