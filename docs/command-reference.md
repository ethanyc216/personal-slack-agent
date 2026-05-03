# Bob Command Reference

This page is the operator-facing reference for the command-line entry points installed by this repo.

The packaged commands are declared in `pyproject.toml`:

| Command | Entry point | Primary use |
| --- | --- | --- |
| `bob` | `personal_slack_agent.cli:wrapper_main` | Start a terminal-originated Bob request through Slack. |
| `bob-agent` | `personal_slack_agent.cli:agent_main` | Run the long-lived Slack watcher and Codex orchestrator. |
| `bob-init` | `personal_slack_agent.cli:init_main` | Interactively generate or update local Bob configuration. |
| `bobctl` | `personal_slack_agent.cli:ctl_main` | Start, stop, inspect, and diagnose the local Bob process. |

These command names stay fixed even when `defaults.assistant_names` customizes Slack-facing callsigns.

Use `.venv/bin/<command>` from a repo checkout, or `<command>` directly when the package is installed on your `PATH`.

## Shared Files

By default, Bob uses these local files:

| Path | Purpose |
| --- | --- |
| `~/.config/personal-slack-agent/bob.toml` | Main local Bob configuration. |
| `~/.local/share/personal-slack-agent/` | Runtime state directory. |
| `~/.local/share/personal-slack-agent/bob.sqlite3` | Bob thread/session state database. |
| `~/.local/share/personal-slack-agent/logs/bob.log` | Bob runtime log. |
| `~/.local/share/personal-slack-agent/bob.pid` | Best-effort pid marker for the running agent. |
| `~/.local/share/personal-slack-agent/bob.lock` | Single-instance lock marker. |
| `~/.local/share/personal-slack-agent/bob.stop` | Cooperative stop request file. |

If `runner.bob_codex_home` is set in `bob.toml`, Bob child Codex sessions use that Codex home. Otherwise they use `~/.local/share/personal-slack-agent/codex-home`.

Important: changing `runner.bob_codex_home` is a runtime-state migration. Copy the dynamic Codex state into the new home before restarting Bob onto it.

## `bob`

`bob` starts a real Slack-thread-backed Bob request from the terminal.

```bash
bob --workspace my-workspace --channel my-private-channel "summarize this repo"
```

If exactly one configured channel has `post_terminal_threads_here = true`, the target can be omitted:

```bash
bob "summarize this repo"
```

What it does:

1. Loads `bob.toml`.
2. Resolves the target workspace/channel.
3. Posts through the fixed `bob` command name while ensuring the Slack message starts with an effective callsign.
4. Posts a real root Bob message into Slack.
5. Waits for Bob to finish the backing Codex session.
6. Prints the Slack thread timestamp, Codex session id, and final Bob reply.

Options:

| Option | Meaning |
| --- | --- |
| `--workspace WORKSPACE` | Workspace name from `bob.toml`. |
| `--channel CHANNEL` | Channel name from `bob.toml`. |
| `--timeout-seconds SECONDS` | Maximum time to wait for completion. Default: `1800`. |
| `--poll-interval-seconds SECONDS` | How often to poll Bob state while waiting. Default: `1`. |

Typical output includes:

```text
Bob request completed.
thread_ts: ...
session_id: ...
final_message: ...
```

Use `bob` when you want the same path as a Slack-originated request, including Slack thread history and later thread resume.

## `bob-agent`

`bob-agent` runs the actual Bob runtime loop.

```bash
bob-agent --config ~/.config/personal-slack-agent/bob.toml
```

Normal operation should use `bobctl start` instead of invoking `bob-agent` directly. Direct `bob-agent` execution is mainly useful for debugging.

What it does:

1. Loads Bob config.
2. Acquires the single-instance lock.
3. Initializes the state database.
4. Attaches to the configured Slack browser session.
5. Prepares Bob's Codex home.
6. Watches Slack for Bob requests and follow-up replies.
7. Starts or resumes local Codex sessions.
8. Posts working, waiting, approval, error, and final replies back to Slack.

Options:

| Option | Meaning |
| --- | --- |
| `--config PATH` | Config file path. Default: `~/.config/personal-slack-agent/bob.toml`. |
| `--once` | Run one startup/poll cycle and exit. Useful for debugging setup. |
| `--poll-interval-seconds SECONDS` | Idle interval between watcher cycles. Default: `30`, or `BOB_POLL_INTERVAL_SECONDS`. |

One-shot debugging example:

