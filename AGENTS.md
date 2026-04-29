# Personal Slack Agent Notes

These instructions apply to the `personal_slack_agent` repository.

## Bob Runtime

- Treat changes to `defaults.bob_codex_home` in Bob config as a data migration, not just a path edit.
- Before restarting Bob onto a new `bob_codex_home`, migrate the dynamic Codex state from the old home into the new one.
- The dynamic state includes at least:
  - `sessions/`
  - `history.jsonl`
  - `logs_2.sqlite*`
  - `shell_snapshots/`
  - `log/`
  - `state_5.sqlite*`
  - `.tmp/`
  - `tmp/`
- After migration, verify the new home contains those runtime files and directories before deleting the old home.

## Recurring Failure Pattern

- If a fresh Bob session reports errors like:
  - `shell commands fail before launch`
  - `sandbox-exec: sandbox_apply: Operation not permitted`
  - `browser actions beyond listing existing tabs are being cancelled`
  right after a `bob_codex_home` move or Bob restart, suspect a broken or incomplete Bob Codex-home migration first.
- In that situation:
  1. Compare the new Bob Codex home against the previous one.
  2. Ensure the dynamic runtime state was copied over, not just the symlinked/static files.
  3. Restart Bob only after the migration is complete.

- Distinguish global Bob/runtime breakage from a bad single Codex session bootstrap:
  - If one Bob session fails on its very first trivial `exec_command` calls (for example `cat ~/.codex/...` or `/bin/echo test`) with `sandbox-exec: sandbox_apply: Operation not permitted`,
  - but another fresh Bob smoke session on the same running Bob process can execute shell commands successfully,
  - then treat the failed session as a bad per-session Codex bootstrap rather than evidence that Bob is globally broken.
- In that case, prefer discarding the bad session and retrying in a fresh Bob session before changing Bob config or runtime state again.

- Distinguish `bob_codex_home` migration breakage from Bob-daemon sandbox inheritance:
  - If Bob `workspace-write` sessions fail immediately with `sandbox-exec: sandbox_apply: Operation not permitted`,
  - and Bob was started or restarted from inside an already sandboxed Codex session,
  - then suspect that `bob-agent` inherited the parent session's sandbox and is trying to launch nested sandboxed Codex children.
- In that situation:
  1. Restart `bob-agent` from a normal unsandboxed shell or top-level session first.
  2. Retry the same Bob Slack prompt before changing `bob_codex_home`, writable roots, or sandbox mode.
  3. Do not treat `/tmp/.../codex-home` versus `/Users/.../codex-home` as proof of a path-location bug until Bob has been relaunched outside the inherited sandbox.
- A workspace-backed `bob_codex_home` such as `/Users/bob_owner_handle/workspace/personal-slack-agent/codex-home` is valid once `bob-agent` is launched normally.

- Distinguish Jira/browser-path failure from global browser availability:
  - If a Bob session can start normally but Chrome DevTools calls such as `new_page`, `navigate_page`, `evaluate_script`, or `take_snapshot` return `user cancelled MCP tool call`,
  - while the same Chrome DevTools Jira navigation works from a normal top-level Codex session,
  - then treat the problem as Bob-session-specific browser/MCP access failure, not proof that the shared browser session or Jira site is globally unavailable.
- In that case, debug the Bob session/tooling path separately from the browser login state.

- Distinguish prompt-argv cancellation from Bob daemon or sandbox failures:
  - If Bob records `codex exec failed with exit code -9` or a startup-failed session before any real Codex session id exists,
  - and the Slack prompt contains a token that also makes a trivial local command fail when the token is passed in argv,
  - then suspect local command/argv cancellation rather than a broken Bob restart or Codex-home migration.
- Bob should pass Slack prompt text to `codex exec` through stdin using prompt argument `-`; do not put arbitrary Slack user text directly in the `codex exec` argv.

## Bob Control

- `bobctl restart` is cooperative: it writes a stop request and may race if the old agent has not exited yet.
- If `restart` reports that Bob is still running on the same pid, check `bobctl status`, wait for at least one poll tick, and only then retry.
- If the old pid remains stuck and the user wants Bob restarted immediately, stop the stale process explicitly, then start Bob again.

## Bob Doctor

- `bobctl doctor` now checks both the browser path and a lightweight Bob-style child Codex execution probe.
- Interpret `terminal_codex_exec` as a child-session sanity check, not just a static config check.
- Run `bobctl doctor` from a normal unsandboxed shell for operator truth.
- If `bobctl doctor` is run from inside an already sandboxed Codex session, `terminal_codex_exec` may fail with nested-sandbox errors even while Bob itself is healthy.

## Bob Browser Usage

- Normal Bob operation should no longer require a persistent visible `https://bob-company.enterprise.slack.example/api/api.test` helper tab for Slack API calls.
- Bob still needs a real Slack workspace tab such as `https://app.slack.com/client/...` for websocket-driven detection, channel-id discovery, and browser-auth bootstrap.
- If the required Slack workspace tab is missing, Bob now prefers non-focusing Chromium target creation instead of foreground `new_page()` tab creation, but real focus behavior is still browser-dependent and should be validated from the live operator shell when it matters.

## Verification

- After Bob runtime changes, verify all of the following:
  - `bobctl status`
  - latest startup lines in `~/.local/share/personal-slack-agent/logs/bob.log`
  - `~/.local/share/personal-slack-agent/bob.pid`
  - presence of expected runtime files under the configured `bob_codex_home`
