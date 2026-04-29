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
- websocket-first Slack event detection
- targeted Slack API hydration for channel roots and thread replies
- thread/session mapping to local Codex sessions
- thread reply resume for existing sessions
- waiting-state reminders and auto-close handling
- manual `bob close` thread closure with later resume support
- cleanup of obsolete waiting prompts after resolution
- local process control with `bobctl start|stop|restart|status|tail-log|show-config|doctor`

Current constraints:

- macOS only
- Chrome/Chromium required
- Slack integration uses Slack Web realtime sockets plus private browser-session-backed `/api/...` calls, not Slack’s official public app API
- only targeted waiting-prompt cleanup is implemented; broader Slack message edit/delete flows are still limited

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

Also included in the repo:

```text
config/bob.sample.toml
docs/setup.md
docs/bob-config-setup.md
```

### Example config

```toml
[defaults]

[browser]
slack_signin_url = "https://slack.com/signin?entry_point=nav_menu#/signin"
browser_mode = "shared_browser"
browser_url = "http://127.0.0.1:9222"
cdp_url = "http://127.0.0.1:9222"
browser_user_data_dir = "/Users/you/.cache/personal-slack-agent/chrome-profile"

[runner]
codex_exec_timeout_seconds = 600

[lifecycle]
reminder_minutes = [30]
auto_close_minutes = 120

[orchestrator]
max_concurrent_tasks = 1
max_concurrent_per_thread = 1

[watcher]
root_batch_size = 50
thread_batch_size = 200
thread_reply_rate_limit_backoff_seconds = 60
recent_terminal_thread_reconcile_limit = 6
periodic_terminal_thread_reconcile_batch_size = 1
historical_terminal_thread_reconcile_base_interval_seconds = 60
historical_terminal_thread_reconcile_max_interval_seconds = 900
bob_ultimate_mode = false

[[workspaces]]
name = "my-workspace"
slack_url = "https://app.slack.com/client/T12345678/C12345678"
slack_api_origin = "https://example.enterprise.slack.com"
slack_api_token = "xoxc-..."

[workspaces.channel_defaults]
allowed_actor_ids = ["U12345678"]
default_cwd = "/Users/you/Code"
additional_roots = ["/Users/you/Code"]
accept_root_bob_requests = true
codex_home_mode = "default"

[[workspaces.channels]]
name = "my-private-channel"
allowed_actor_ids = ["U12345678"]
persistent_memory_mode = "owner_only"
persistent_memory_owner = "bob_owner_handle"

[[workspaces.channels]]
name = "my-shared-bob-channel"
codex_sandbox_mode = "workspace-write"
persistent_memory_mode = "disabled"
```

`watcher.bob_ultimate_mode = false` preserves the current configured-channel Bob behavior. Set `watcher.bob_ultimate_mode = true` to allow explicit `bob ...` invocation from any accessible public/private channel, DM, or group DM, still restricted by `allowed_actor_ids`; in that mode Bob appends working and final status into the invoking message instead of posting a separate Bob reply for that invocation.

### Important config notes

For a field-by-field explanation of `bob.toml`, see:

```text
docs/bob-config-setup.md
```

- `allowed_actor_ids`
  At `[workspaces.channel_defaults]`, this defines who may trigger or resume Bob work in that workspace's channels by default.
  Optionally set it again on `[[workspaces.channels]]` to override that workspace default for one channel.

- `workspaces.channel_defaults`
  Use this to define the default cwd, roots, Bob acceptance policy, and Codex sandbox/home behavior
  that should apply to channels in one workspace unless a channel overrides them directly.

- `slack_url`
  This should point to any Slack client URL inside the target workspace.
  Bob resolves per-channel ids from the rendered sidebar DOM at startup, so channels only need names in config.

- `slack_api_origin`
  This is the same-origin Slack web host Bob will use for browser-session-backed `/api/...` calls.

- `slack_api_token`
  This is currently the browser-session token used for the private Slack web API path.
  Treat it as sensitive.

- `post_terminal_threads_here`
  Channels with this flag can be targeted by the `bob` terminal wrapper for terminal-originated Bob requests.
  If exactly one channel across your config has this flag, `bob "<prompt>"` can use it by default.

- `persistent_memory_mode`
  Required for every configured channel.
  Use `owner_only` for a private channel that is allowed to update one person's durable preference notes.
  Use `disabled` for shared or test channels that must not update personal durable notes.

- `persistent_memory_owner`
  Required only when `persistent_memory_mode = "owner_only"`.
  This identifies whose durable preference notes the channel may update.

- `slack_channel_id`
  Optional per channel.
  Use this when Slack does not expose the channel in the rendered sidebar for Bob's browser session.
  If provided, Bob will seed the channel route directly instead of relying on sidebar discovery for that channel.

