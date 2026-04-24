# Bob Ultimate Mode Design

## Goal

Add an opt-in Bob mode that lets Yifan invoke Bob with an explicit `bob ...` message from any accessible Slack conversation, including public channels, private channels, DMs, and group DMs, while keeping the current configured-channel Bob behavior unchanged unless the new mode is enabled.

## Current Problem

Bob currently assumes all supported conversations are explicitly listed in `bob.toml`. It only creates new sessions from root messages that begin with `bob`, only resumes tracked configured threads, and always posts Bob status/output as separate thread replies. That does not support the desired workflow where Yifan can reply inside an arbitrary thread with `bob can you do it?` and have Bob acknowledge the message, append working status into that same message, append the final Bob response into that same message, and then stop monitoring unless Yifan explicitly invokes Bob again.

## Approved Scope

- Add a config-gated feature flag named `bob_ultimate_mode`.
- Place the flag under `[watcher]`.
- Default the flag to `false` so current behavior is preserved.
- When `bob_ultimate_mode = true`, allow explicit `bob ...` invocation from any accessible conversation in the configured workspace, including public/private channels, DMs, and group DMs.
- Only allowed actor ids may invoke the mode; in practice this remains Yifan-only through existing actor-id config.
- Treat each explicit `bob ...` message as a one-time invocation.
- Reuse the same Codex session for later explicit `bob ...` messages in the same Slack thread.
- Before each explicit invocation, hydrate the full current Slack thread and include it in the prompt so Codex sees replies that may have happened outside the local session.
- For the new mode, Bob must add `:ack:` to the invoking message and then edit that same message to append the working line and the final `codex Bob` line instead of posting separate Bob replies.
- After an invocation completes, Bob does not keep monitoring the thread for non-`bob` replies. A later explicit `bob ...` message from Yifan triggers the next one-shot run.

## Out of Scope

- Changing the existing configured Bob-channel flow when `bob_ultimate_mode = false`.
- Replacing the current thread/session persistence model with a conversation-id-first architecture.
- Making other users able to invoke workspace-wide Bob.
- Building a general Slack message-edit/delete system beyond what is required for Bob’s inline invocation path.

## Architecture

### Feature Flag

Add `watcher.bob_ultimate_mode: bool` with default `false`.

- `false`: keep current ingress, tracking, and Bob reply behavior.
- `true`: enable workspace-wide explicit invocation discovery and inline message-edit response behavior.

`[watcher]` is the correct config surface because this mode primarily changes which Slack conversations Bob watches and which inbound Slack events are routed into the orchestrator.

### Conversation Identity

Current state keys are `(workspace_name, channel_name, thread_ts)`. That model can stay if Bob gains a runtime channel layer:

- explicitly configured channels keep their existing configured names
- unconfigured conversations get a synthetic runtime channel key derived from Slack conversation id, for example `slack:C123...`, `slack:D123...`, or `slack:G123...`
- the synthetic channel config is resolved from `workspaces.channel_defaults`, plus the concrete Slack conversation id

This avoids a full conversation-id migration while still giving Bob a stable state key for any accessible conversation.

### Workspace-Wide Discovery

When `bob_ultimate_mode = true`, watcher ingress must no longer depend only on `workspace.channels`.

Watcher should:

- register websocket event handling at the workspace level as it already does
- lazily learn conversation ids from incoming events and API hydration
- allow root-message hydration and thread-reply hydration for any accessible conversation in the workspace
- resolve the runtime channel key and effective runtime channel config before handing the event to the orchestrator

The runtime channel config should inherit:

- `allowed_actor_ids`
- `default_cwd`
- `additional_roots`
- `codex_home_mode`
- `codex_sandbox_mode`
- `codex_workspace_write_writable_roots`
- `persistent_memory_mode`
- `persistent_memory_owner`

from `workspaces.channel_defaults`.

`accept_root_bob_requests` should still apply for the runtime channel view. In ultimate mode, explicit `bob ...` messages remain gated by the effective allowed actors.

### Invocation Classification

There are now two distinct Bob entry paths:

1. **Normal configured-channel Bob flow**
   - existing path
   - thread creation/resume behavior stays unchanged
   - Bob posts thread replies as it does today

2. **Ultimate inline invocation flow**
   - only active when `bob_ultimate_mode = true`
   - trigger: trimmed text starts with `bob`
   - can happen on a root message or a reply in any accessible conversation
   - response target is the invoking message itself
   - session identity is the Slack thread

For replies:

- if the reply belongs to an existing tracked Bob thread in configured mode, current behavior stays
- if the reply starts with `bob` in ultimate mode, it becomes a one-shot inline invocation even if the thread was not previously Bob-tracked

### Session and Thread Reuse

The thread remains the durable session identity:

- first explicit invocation in a thread creates a Codex session
- later explicit `bob ...` invocations in that same thread resume the same session
- non-`bob` replies do not enqueue work in ultimate mode after the invocation completes

This matches the requested “one time only unless I start with bob again” behavior.

### Full-Thread Hydration

