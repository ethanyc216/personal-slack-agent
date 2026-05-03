# How Bob Works

Bob connects local Codex execution to an approved company messaging surface.
The current implementation targets Slack Web, but the core idea is broader:
keep work local while making the work visible in the message system where people
already coordinate.

## Background

In many company environments, connecting an AI coding tool directly to Slack is
not as simple as installing an app. Common restrictions include:

- hosted connectors cannot be granted access to internal Slack messages
- Slack app installation or OAuth scopes require security review
- internal code and prompts must not leave approved local or company-controlled
  environments
- teams still need a visible place to track long-running agent work, approvals,
  and final output

Bob is a local bridge for that situation. It does not require a custom Slack app
install. It uses the user's existing browser-authenticated Slack session and
runs Codex locally on the same machine.

## Design Goals

- Keep Codex execution local.
- Use Slack as a coordination and audit surface.
- Make every Bob conversation thread-backed and resumable.
- Keep channel authorization and persistent-memory policy explicit.
- Avoid committing personal Slack tokens, Chrome profiles, or machine-specific
  config.
- Prefer configuration for environment-specific behavior.

## Non-Goals

- Bob is not an official Slack app integration.
- Bob is not a hosted SaaS bot.
- Bob is not a generic Slack archive or broad message scraper.
- Bob does not try to hide Python source code when packaged.

## Main Components

| Component | Purpose |
| --- | --- |
| Chrome session | Holds the user's Slack Web login and remote-debugging endpoint. |
| Slack adapter | Attaches to Slack Web, discovers workspace/channel context, and sends/reads targeted messages. |
| Watcher | Detects Bob invocations and follow-up replies. |
| State store | Maps Slack threads to Codex sessions and lifecycle state. |
| Orchestrator | Enforces concurrency and decides when to start or resume work. |
| Codex runner | Launches local Codex child sessions with configured cwd, roots, sandbox, and Codex home. |
| Slack poster | Writes working, waiting, approval, error, and final messages back to Slack threads. |
| CLI tools | Provide setup, process control, diagnostics, terminal-originated requests, and smoke tests. |

## Message Flow

1. A user posts a Slack message such as `Bob, summarize this repo`.
2. Bob verifies that the workspace/channel and actor are allowed.
3. Bob creates or locates the Slack thread for the request.
4. Bob stores the thread/session relationship in local state.
5. Bob posts a working message that includes the local Codex session id.
6. Bob launches local Codex with the configured working directory, writable
   roots, sandbox mode, and Codex home.
7. Bob posts the final answer, waiting state, approval request, or error back
   into the same Slack thread.
8. Later replies in that Slack thread resume the same Codex session.

This makes Slack the visible work log while leaving the actual execution local.

## What The Slack Thread Looks Like

The screenshot in the README is synthetic and sanitized. It shows the shape of a
typical Bob interaction:

- a human asks whether Bob is online
- Bob reacts/acknowledges the request
- Bob posts a working status with a session id
- Bob posts a final answer
- a follow-up reply continues the same thread/session

The real runtime uses actual Slack thread timestamps and Codex session ids. The
committed screenshot intentionally uses fake names, avatars, timestamps, and ids.

## Browser-Authenticated Slack Transport

Bob expects a debuggable Chrome session that is already logged into Slack. The
browser is used for:

- Slack auth bootstrap
- workspace/channel discovery
- websocket-driven event detection
- targeted Slack Web request/response paths

This avoids a Slack app install, but it also means Bob depends on Slack Web
behavior. If Slack changes its private web client APIs or realtime protocol,
Bob may need code changes.

## Local State

Bob keeps local runtime state under `~/.local/share/personal-slack-agent/` by
default. The important state includes:

- thread/session mappings
- waiting and terminal lifecycle state
- Bob process lock and pid files
- logs
- optional Bob-specific Codex home state

If `runner.bob_codex_home` is moved, treat it as a runtime-state migration. Copy
the dynamic Codex state before restarting Bob onto the new path.

## Channel And Memory Policy

Bob supports per-workspace defaults and per-channel overrides. Important controls
include:

- `allowed_actor_ids`: who can invoke Bob
- `default_cwd`: where Codex work starts
- `additional_roots`: extra writable roots when sandboxing allows them
- `codex_home_mode`: default or isolated Codex home behavior
- `persistent_memory_mode`: whether a channel can update durable personal notes

Shared/test channels should usually use `persistent_memory_mode = "disabled"`.
Private owner channels can use `owner_only` with `persistent_memory_owner`.

## Terminal-Originated Requests

The `bob` terminal wrapper can post a real Slack root message, wait for the Bob
session to finish, and print the final result locally. This gives terminal
requests the same Slack-visible audit path as Slack-originated requests.

## Related Docs

- [README](../README.md)
- [Setup guide](setup.md)
- [Config guide](bob-config-setup.md)
- [Command reference](command-reference.md)
- [Publishing guide](publishing.md)
