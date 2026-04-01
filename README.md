# personal-slack-agent

`personal-slack-agent` is a local Slack-to-Codex bridge. It runs a background agent named `Bob` on your machine, watches configured Slack channels, starts or resumes local Codex sessions, and posts results back into Slack threads.

This project is built for a browser-authenticated workflow:

- Slack auth comes from your logged-in Chrome session
- Bob talks to Slack through the browser session, not a Slack app install
- Codex work runs locally on your machine

## Status

The project is functional but still experimental.

Working pieces:

- package install and CLI entrypoints
- config generation and validation
- background watcher loop
- Slack root-message pickup from configured channels
- thread/session mapping to local Codex sessions
- thread reply resume for existing sessions
- local process control with `bobctl start|stop|status|tail-log|show-config|doctor`

Current constraints:

- macOS only
- Chrome/Chromium required
- Slack integration uses private browser-session-backed `/api/...` calls, not Slack’s official public app API
- Slack message deletion/edit cleanup is not implemented yet

## Install

### Requirements

- Python 3.9+
- Google Chrome
- A working local Codex CLI installation
- A Slack web session you can log into in Chrome

### Local setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[dev]'
```

### Verify install

```bash
.venv/bin/bobctl --help
.venv/bin/bob-agent --help
.venv/bin/bob-init --help
```

## Configuration

Generate a starter config:

```bash
.venv/bin/bob-init
```

This writes:

```text
~/.config/personal-slack-agent/bob.toml
```

### Example config

```toml
[defaults]
default_cwd = "/Users/you/Code"
additional_roots = ["/Users/you/Code"]
allowed_actor_ids = ["U12345678"]
accept_root_bob_requests = true
slack_signin_url = "https://slack.com/signin?entry_point=nav_menu#/signin"
browser_mode = "shared_browser"
browser_url = "http://127.0.0.1:9222"
cdp_url = "http://127.0.0.1:9222"
browser_user_data_dir = "/Users/you/.cache/personal-slack-agent/chrome-profile"
reminder_minutes = [30]
auto_close_minutes = 120

[[workspaces]]
name = "my-workspace"
allowed_actor_ids = ["U12345678"]
slack_url = "https://app.slack.com/client/T12345678/C12345678"
slack_api_origin = "https://example.enterprise.slack.com"
slack_api_token = "xoxc-..."

[[workspaces.channels]]
name = "my-private-channel"
default_cwd = "/Users/you/Code"
accept_root_bob_requests = true
post_terminal_threads_here = true
```

### Important config notes

- `allowed_actor_ids`
  Only these Slack user IDs may trigger or resume Bob work.

- `slack_url`
  This should point to the Slack client URL for the workspace/channel you want Bob to use.

- `slack_api_origin`
  This is the same-origin Slack web host Bob will use for browser-session-backed `/api/...` calls.

- `slack_api_token`
  This is currently the browser-session token used for the private Slack web API path.
  Treat it as sensitive.

- `browser_mode`
  Supported values:
  - `shared_browser`
  - `dedicated_browser`

## Chrome setup

Bob expects a debuggable Chrome session.

Start Chrome with remote debugging enabled:

```bash
open -na "Google Chrome" --args \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.cache/personal-slack-agent/chrome-profile" \
  --no-first-run \
  --no-default-browser-check \
  "https://slack.com/signin?entry_point=nav_menu#/signin"
```

In that Chrome instance:

1. open `chrome://inspect/#remote-debugging`
2. enable remote debugging
3. log into Slack
4. open the workspace/channel page you configured

## Usage

### Start Bob in background

```bash
.venv/bin/bobctl start --config ~/.config/personal-slack-agent/bob.toml --poll-interval-seconds 10
```

Check status:

```bash
.venv/bin/bobctl status
```

Tail logs:

```bash
.venv/bin/bobctl tail-log --lines 50
```

Stop Bob:

```bash
.venv/bin/bobctl stop
```

### One-shot poll

For debugging:

```bash
.venv/bin/bob-agent --once --config ~/.config/personal-slack-agent/bob.toml
```

### Triggering work from Slack

Send a message in a watched channel that starts with `Bob` or `bob`, for example:

```text
Bob, summarize this repo
```

Bob will:

1. create or use the Slack thread for that request
2. start a local Codex session
3. post:
   - `Bob is working on it: <session-id>`
4. post final output as:
   - `codex Bob: ...`

If you reply in the thread later, Bob resumes the same local Codex session.

## Testing

Run the full test suite:

```bash
.venv/bin/python -m pytest -q
```

## Security notes

- This project currently relies on browser-session-backed Slack web requests.
- The `slack_api_token` is sensitive and should not be committed.
- Do not publish your personal config file.
- Do not share your Chrome profile or Slack browser session.

## Project layout

- `src/personal_slack_agent/`
  package source
- `tests/`
  automated tests
- `pyproject.toml`
  packaging metadata

## Current limitations

- macOS only
- no official Slack app/OAuth integration
- no Slack message cleanup/deletion flow yet
- browser/web request behavior may need adjustment if Slack changes its private web client APIs