Before each ultimate-mode invocation, Bob must fetch the full current thread transcript and build a prompt section that includes:

- workspace name
- runtime channel identity
- root message text
- ordered thread replies with actor ids and timestamps
- which Slack message is the current invocation target

That transcript should be wrapped into the Codex prompt for both:

- a new session start
- a session resume

The full thread must be refreshed for each explicit invocation because the local Codex session may not have seen replies posted since the last run.

### Inline Slack UX

For ultimate-mode invocations, Bob’s Slack behavior is:

1. add `:ack:` reaction to the invoking message
2. edit the invoking message and append a new line:
   - `Bob is working on it :arrows_counterclockwise:: session=... thread=...`
3. after completion, edit the same message again and append a new line:
   - `codex Bob :white_check_mark:: ...`

The original user text stays intact at the top of the message.

If the run ends in a waiting state:

- append a single terminal line indicating Bob needs input or approval
- do not keep background monitoring for ordinary thread replies
- a later explicit `bob ...` message resumes the same session

If Slack message editing fails:

- Bob should fall back to the existing thread-reply delivery path so the run result is not lost

### Slack Adapter Changes

The Slack adapter currently supports posting messages, reactions, snippets, and thread reads, but not message edits. Add:

- `chat.update` in the Slack API client
- `update_message` in the Slack browser adapter protocol
- `update_message` in the Playwright adapter

The orchestrator also needs an append helper that:

- reads the current message text or tracks the latest appended text for the invocation target
- appends normalized Bob lines exactly once per intent key
- updates the same message text

### State Model Changes

Current session state persists thread-level session records. That remains valid, but tasks and outbound intents need extra metadata for ultimate mode:

- task/intent delivery target type:
  - `thread_reply`
  - `message_append`
- target message timestamp for inline edits
- whether a thread is configured-mode or ultimate-mode runtime-mode
- conversation id / effective Slack channel id for runtime channels

Outbound intent dedupe must work for both:

- posted thread replies
- inline message append edits

### Prompt Shape

The existing Bob prompt wrapper should remain, but ultimate mode needs extra context:

- conversation identity
- that this is an explicit one-shot Slack invocation
- full thread transcript
- current invocation message timestamp
- instruction that the Slack-facing output will be appended into the invoking message

The final text returned by Codex should still be plain answer text. Bob adds the `codex Bob` label in Slack, not inside the prompt contract.

## Data Flow

### New Root Invocation in Ultimate Mode

1. watcher sees an accessible root message starting with `bob`
2. watcher resolves or synthesizes runtime channel config
3. orchestrator verifies actor is allowed
4. Bob adds `:ack:` to the invoking message
5. Bob hydrates the full thread transcript
6. orchestrator creates or reuses the thread session
7. Bob appends the working line into the invoking message
8. Codex runs
9. Bob appends the final line into the invoking message
10. session becomes idle/closed as today

### Reply Invocation in Ultimate Mode

1. watcher sees a reply message inside any accessible thread
2. if trimmed text does not start with `bob`, ignore it
3. if it starts with `bob`, resolve runtime channel config and actor authorization
4. add `:ack:` to that reply
5. hydrate the full current thread transcript
6. create or resume the session keyed by that thread
7. append working/final lines into that same reply message
8. stop until another explicit `bob ...` message appears

## Error Handling

- unauthorized actor: ignore the message completely
- feature flag disabled: current behavior only
- thread hydration failure: fail closed and post a concise Bob error via fallback thread reply
- message edit failure: fall back to thread reply delivery
- session resume failure: append/fallback a Bob error line and keep the session available for a later retry
- Slack rate limit during broader workspace reconciliation: keep current backoff behavior

## Testing

Required coverage:

- config parsing/default/dump for `watcher.bob_ultimate_mode`
- runtime synthetic channel resolution from workspace channel defaults
- explicit root `bob ...` invocation in an unconfigured accessible conversation
- explicit reply `bob ...` invocation in an arbitrary thread whose root is not a Bob message
- Yifan-only invocation enforcement via actor ids
- same-message append behavior for working and final output
- session reuse on a later `bob ...` in the same thread
- full-thread hydration being included on each explicit invocation
- no follow-up processing for non-`bob` replies after completion
- fallback to thread reply if Slack message update fails
- regression coverage for existing configured Bob-channel behavior when the flag is `false`

## Risks

- workspace-wide discovery can increase Slack API pressure, so ultimate-mode ingress should prefer lazy event-driven resolution and reuse existing rate-limit backoff.
- appending into an existing message requires careful dedupe so Bob does not duplicate working/final lines after retries.
- runtime synthetic channel keys must stay stable across runs or Bob could split one Slack thread across multiple local session identities.

## Design Review

Self-review results:

- Scope is limited to a feature-gated incremental path and avoids a full state-schema rewrite.
- The design covers config, watcher ingress, runtime channel resolution, orchestrator routing, Slack message edits, and tests.
- The main ambiguity is whether Slack allows message edits in all accessible conversation types through the same API path; the implementation will verify this behind the existing API-backed adapter and retain a reply fallback.
