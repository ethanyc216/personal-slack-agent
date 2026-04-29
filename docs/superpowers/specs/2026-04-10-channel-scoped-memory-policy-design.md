# Channel-Scoped Memory Policy Design

## Goal

Ensure Bob can use the full Codex capability surface in every allowed Slack channel while preventing shared or test channels from updating Bob owner's personal durable preference notes.

## Problem

Bob currently forwards Slack thread text directly into Codex sessions without any explicit channel-scoped memory policy. That means Codex has no native idea whether a conversation came from:

- `bob_private_channel`, where durable personal preference capture is allowed
- `bob_test_channel`, where testing conversation should not update Bob owner's personal notes
- `bob_channel`, where coworker conversation should not update Bob owner's personal notes

The missing boundary is not about capability. Bob users in shared channels should still be able to use the same tools, skills, MCP servers, and agents. The missing boundary is about whose durable preferences may be updated from a given channel context.

## Requirements

### Functional

- Bob must attach explicit channel context to every Codex request.
- Bob must attach explicit durable-memory policy to every new session and every resumed session.
- `bob_private_channel` must allow durable personal preference updates for owner `bob_owner_handle`.
- `bob_channel` and `bob_test_channel` must forbid updates to Bob owner's personal session note or other durable preference files.
- Shared/test channels must still allow full normal Bob capabilities, including skills, MCP, agents, and tools.
- The design must be identity-ready so future channels can target a different durable-memory owner or disable owner updates entirely.

### Configuration

- The new channel memory-policy fields are required for configured channels.
- Bob startup must fail fast if a channel omits the required memory-policy configuration.
- The configuration model must make the intended policy obvious when reading `bob.toml`.

### Non-Goals

- Do not implement per-coworker durable note storage in this change.
- Do not restrict tool access, skills, MCP, agents, or approval behavior by channel.
- Do not attempt to infer memory policy from message author, channel naming conventions, or Slack metadata alone.

## Proposed Design

### 1. Required channel memory policy

Each configured channel declares a durable-memory policy with two fields:

- `persistent_memory_mode`
  - Allowed values:
    - `owner_only`
    - `disabled`
- `persistent_memory_owner`
  - Required when `persistent_memory_mode = "owner_only"`
  - Omitted when `persistent_memory_mode = "disabled"`

Example intent:

- `bob_private_channel`
  - `persistent_memory_mode = "owner_only"`
  - `persistent_memory_owner = "bob_owner_handle"`
- `bob_channel`
  - `persistent_memory_mode = "disabled"`
- `bob_test_channel`
  - `persistent_memory_mode = "disabled"`

This shape is identity-ready because future channels can point at a different owner without changing orchestration semantics.

### 2. Channel-scoped Codex prompt policy

Bob will stop sending raw Slack text directly to Codex. Instead, Bob will build a wrapped prompt for every new session and every resume. The wrapper will include:

- workspace name
- channel name
- durable-memory mode
- durable-memory owner when present
- explicit statement that all normal Codex capabilities remain available
- explicit rule describing whether personal durable preferences may be updated

Representative prompt wrapper for a shared/test channel:

```text
Bob execution context:
- workspace: bob_company
- channel: bob_test_channel
- persistent_memory_mode: disabled
- persistent_memory_owner: none

Rules:
- You may use all available tools, skills, MCP servers, and agents normally.
- This Slack channel does not grant permission to update Bob owner's personal durable preference files.
- Do not update personal session notes or similar durable preference files for Bob owner from this conversation.

User request from Slack:
<original Slack text>
```

Representative prompt wrapper for Bob owner's private channel:

```text
Bob execution context:
- workspace: bob_company
- channel: bob_private_channel
- persistent_memory_mode: owner_only
- persistent_memory_owner: bob_owner_handle

Rules:
- You may use all available tools, skills, MCP servers, and agents normally.
- This Slack channel is allowed to update durable personal preference notes for owner `bob_owner_handle` when the conversation reveals a durable preference or workflow rule.

User request from Slack:
<original Slack text>
```

Re-sending this wrapper on resume is required. Codex does not natively know what Slack channel a conversation came from, so Bob must restate the policy every time it sends thread input.

### 3. Scope of restricted files

For `disabled` channels, the instruction should explicitly cover Bob owner's personal durable preference artifacts, especially:

- `~/.codex/memories/session-note.md`
- any equivalent personal preference file Bob or Codex would otherwise update for Bob owner

The restriction is intentionally narrow. It does not block normal execution or collaboration; it only blocks writing shared/test-channel statements into Bob owner's personal durable memory.

### 4. Validation behavior

Configuration loading should reject invalid or incomplete policy combinations, including:

- missing `persistent_memory_mode`
- unknown `persistent_memory_mode`
- `owner_only` without `persistent_memory_owner`
- `disabled` with an unexpected `persistent_memory_owner`

This keeps channel behavior explicit and prevents silent fallback into the wrong memory behavior.

## Alternatives Considered

### Hardcoded shared-channel denylist

Rejected because it hides policy in code, couples behavior to current channel names, and does not scale to future owners.

### Actor-based gating

Rejected because the user's requirement is channel-scoped, not merely author-scoped. Even Bob owner's own messages in `bob_test_channel` should not update his personal durable notes.

### Restricting tools or skills in shared channels

Rejected because it solves the wrong problem. The user wants full Bob capability in those channels, with only durable-memory updates disabled.

## Testing Strategy

- Config parsing tests:
  - valid `owner_only` channel config
  - valid `disabled` channel config
  - rejection for missing or invalid policy fields
- Orchestrator tests:
  - new-session prompt for `bob_private_channel` includes `owner_only` policy and `bob_owner_handle`
  - new-session prompt for `bob_channel` includes `disabled` policy and explicit no-update rule
  - resume prompt for shared/test channels reasserts the no-update rule
  - shared/test prompts still explicitly say all tools, skills, MCP servers, and agents remain available
- Documentation updates:
  - sample config
  - README configuration section

## Expected Outcome

After this change, Bob will remain fully capable in all configured channels, but Codex sessions started from `bob_channel` and `bob_test_channel` will receive explicit instructions not to update Bob owner's personal session note or other durable preference files. `bob_private_channel` will remain the channel where Bob owner-specific durable preference capture is allowed.
