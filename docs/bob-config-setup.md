# Bob Config Setup

This document explains how `~/.config/personal-slack-agent/bob.toml` is structured, which settings are required, and how to choose values for a practical Bob setup.

This is not the first-time machine bootstrap guide. For installation and Chrome startup, see [docs/setup.md](setup.md).

## Config File Location

Bob reads configuration from:

```text
~/.config/personal-slack-agent/bob.toml
```

Run the interactive setup wizard with:

```bash
bob-init
```

The repo also includes:

```text
config/bob.sample.toml
```

`bob-init` writes a validated first config, then points you to `bobctl show-config`,
`bobctl doctor`, and this guide for further updates.

## Structure Overview

The file is divided into these main sections:

- `[defaults]`: global owner identity and fallback runtime defaults
- `[browser]`: how Bob attaches to Chrome and Slack Web
- `[runner]`: how Bob launches child Codex sessions
- `[lifecycle]`: reminder and auto-close behavior for waiting states
- `[orchestrator]`: concurrency controls
- `[watcher]`: Slack polling and reconciliation tuning
- `[[workspaces]]`: one Slack workspace definition
- `[workspaces.channel_defaults]`: per-workspace defaults applied to channels unless overridden
- `[[workspaces.channels]]`: individual channel behavior

## Minimum Practical Config

For a usable setup, you typically need:

- one `[defaults]` section with `owner_name` and `owner_preferred_name`
- one `[browser]` section with a reachable `cdp_url`
- one `[[workspaces]]` section with `name` and `slack_url`
- one `[workspaces.channel_defaults]` section with at least `default_cwd`
- one or more `[[workspaces.channels]]` entries with `name` and `persistent_memory_mode`
- `persistent_memory_owner` on any channel using `owner_only`

## Section-by-Section Guide

### `[defaults]`

This defines global values Bob uses when wrapping Slack requests for Codex.

Common fields:

- `owner_name`
  The owner's full display name for durable preference guardrails.

- `owner_preferred_name`
  The owner's preferred name for Bob's role prompt.

- `assistant_names`
  Optional list of Slack callsigns that can invoke Bob. If omitted or empty, Bob uses `["Bob"]`.
  Matching is case-insensitive and boundary-aware, so `bobcat` does not invoke `Bob`.
  Bob replies using the exact alias typed by the user for that interaction. For example:

  ```toml
  assistant_names = ["Bob", "Bobby", "Copilot"]
  ```

  The command names are fixed: `bob`, `bobctl`, `bob-agent`, and `bob-init` do not change
  with this setting. The `bob` terminal wrapper may prefix the Slack message with the first
  effective callsign when the prompt does not already start with one.

Keep committed examples anonymized. Put real owner values only in local config files that are not committed.

### `[browser]`

This controls how Bob talks to the logged-in Chrome session.

Common fields:

- `slack_signin_url`
  Usually leave this at the default Slack sign-in URL.

- `browser_mode`
  Supported values:
  - `shared_browser`
  - `dedicated_browser`

  For normal Bob usage in this repo, `shared_browser` is the expected choice. It tells Bob to attach to an already running Chrome session instead of launching its own persistent browser context.

- `browser_url`
  Historical/debugging field pointing at the Chrome remote debugging endpoint. In practice `cdp_url` is the field Bob actually uses for attach checks and Playwright CDP connection.

- `cdp_url`
  Usually:

  ```toml
  cdp_url = "http://127.0.0.1:9222"
  ```

  `bobctl doctor` reports `cdp_reachable` and `browser_attach` against this endpoint.

- `browser_user_data_dir`
  Relevant primarily for dedicated-browser workflows or when you want a stable Chrome profile directory for the Bob browser session.

### `[runner]`

This controls child Codex execution.

- `codex_exec_timeout_seconds`
  Maximum time a Bob-started child Codex execution is allowed to run before the wrapper declares it timed out.

- `bob_codex_home`
  Optional explicit Codex home for Bob child sessions.

  Important operational rule:
  changing `bob_codex_home` is a migration, not a cosmetic path edit. The dynamic state under the old home must be moved before you restart Bob onto the new home.

### `[lifecycle]`

This controls waiting-state reminders and cleanup.

- `reminder_minutes`
  Minutes after which Bob should remind a waiting thread.

- `auto_close_minutes`
  Minutes after which Bob should auto-close a waiting session.

### `[orchestrator]`

This controls Bob's task concurrency.

- `max_concurrent_tasks`
  Maximum number of concurrent Bob tasks across all threads.

- `max_concurrent_per_thread`
  Maximum number of concurrent tasks inside the same Slack thread.

### `[watcher]`

This controls Slack hydration and reconciliation behavior.

Useful fields:

- `root_batch_size`
- `thread_batch_size`
- `thread_reply_rate_limit_backoff_seconds`
- `recent_terminal_thread_reconcile_limit`
- `periodic_terminal_thread_reconcile_batch_size`
- `historical_terminal_thread_reconcile_base_interval_seconds`
- `historical_terminal_thread_reconcile_max_interval_seconds`
- `bob_ultimate_mode`

You usually do not need to change these unless you are tuning performance or investigating Slack reconciliation behavior.

`bob_ultimate_mode = false` preserves the current configured-channel Bob behavior. `bob_ultimate_mode = true` enables explicit configured-callsign invocation from any accessible public/private channel, DM, or group DM, still gated by `allowed_actor_ids`, and appends Bob status/output into the invoking message.

## `[[workspaces]]`

Each workspace section defines one Slack workspace Bob can operate in.

Common fields:

- `name`
  Internal Bob name for the workspace.

