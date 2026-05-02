# Customizable Callsigns Design

## Goal

Allow Bob to be invoked by any configured Slack callsign while preserving the exact alias the user typed in Bob's Slack replies for that interaction.

## Configuration

Add `assistant_names` under `[defaults]`.

```toml
[defaults]
owner_name = "Yang Jiao"
owner_preferred_name = "Yang"
assistant_names = ["Bob", "Bobby", "Copilot"]
```

The field is optional and defaults to `["Bob"]`, so existing configs continue to work unchanged. Values are stripped, case-insensitive duplicates are rejected, empty lists are rejected, and control characters are rejected. The first configured name is the fallback label when no alias can be inferred from a legacy session.

## Invocation Semantics

Root messages and ultimate-mode invocations match any configured callsign case-insensitively with a boundary check. `Bob, run tests`, `bobby run tests`, and `Copilot: summarize` are valid when those names are configured. `bobcat run tests` is not valid for `Bob`.

Manual close commands support both existing shapes for every alias: `<alias> close` and `close <alias>`.

## Reply Identity

Bob's status and lifecycle messages use the exact alias spelling and casing from the triggering Slack message whenever a callsign is present. For example, `bObBy, run tests` yields `_*bObBy is working on it...*_` and `_*bObBy :white_check_mark::*_ ...`.

Sessions store the most recent explicit alias. Thread replies that do not contain a callsign use the stored alias, which keeps waiting prompts, reminders, approvals, resumes, and auto-close messages consistent with the user's last explicit invocation.

## Runtime Prompt

Codex prompt context should no longer hardcode `Bob` as the only Slack name. It should state the current Slack assistant alias and the configured callsigns, and instruct Codex to use the current alias as its name in that Slack-started session.

## Ultimate Mode

Ultimate-mode search must search for all configured callsigns and use the same parser as normal configured channels before routing a message. Message-id dedupe remains the source of truth, so finding the same message through more than one alias search must not enqueue duplicate work.

## Non-Goals

The terminal executable remains `bob`. This change customizes Slack-facing callsigns and reply identity, not the package name, process name, CLI command name, or persisted database filename.
