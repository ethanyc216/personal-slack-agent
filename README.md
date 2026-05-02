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
- Accepts messages that invoke `Bob` or `bob`.
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
- `bob close` thread closure with later resume support
- process control through `bobctl start|stop|restart|status|tail-log|show-config|doctor`
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

For a full first-time setup, use [docs/setup.md](docs/setup.md).

### Local Development

Use an editable install when working from a repo checkout:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[dev]'
```

### TestPyPI Install

Use TestPyPI only for release testing. Install in a throwaway virtual
environment so the package does not conflict with an editable local Bob install:

```bash
python3 -m venv /tmp/bob-testpypi
/tmp/bob-testpypi/bin/python -m pip install --upgrade pip
/tmp/bob-testpypi/bin/python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  personal-slack-agent
```

`--extra-index-url` lets pip resolve normal dependencies such as Playwright from
PyPI when they are not available on TestPyPI.

### PyPI Install

After the project has a real public PyPI release, install from PyPI with:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
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

Run a live smoke test:

```bash
.venv/bin/bobctl smoke-test --workspace my-workspace --channel my-private-channel
```

Use the terminal wrapper to create a Slack-thread-backed request:

```bash
.venv/bin/bob --workspace my-workspace --channel my-private-channel "summarize this repo"
```

## Docs

- [Setup guide](docs/setup.md): install, Chrome setup, first config, smoke test.
- [How it works](docs/how-it-works.md): motivation, architecture, and message flow.
- [Config guide](docs/bob-config-setup.md): field-by-field `bob.toml` reference.
- [Command reference](docs/command-reference.md): `bob`, `bob-agent`, `bob-init`, and `bobctl`.
- [Publishing guide](docs/publishing.md): GitHub Releases, TestPyPI, PyPI options, and package exposure.
- [Slack client findings](docs/slack-client-findings.md): implementation notes from Slack Web inspection.
- [Sample config](config/bob.sample.toml): committed anonymized config template.

## Release And Publishing

GitHub Releases are generated automatically from successful pushes to `main`.
Each generated release uploads a wheel and source distribution as downloadable
release assets.

TestPyPI publishing is configured but manual. PyPI publishing is wired to run
after each generated GitHub Release once the PyPI Trusted Publisher and GitHub
`pypi` environment are configured. The PyPI job publishes the exact wheel and
source distribution built by the release job, so the PyPI package version
matches the GitHub Release tag. A manual PyPI workflow can also publish an
existing GitHub Release tag as a fallback.

See [docs/publishing.md](docs/publishing.md) for the setup values and release
workflow details.

The CI badge above updates when GitHub renders the README. The latest-release
link resolves through GitHub to the current latest release. Literal version
numbers in README prose only change when a commit changes them.

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

## Testing

Run the full test suite:

```bash
.venv/bin/python -m pytest -q
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