- `slack_url`
  Any normal Slack client URL inside the workspace, for example:

  ```toml
  slack_url = "https://app.slack.com/client/bob_team/bob_channel"
  ```

  Bob uses this as the anchor workspace route for the browser session.

- `slack_api_origin`
  Same-origin Slack web host used for the private Slack Web API transport, for example:

  ```toml
  slack_api_origin = "https://bob-company.enterprise.slack.example"
  ```

- `slack_api_token`
  Slack Web session token.

  Treat this as sensitive. Do not commit personal values.

## `[workspaces.channel_defaults]`

This section defines defaults shared by channels inside that workspace.

Common fields:

- `accept_root_bob_requests`
  Whether channels in this workspace accept new root requests by default.

- `allowed_actor_ids`
  Restricts who may invoke Bob in those channels. Empty means no restriction.
  Values are Slack member/user IDs such as `U12345678`, not Slack handles or display names.

  To find a person's Slack member/user ID:
  1. Open the person's Slack profile in the target workspace.
  2. Click `More` or the `...` menu.
  3. Choose `Copy member ID`.
  4. Paste that value into `allowed_actor_ids`.

  Example:

  ```toml
  allowed_actor_ids = ["U12345678"]
  ```

- `default_cwd`
  Default working directory for Bob tasks in this workspace.

- `codex_home_mode`
  Common values:
  - `default`
  - `isolated`

  `isolated` means the channel should use Bob's dedicated `CODEX_HOME` instead of the caller's normal Codex home.

- `codex_workspace_write_writable_roots`
  Extra writable roots passed when workspace-write sandboxing is used.

- `persistent_memory_mode`
  Expected values:
  - `owner_only`
  - `disabled`

- `slack_channel_id`
  Optional direct Slack channel id seed. Useful when the channel is not discoverable from the rendered Slack sidebar in the current browser session.

## `[[workspaces.channels]]`

Each channel entry can inherit from channel defaults or override them.

Common fields:

- `name`
  Slack channel name as Bob should know it.

- `post_terminal_threads_here`
  Marks the channel as eligible for terminal-originated Bob requests.

  If exactly one channel across the config has this set to `true`, terminal commands like `bob "..."` can target it by default.

- `codex_home_mode`
  Per-channel override for `default` vs `isolated`.

- `codex_sandbox_mode`
  Per-channel override for child Codex sandbox behavior, for example:
  - `workspace-write`
  - `danger-full-access`

- `persistent_memory_mode`
  Required in practice for every configured channel.

- `persistent_memory_owner`
  Required when `persistent_memory_mode = "owner_only"`.

- `allowed_actor_ids`
  Optional per-channel override for who may invoke Bob in this channel.
  Use Slack member/user IDs copied from the user's Slack profile, for example `["U12345678"]`.
  Set `[]` to allow any actor who can post in that channel.

- `slack_channel_id`
  Optional direct id seed for this specific channel.

## Recommended Channel Patterns

### Private owner channel

Use for personal Bob work where durable preferences may be updated:

```toml
[[workspaces.channels]]
name = "bob_private_channel"
post_terminal_threads_here = true
codex_home_mode = "default"
persistent_memory_mode = "owner_only"
persistent_memory_owner = "bob_owner_handle"
additional_roots = ["/Users/bob_owner_handle/Code"]
```

### Shared Bob channel

Use for normal shared Bob work where durable personal memory must stay disabled:

```toml
[[workspaces.channels]]
name = "bob_channel"
codex_sandbox_mode = "workspace-write"
codex_home_mode = "isolated"
persistent_memory_mode = "disabled"
```

### Test channel

Use for smoke tests and integration validation:

```toml
[[workspaces.channels]]
name = "bob_test_channel"
codex_sandbox_mode = "workspace-write"
codex_home_mode = "isolated"
persistent_memory_mode = "disabled"
```

## How To Read `bobctl doctor`

The most useful fields are:

- `cdp_reachable`
  Whether Chrome's remote debugging endpoint is reachable.

- `browser_attach`
  Whether Bob can attach Playwright to the shared browser session.

- `terminal_default_target`
  The workspace/channel Bob will use for terminal-originated requests by default.

- `terminal_codex_exec`
  Lightweight Bob-style child Codex execution probe.

  Important:
  run `bobctl doctor` from a normal unsandboxed shell for operator truth.
  If you run it from inside another sandboxed Codex session, `terminal_codex_exec` may fail because nested sandboxing is blocked, even while Bob itself is healthy.

## Common Failure Modes

### `browser_attach: False`

Usually means:

- Chrome debugging endpoint is stale or unreachable
- shared browser session is unhealthy
- Bob needs a clean restart after a browser attach failure

### `terminal_codex_exec: False`

Usually means one of:

- Bob child Codex sessions cannot launch in the current sandbox context
- Bob was started from inside another sandboxed Codex session and inherited a bad sandbox environment
- child-session config such as writable roots or `CODEX_HOME` is invalid

### Channel name present but channel id missing

Usually means Slack did not expose the channel in the rendered sidebar. Add `slack_channel_id` for that channel in `bob.toml`.

## Operational Notes

- The local `bobctl` install in this environment is typically an editable install against this repo checkout.
- Normal Bob operation should no longer require a persistent visible `https://bob-company.enterprise.slack.example/api/api.test` helper tab for Slack API calls.
- Bob still requires the main Slack workspace tab for websocket-driven detection, channel discovery, and auth bootstrap.

## Related Docs

- [README.md](../README.md)
- [docs/setup.md](setup.md)
- [docs/how-it-works.md](how-it-works.md)
- [docs/command-reference.md](command-reference.md)
- [docs/publishing.md](publishing.md)
- [config/bob.sample.toml](../config/bob.sample.toml)
