# Bob Global Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persisted Bob task queue plus configurable global worker concurrency so Bob can run up to 5 top-level Codex sessions concurrently across Slack threads while preserving one active task per thread.

**Architecture:** Keep Slack detection single-threaded, enqueue inbound work into SQLite, and let a dispatcher submit queued tasks to a bounded worker pool. Workers use the existing blocking Codex runner path, while queue state and session state stay in the existing state store.

**Tech Stack:** Python 3, sqlite3, pytest, concurrent.futures

---

### Task 1: Add config coverage for concurrency settings

**Files:**
- Modify: `src/personal_slack_agent/models.py`
- Modify: `src/personal_slack_agent/config.py`
- Modify: `config/bob.sample.toml`
- Test: `tests/test_config.py`

- [ ] Add `max_concurrent_tasks` and `max_concurrent_per_thread` to the default settings model and config parser/dumper.
- [ ] Write config tests covering defaults and explicit parsing.
- [ ] Run the config test slice and verify the new expectations pass.

### Task 2: Add persisted task queue state

**Files:**
- Modify: `src/personal_slack_agent/models.py`
- Modify: `src/personal_slack_agent/state.py`
- Test: `tests/test_state.py`

- [ ] Add a task-record model and task-status constants.
- [ ] Add the `task_queue` table and migration-safe initialization.
- [ ] Add queue helpers for enqueue, list queued, claim, complete, fail, and requeue-running.
- [ ] Write state tests proving FIFO persistence, one-time claim, and restart requeue behavior.
- [ ] Run the state test slice and verify it fails before implementation and passes after.

### Task 3: Move Bob execution from inline calls to queued worker dispatch

**Files:**
- Modify: `src/personal_slack_agent/orchestrator.py`
- Test: `tests/test_orchestrator.py`

- [ ] Split ingress from execution so root messages and thread replies enqueue task records instead of running immediately.
- [ ] Add a bounded worker pool sized by config.
- [ ] Track active tasks in memory by task id and by Slack thread.
- [ ] Dispatch queued tasks in FIFO order while enforcing global and per-thread limits.
- [ ] Preserve existing session lifecycle behavior for new sessions, resume flows, waiting states, approvals, and final output.
- [ ] Add orchestrator tests for 5-global-worker behavior, 1-per-thread serialization, and queued same-thread follow-up execution.

### Task 4: Wire runtime startup and shutdown

**Files:**
- Modify: `src/personal_slack_agent/cli/agent.py`
- Test: `tests/test_agent_runtime.py`

- [ ] Ensure runtime startup uses the latest config values when building the orchestrator.
- [ ] Requeue interrupted running tasks on startup.
- [ ] Shut down the worker pool on agent exit.
- [ ] Add runtime tests that keep existing startup behavior intact.

### Task 5: Verify end-to-end behavior

**Files:**
- Test: `tests/test_config.py`
- Test: `tests/test_state.py`
- Test: `tests/test_orchestrator.py`
- Test: `tests/test_agent_runtime.py`

- [ ] Run the targeted queue/concurrency tests.
- [ ] Run the broader regression suite already used as the baseline for this change.
- [ ] Review the diff for same-thread ordering, restart recovery, and Slack message behavior before declaring completion.
