# Customizable Callsigns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable Slack callsigns and make Bob reply using the exact alias typed by the user.

**Architecture:** Add a focused callsign parser module, extend defaults config and session state with assistant names/current alias, then route orchestrator and watcher matching through the parser. Keep database migration additive and preserve default `Bob` behavior.

**Tech Stack:** Python dataclasses, TOML config parsing, SQLite migrations, pytest.

---

### Task 1: Config And Parser

**Files:**
- Create: `src/personal_slack_agent/callsign.py`
- Modify: `src/personal_slack_agent/models.py`
- Modify: `src/personal_slack_agent/config.py`
- Test: `tests/test_config.py`
- Test: `tests/test_callsign.py`

- [x] Write failing parser tests for exact alias capture, case-insensitive matching, prefix stripping, close commands, and `bobcat` non-match.
- [x] Write failing config tests for default `["Bob"]`, configured aliases, duplicate rejection, and empty-list rejection.
- [x] Add `DEFAULT_ASSISTANT_NAMES` and `assistant_names` to `DefaultSettings`.
- [x] Parse and dump `assistant_names`.
- [x] Implement `callsign.py` helpers.
- [x] Run parser/config tests.

### Task 2: Persist Session Alias

**Files:**
- Modify: `src/personal_slack_agent/models.py`
- Modify: `src/personal_slack_agent/state.py`
- Test: `tests/test_state.py`

- [x] Write a failing state test proving `assistant_name` is stored and migrated.
- [x] Add `assistant_name` to `SessionRecord`.
- [x] Add a nullable-safe SQLite migration for `sessions.assistant_name`.
- [x] Add `assistant_name` to `upsert_session` and row mapping.
- [x] Add `update_assistant_name`.
- [x] Run state tests.

### Task 3: Orchestrator Reply Identity

**Files:**
- Modify: `src/personal_slack_agent/orchestrator.py`
- Test: `tests/test_orchestrator.py`

- [x] Write failing orchestrator tests for alias-triggered root work, exact-casing labels, boundary non-match, alias close commands, and prompt identity.
- [x] Replace hardcoded root matching, prefix stripping, close parsing, labels, and prompt identity with callsign helpers.
- [x] Store explicit aliases on session start and update the session alias on explicit thread replies.
- [x] Pass the selected alias into result processing and lifecycle messages.
- [x] Run orchestrator tests.

### Task 4: Ultimate Mode And Docs

**Files:**
- Modify: `src/personal_slack_agent/slack/watcher.py`
- Modify: `README.md`
- Modify: `docs/bob-config-setup.md`
- Modify: `config/bob.sample.toml`
- Modify: `src/personal_slack_agent/cli/wrapper.py`
- Test: `tests/test_orchestrator.py`
- Test: existing watcher tests if present
- Test: `tests/test_cli_bootstrap.py`

- [x] Write failing ultimate-mode coverage for non-`bob` aliases and duplicate-safe routing.
- [x] Search configured callsigns in ultimate mode and route only parser-confirmed invocations.
- [x] Make the terminal wrapper use the first configured callsign when prefixing a prompt.
- [x] Document `assistant_names`, examples, and terminal command distinction.
- [x] Run focused tests and full test suite.
