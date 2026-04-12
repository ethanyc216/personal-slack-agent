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

## Bob Control

- `bobctl restart` is cooperative: it writes a stop request and may race if the old agent has not exited yet.
- If `restart` reports that Bob is still running on the same pid, check `bobctl status`, wait for at least one poll tick, and only then retry.
- If the old pid remains stuck and the user wants Bob restarted immediately, stop the stale process explicitly, then start Bob again.

## Verification

- After Bob runtime changes, verify all of the following:
  - `bobctl status`
  - latest startup lines in `~/.local/share/personal-slack-agent/logs/bob.log`
  - `~/.local/share/personal-slack-agent/bob.pid`
  - presence of expected runtime files under the configured `bob_codex_home`
