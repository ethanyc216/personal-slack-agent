# Bob Global Concurrency Design

## Goal

Allow Bob to run up to 5 top-level Codex tasks concurrently across Slack threads while preserving one active top-level task per Slack thread and queueing additional work safely.

## Current Problem

Today Bob routes Slack events through a single watcher loop and then runs `codex exec` synchronously inline. A long-running request blocks intake and processing for other Slack threads. Bob also models a Slack thread as a single session identity, so same-thread follow-ups cannot overlap and are effectively deferred by the blocking runtime rather than through an explicit queue.

## Approved Scope

- Global concurrency limit is configurable and should support `5`.
- Per-thread concurrency remains `1`.
- Additional Slack work for a busy thread is queued, not dropped.
- Codex may still spawn its own internal subagents inside a top-level session.
- Persist the queue so queued work survives Bob restarts.

## Architecture

### Ingress

Slack watcher and reconciliation stay single-threaded. Their job is to detect root messages and thread replies, dedupe them, and enqueue Bob tasks instead of running `codex exec` inline.

### Task Queue

Add a persisted `task_queue` table keyed by an auto-increment task id. Each task stores:

- workspace, channel, thread, and message timestamps
- task kind: `new_root` or `thread_reply`
- actor id
- prompt text
- optional target Codex session id
- lifecycle state: `queued`, `running`, `completed`, `failed`, `canceled`
- timestamps and error text

The queue is FIFO by creation time.

### Dispatcher

Bob owns a worker pool sized by `max_concurrent_tasks`. On each loop:

1. finish completed futures
2. run reminder/auto-close maintenance
3. claim queued tasks in FIFO order
4. dispatch up to the global concurrency limit
5. enforce `max_concurrent_per_thread = 1`

If Bob restarts while tasks are marked `running`, it requeues them on startup because Bob already runs as a single locked process.

### Execution

Workers run the existing blocking Codex subprocess path. For roots they create a new Codex session; for replies they resume the existing session. Existing session state and Slack delivery behavior remain the same as much as possible.

### Slack UX

- When a task actually starts, Bob posts the normal working status.
- When a reply arrives for a busy thread, Bob posts a queued notice.
- When the queued task later starts, it posts the normal working status.

## Config

Add new default settings:

- `max_concurrent_tasks`
- `max_concurrent_per_thread`

Default values should preserve current behavior:

- `max_concurrent_tasks = 1`
- `max_concurrent_per_thread = 1`

## Testing

Required coverage:

- queue persistence and claim/complete transitions
- requeue of interrupted `running` tasks on restart
- 5 tasks may run concurrently across 5 different threads
- a 6th task remains queued
- same-thread tasks do not overlap
- queued same-thread follow-up runs after the earlier task finishes
- config parsing and defaults for the new knobs

## Risks

- Worker-thread Slack posting relies on the current Slack API path staying mostly HTTP-backed after watcher initialization.
- Restart recovery requeues interrupted running tasks, so a restarted Bob may repeat a partially completed top-level task.
- Same-thread queueing is top-level only; it does not attempt in-thread task routing beyond FIFO.