### Automatic Slack auth bootstrap

If the target workspace is already open in your debuggable Chrome session, you can populate
`slack_api_origin` and `slack_api_token` automatically:

```bash
.venv/bin/bob-init --discover-slack-auth --workspace my-workspace
```

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
4. open any page inside the workspace you configured

## Bob Chrome Dock Launcher

Install the reusable debug-browser app:

```bash
.venv/bin/bobctl install-chrome-launcher --force
```

This writes `~/Applications/Bob Chrome.app`.

At install time, Bob renders that launcher from the current `[browser]` config in `bob.toml`:

- `browser.cdp_url`
- `browser.browser_user_data_dir`
- `browser.chrome_executable_path` when set

If you use a non-default config file, pass it when installing:

```bash
.venv/bin/bobctl install-chrome-launcher --config ~/.config/personal-slack-agent/bob.toml --force
```

Behavior:

- if the configured `browser.cdp_url` is already live, it just foregrounds the configured Chrome app
- otherwise it launches a fresh debug-enabled Chrome instance using the configured app/profile settings
- it does not open Slack or any other URL automatically

`Bob Chrome.app` is safe to pin to the macOS Dock.
If you later change the `[browser]` settings, rerun the same install command to refresh the installed app, including `--config ...` when you use a non-default config file.

This launcher is for the browser only. Bob itself still must be started or restarted from a normal unsandboxed shell.

## Usage

### Start Bob in background

```bash
.venv/bin/bobctl start --config ~/.config/personal-slack-agent/bob.toml --poll-interval-seconds 10
```

`--poll-interval-seconds` is the idle cycle / recovery interval. Bob does not walk every channel on every tick anymore; normal detection is websocket-driven and the interval mainly controls reconnect recovery cadence and stop-file responsiveness.

Check status:

```bash
.venv/bin/bob "summarize this repo" --workspace my-workspace --channel my-private-channel
.venv/bin/bobctl status
.venv/bin/bobctl doctor
.venv/bin/bobctl smoke-test --workspace my-workspace --channel my-private-channel
```

Restart Bob:

```bash
.venv/bin/bobctl restart --config ~/.config/personal-slack-agent/bob.toml --poll-interval-seconds 10
```

Tail logs:

```bash
.venv/bin/bobctl tail-log --lines 50
```

Stop Bob:

```bash
.venv/bin/bobctl stop
```

### One-shot cycle

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
   - `_*Bob is working on it :arrows_counterclockwise::*_ <session-id>`
4. post final output as:
   - `_*codex Bob :white_check_mark::*_ ...`

If you reply in the thread later, Bob resumes the same local Codex session.

If Bob is waiting for input or approval:

- configured reminders apply only to those waiting states
- auto-close applies only to those waiting states
- reply with `bob close` to close the thread without losing the underlying Codex session
- reply again later in the same thread to resume

### Terminal Requests

You can start a Bob request from the terminal with the `bob` wrapper:

```bash
.venv/bin/bob --workspace my-workspace --channel my-private-channel "summarize this repo"
```

If exactly one configured channel has `post_terminal_threads_here = true`, you can omit the target and run:

```bash
.venv/bin/bob "summarize this repo"
```

The wrapper posts a real root Bob message into Slack, waits for the Bob session to finish, and prints the Slack thread id, Codex session id, and final Bob reply.

### Live smoke test

Before relying on Bob for real work:

1. run `bobctl doctor`
2. confirm `config_loaded: True`
3. confirm the configured `cdp_url` is reachable
4. confirm your target workspace/channel appears in the doctor output
5. run `bobctl smoke-test --workspace my-workspace --channel my-private-channel`
6. verify it prints `Smoke test passed.` with a `thread_ts`, `session_id`, and final Bob reply

If that fails, check `bobctl tail-log --lines 100` before changing config or code.

## Testing

Run the full test suite:

```bash
.venv/bin/python -m pytest -q
```

## Security notes

- This project relies on Slack Web realtime sockets plus browser-session-backed private Slack web requests.
- The `slack_api_token` is sensitive and should not be committed.
- Do not publish your personal config file.
- Do not share your Chrome profile or Slack browser session.

## Project layout

- `src/personal_slack_agent/`
  package source
- `tests/`
  automated tests
- `config/bob.sample.toml`
  committed sample config template
- `docs/setup.md`
  dedicated setup guide
- `pyproject.toml`
  packaging metadata

## Current limitations

- macOS only
- no official Slack app/OAuth integration
- no broad Slack message edit/delete flow beyond targeted waiting-prompt cleanup
- browser/web request behavior may need adjustment if Slack changes its private web client APIs or websocket protocol