```bash
bob-agent --once --config ~/.config/personal-slack-agent/bob.toml
```

Operational note: do not start or restart Bob from inside a sandboxed Codex session. Start it from a normal unsandboxed shell so Bob's child Codex sessions do not inherit a nested sandbox.

## `bob-init`

`bob-init` creates or updates local Bob configuration.

Run the interactive setup wizard:

```bash
bob-init
```

The wizard prompts for owner identity, workspace, channel, default working directory,
and memory policy, then writes a validated config:

```text
~/.config/personal-slack-agent/bob.toml
```

Options:

| Option | Meaning |
| --- | --- |
| `--config PATH` | Config file path. Default: `~/.config/personal-slack-agent/bob.toml`. |
| `--force` | Overwrite an existing generated config. |
| `--discover-slack-auth` | Discover Slack Web API auth from the logged-in browser session and write it into config. |
| `--workspace WORKSPACE` | Workspace to update when using `--discover-slack-auth`. |

Discover Slack auth:

```bash
bob-init --discover-slack-auth --workspace my-workspace
```

That command attaches to the configured browser session, discovers browser-session-backed Slack API auth, and writes `slack_api_origin` and `slack_api_token` into the matching workspace config.

Security note: `slack_api_token` is sensitive local state. Do not commit a personal `bob.toml`.

## `bobctl`

`bobctl` is the process-control and diagnostics command for Bob.

```bash
bobctl status
```

Subcommands:

| Subcommand | Purpose |
| --- | --- |
| `start` | Start `bob-agent` in the background. |
| `restart` | Cooperatively stop the current agent and start a new one. |
| `stop` | Request the running agent to stop. |
| `status` | Show whether Bob appears to be running. |
| `install-chrome-launcher` | Install the `Bob Chrome.app` launcher for the configured debug browser. |
| `tail-log` | Print trailing Bob log lines. |
| `show-config` | Print the resolved config path and contents. |
| `doctor` | Run config, browser, workspace/channel, and child Codex diagnostics. |
| `smoke-test` | Post a live test request through Slack and wait for Bob to finish it. |

Start Bob:

```bash
bobctl start --config ~/.config/personal-slack-agent/bob.toml --poll-interval-seconds 10
```

Restart Bob:

```bash
bobctl restart --config ~/.config/personal-slack-agent/bob.toml --poll-interval-seconds 10
```

Stop Bob:

```bash
bobctl stop
```

Force stop when cooperative stop does not complete:

```bash
bobctl stop --force
```

Inspect Bob:

```bash
bobctl status
bobctl show-config --config ~/.config/personal-slack-agent/bob.toml
bobctl doctor --config ~/.config/personal-slack-agent/bob.toml
bobctl tail-log --lines 50
```

Run a live smoke test:

```bash
bobctl smoke-test --workspace my-workspace --channel my-private-channel
```

Install or refresh the Chrome launcher:

```bash
bobctl install-chrome-launcher --config ~/.config/personal-slack-agent/bob.toml --force
```

`bobctl doctor` fields to check first:

| Field | Meaning |
| --- | --- |
| `config_loaded` | Bob could load the configured `bob.toml`. |
| `cdp_reachable` | Chrome remote debugging endpoint is reachable. |
| `browser_attach` | Bob can attach Playwright to the browser session. |
| `terminal_default_target` | Workspace/channel used by `bob` when target flags are omitted. |
| `terminal_codex_exec` | Lightweight Bob-style child Codex execution probe. |

Run `bobctl doctor` from a normal unsandboxed shell for operator truth. Inside a sandboxed Codex session, the child Codex probe can fail because nested sandboxing is blocked even when the real Bob daemon is healthy.

## Local Alias: `bobcodex`

`bobcodex` is not installed by this repo and is not declared in `pyproject.toml`.

In a local shell, it can be configured as a convenience alias equivalent to:

```bash
CODEX_HOME=/Users/bob_owner_handle/.local/share/personal-slack-agent/codex-home codex
```

Use it to launch normal Codex while pointing at Bob's isolated Codex home. That is useful for inspecting or debugging Bob-owned sessions and runtime state without changing the normal `~/.codex` home.

Because this is a personal shell alias, it may not exist in non-interactive shells or on another machine.

## Related Docs

- [Setup guide](setup.md)
- [How Bob works](how-it-works.md)
- [Bob config setup](bob-config-setup.md)
- [Publishing guide](publishing.md)
- [Sample config](../config/bob.sample.toml)
- [README](../README.md)
