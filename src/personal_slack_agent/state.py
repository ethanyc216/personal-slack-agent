from contextlib import contextmanager
import sqlite3
import time
from pathlib import Path
from typing import Iterator, List, Optional, Union

from .models import (
    DEFAULT_ASSISTANT_NAMES,
    OutboundIntentRecord,
    SessionRecord,
    SessionStatus,
    TaskRecord,
    TaskStatus,
)


class BobStateStore:
    _OUTBOUND_DELIVERY_PENDING = "pending"
    _OUTBOUND_DELIVERY_ATTEMPTED = "attempted"
    _OUTBOUND_DELIVERY_DELIVERED = "delivered"

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    workspace_name TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    root_ts TEXT NOT NULL,
                    codex_session_id TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owner_actor_id TEXT NOT NULL,
                    assistant_name TEXT NOT NULL DEFAULT 'Bob',
                    waiting_message_ts TEXT,
                    approval_request_id TEXT,
                    approval_command_summary TEXT,
                    reminder_count INTEGER NOT NULL DEFAULT 0,
                    reminder_due_at INTEGER,
                    auto_close_due_at INTEGER,
                    last_summary TEXT,
                    last_error TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (workspace_name, channel_name, thread_ts)
                );

                CREATE TABLE IF NOT EXISTS processed_messages (
                    workspace_name TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    message_ts TEXT NOT NULL,
                    author_actor_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    processed_at INTEGER NOT NULL,
                    PRIMARY KEY (workspace_name, channel_name, thread_ts, message_ts, purpose)
                );

                CREATE TABLE IF NOT EXISTS outbound_intents (
                    workspace_name TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    intent_key TEXT NOT NULL,
                    action TEXT NOT NULL,
                    text TEXT NOT NULL,
                    delivery_state TEXT NOT NULL DEFAULT 'pending',
                    delivered INTEGER NOT NULL DEFAULT 0,
                    message_ts TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (workspace_name, channel_name, thread_ts, intent_key)
                );

                CREATE TABLE IF NOT EXISTS channel_cursors (
                    workspace_name TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    latest_message_ts TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (workspace_name, channel_name)
                );

                CREATE TABLE IF NOT EXISTS thread_cursors (
                    workspace_name TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    latest_message_ts TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (workspace_name, channel_name, thread_ts)
                );

                CREATE TABLE IF NOT EXISTS task_queue (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_name TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    message_ts TEXT NOT NULL,
                    author_actor_id TEXT NOT NULL,
                    task_kind TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    codex_session_id TEXT,
                    status TEXT NOT NULL,
                    error_text TEXT,
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    finished_at INTEGER,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS watcher_leases (
                    scope TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            self._migrate_sessions_table(connection)
            self._migrate_outbound_intents_table(connection)

    def upsert_session(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        root_ts: str,
        codex_session_id: str,
        cwd: str,
        owner_actor_id: str,
        status: SessionStatus,
        assistant_name: str = DEFAULT_ASSISTANT_NAMES[0],
        waiting_message_ts: Optional[str] = None,
        approval_request_id: Optional[str] = None,
        approval_command_summary: Optional[str] = None,
        reminder_count: int = 0,
        reminder_due_at: Optional[int] = None,
        auto_close_due_at: Optional[int] = None,
        last_summary: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        now = int(time.time())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO sessions (
                    workspace_name,
                    channel_name,
                    thread_ts,
                    root_ts,
                    codex_session_id,
                    cwd,
                    status,
                    owner_actor_id,
                    assistant_name,
                    waiting_message_ts,
                    approval_request_id,
                    approval_command_summary,
                    reminder_count,
                    reminder_due_at,
                    auto_close_due_at,
                    last_summary,
                    last_error,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (workspace_name, channel_name, thread_ts)
                DO UPDATE SET
                    root_ts = excluded.root_ts,
                    status = excluded.status,
                    assistant_name = excluded.assistant_name,
                    waiting_message_ts = excluded.waiting_message_ts,
                    approval_request_id = excluded.approval_request_id,
                    approval_command_summary = excluded.approval_command_summary,
                    reminder_count = excluded.reminder_count,
                    reminder_due_at = excluded.reminder_due_at,
                    auto_close_due_at = excluded.auto_close_due_at,
                    last_summary = excluded.last_summary,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                WHERE sessions.codex_session_id = excluded.codex_session_id
                  AND sessions.cwd = excluded.cwd
                  AND sessions.owner_actor_id = excluded.owner_actor_id
                """,
                (
                    workspace_name,
                    channel_name,
                    thread_ts,
                    root_ts,
                    codex_session_id,
                    cwd,
                    status.value,
                    owner_actor_id,
                    assistant_name,
                    waiting_message_ts,
                    approval_request_id,
                    approval_command_summary,
                    reminder_count,
                    reminder_due_at,
                    auto_close_due_at,
                    last_summary,
                    last_error,
                    now,
                    now,
                ),
            )
            if cursor.rowcount == 0:
                existing = connection.execute(
                    """
                    SELECT codex_session_id, cwd, owner_actor_id
                    FROM sessions
                    WHERE workspace_name = ?
                      AND channel_name = ?
                      AND thread_ts = ?
                    """,
                    (workspace_name, channel_name, thread_ts),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["codex_session_id"] != codex_session_id
                        or existing["cwd"] != cwd
                        or existing["owner_actor_id"] != owner_actor_id
                    ):
                        raise ValueError(
                            "Refusing to rebind existing thread to a different session identity"
                        )

    def get_by_thread(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> Optional[SessionRecord]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM sessions
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                """,
                (workspace_name, channel_name, thread_ts),
            ).fetchone()
        if row is None:
            return None
        return self._session_record_from_row(row)

    def list_sessions(
        self,
        workspace_name: str,
        channel_name: str,
    ) -> List[SessionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM sessions
                WHERE workspace_name = ?
                  AND channel_name = ?
                ORDER BY created_at ASC
                """,
                (workspace_name, channel_name),
            ).fetchall()
        return [self._session_record_from_row(row) for row in rows]

    def delete_session(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM sessions
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                """,
                (workspace_name, channel_name, thread_ts),
            )

    def update_assistant_name(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        assistant_name: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET assistant_name = ?,
                    updated_at = ?
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                """,
                (
                    assistant_name,
                    int(time.time()),
                    workspace_name,
                    channel_name,
                    thread_ts,
                ),
            )

    def enqueue_task(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        author_actor_id: str,
        task_kind: str,
        prompt_text: str,
        codex_session_id: Optional[str] = None,
    ) -> int:
        now = int(time.time())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO task_queue (
                    workspace_name,
                    channel_name,
                    thread_ts,
                    message_ts,
                    author_actor_id,
                    task_kind,
                    prompt_text,
                    codex_session_id,
                    status,
                    error_text,
                    created_at,
                    started_at,
                    finished_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?)
                """,
                (
                    workspace_name,
                    channel_name,
                    thread_ts,
                    message_ts,
                    author_actor_id,
                    task_kind,
                    prompt_text,
                    codex_session_id,
                    TaskStatus.QUEUED.value,
                    now,
                    now,
                ),
            )
        return int(cursor.lastrowid)

    def get_task(self, task_id: int) -> Optional[TaskRecord]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM task_queue
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._task_record_from_row(row)

    def get_latest_task_for_thread(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        task_kind: Optional[str] = None,
    ) -> Optional[TaskRecord]:
        with self._connect() as connection:
            if task_kind is None:
                row = connection.execute(
                    """
                    SELECT *
                    FROM task_queue
                    WHERE workspace_name = ?
                      AND channel_name = ?
                      AND thread_ts = ?
                    ORDER BY created_at DESC, task_id DESC
                    LIMIT 1
                    """,
                    (workspace_name, channel_name, thread_ts),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT *
                    FROM task_queue
                    WHERE workspace_name = ?
                      AND channel_name = ?
                      AND thread_ts = ?
                      AND task_kind = ?
                    ORDER BY created_at DESC, task_id DESC
                    LIMIT 1
                    """,
                    (workspace_name, channel_name, thread_ts, task_kind),
                ).fetchone()
        if row is None:
            return None
        return self._task_record_from_row(row)

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
    ) -> List[TaskRecord]:
        with self._connect() as connection:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM task_queue
                    ORDER BY created_at ASC, task_id ASC
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM task_queue
                    WHERE status = ?
                    ORDER BY created_at ASC, task_id ASC
                    """,
                    (status.value,),
                ).fetchall()
        return [self._task_record_from_row(row) for row in rows]

    def count_incomplete_tasks_for_thread(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM task_queue
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                  AND status IN (?, ?)
                """,
                (
                    workspace_name,
                    channel_name,
                    thread_ts,
                    TaskStatus.QUEUED.value,
                    TaskStatus.RUNNING.value,
                ),
            ).fetchone()
        return int(row[0] if row is not None else 0)

    def claim_task(self, task_id: int) -> Optional[TaskRecord]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            cursor = connection.execute(
                """
                UPDATE task_queue
                SET status = ?,
                    started_at = ?,
                    updated_at = ?
                WHERE task_id = ?
                  AND status = ?
                """,
                (
                    TaskStatus.RUNNING.value,
                    now,
                    now,
                    task_id,
                    TaskStatus.QUEUED.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                """
                SELECT *
                FROM task_queue
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return self._task_record_from_row(row)

    def mark_task_completed(self, task_id: int) -> None:
        self._mark_task_finished(task_id=task_id, status=TaskStatus.COMPLETED, error_text=None)

    def mark_task_failed(self, task_id: int, error_text: str) -> None:
        self._mark_task_finished(task_id=task_id, status=TaskStatus.FAILED, error_text=error_text)

    def requeue_running_tasks(self) -> int:
        with self._connect() as connection:
            now = int(time.time())
            cursor = connection.execute(
                """
                UPDATE task_queue
                SET status = ?,
                    error_text = NULL,
                    started_at = NULL,
                    finished_at = NULL,
                    updated_at = ?
                WHERE status = ?
                """,
                (
                    TaskStatus.QUEUED.value,
                    now,
                    TaskStatus.RUNNING.value,
                ),
            )
        return int(cursor.rowcount)

    def list_due_reminders(self, now_epoch: int) -> List[SessionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM sessions
                WHERE reminder_due_at IS NOT NULL
                  AND reminder_due_at <= ?
                  AND status IN (?, ?)
                ORDER BY reminder_due_at ASC
                """,
                (
                    now_epoch,
                    SessionStatus.WAITING_FOR_INPUT.value,
                    SessionStatus.WAITING_FOR_APPROVAL.value,
                ),
            ).fetchall()
        return [self._session_record_from_row(row) for row in rows]

    def update_status(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        status: SessionStatus,
        clear_waiting_fields: bool = True,
        last_error: Optional[str] = None,
    ) -> None:
        with self._connect() as connection:
            now = int(time.time())
            if clear_waiting_fields:
                connection.execute(
                    """
                    UPDATE sessions
                    SET status = ?,
                        waiting_message_ts = NULL,
                        approval_request_id = NULL,
                        approval_command_summary = NULL,
                        reminder_due_at = NULL,
                        auto_close_due_at = NULL,
                        last_error = ?,
                        updated_at = ?
                    WHERE workspace_name = ?
                      AND channel_name = ?
                      AND thread_ts = ?
                    """,
                    (status.value, last_error, now, workspace_name, channel_name, thread_ts),
                )
            else:
                connection.execute(
                    """
                    UPDATE sessions
                    SET status = ?, last_error = ?, updated_at = ?
                    WHERE workspace_name = ?
                      AND channel_name = ?
                      AND thread_ts = ?
                    """,
                    (status.value, last_error, now, workspace_name, channel_name, thread_ts),
                )

    def set_waiting_state(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        status: SessionStatus,
        waiting_message_ts: Optional[str],
        approval_request_id: Optional[str],
        approval_command_summary: Optional[str],
        reminder_due_at: Optional[int],
        auto_close_due_at: Optional[int],
    ) -> None:
        if status not in (
            SessionStatus.WAITING_FOR_INPUT,
            SessionStatus.WAITING_FOR_APPROVAL,
        ):
            raise ValueError("set_waiting_state only accepts waiting statuses")
        if status is SessionStatus.WAITING_FOR_APPROVAL and not approval_request_id:
            raise ValueError(
                "approval_request_id is required for WAITING_FOR_APPROVAL status"
            )
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET status = ?,
                    waiting_message_ts = ?,
                    approval_request_id = ?,
                    approval_command_summary = ?,
                    reminder_due_at = ?,
                    auto_close_due_at = ?,
                    updated_at = ?
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                """,
                (
                    status.value,
                    waiting_message_ts,
                    approval_request_id
                    if status is SessionStatus.WAITING_FOR_APPROVAL
                    else None,
                    approval_command_summary
                    if status is SessionStatus.WAITING_FOR_APPROVAL
                    else None,
                    reminder_due_at,
                    auto_close_due_at,
                    now,
                    workspace_name,
                    channel_name,
                    thread_ts,
                ),
            )

    def list_due_auto_closes(self, now_epoch: int) -> List[SessionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM sessions
                WHERE auto_close_due_at IS NOT NULL
                  AND auto_close_due_at <= ?
                  AND status IN (?, ?)
                ORDER BY auto_close_due_at ASC
                """,
                (
                    now_epoch,
                    SessionStatus.WAITING_FOR_INPUT.value,
                    SessionStatus.WAITING_FOR_APPROVAL.value,
                ),
            ).fetchall()
        return [self._session_record_from_row(row) for row in rows]

    def has_processed_message(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        purpose: str,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM processed_messages
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                  AND message_ts = ?
                  AND purpose = ?
                """,
                (workspace_name, channel_name, thread_ts, message_ts, purpose),
            ).fetchone()
        return row is not None

    def thread_has_processed_purpose(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        purpose: str,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM processed_messages
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                  AND purpose = ?
                LIMIT 1
                """,
                (workspace_name, channel_name, thread_ts, purpose),
            ).fetchone()
        return row is not None

    def record_processed_message(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        author_actor_id: str,
        purpose: str,
    ) -> None:
        self.claim_processed_message(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            message_ts=message_ts,
            author_actor_id=author_actor_id,
            purpose=purpose,
        )

    def claim_processed_message(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        author_actor_id: str,
        purpose: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO processed_messages (
                    workspace_name,
                    channel_name,
                    thread_ts,
                    message_ts,
                    author_actor_id,
                    purpose,
                    processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_name,
                    channel_name,
                    thread_ts,
                    message_ts,
                    author_actor_id,
                    purpose,
                    int(time.time()),
                ),
            )
        return cursor.rowcount == 1

    def try_acquire_watcher_lease(
        self,
        scope: str,
        owner: str,
        now_epoch: int,
        ttl_seconds: int,
    ) -> bool:
        expires_at = int(now_epoch + ttl_seconds)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO watcher_leases (
                    scope,
                    owner,
                    expires_at,
                    updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT (scope)
                DO UPDATE SET
                    owner = excluded.owner,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                WHERE watcher_leases.owner = excluded.owner
                   OR watcher_leases.expires_at <= ?
                """,
                (
                    scope,
                    owner,
                    expires_at,
                    int(now_epoch),
                    int(now_epoch),
                ),
            )
        return cursor.rowcount == 1

    def release_watcher_lease(
        self,
        scope: str,
        owner: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM watcher_leases
                WHERE scope = ?
                  AND owner = ?
                """,
                (scope, owner),
            )
        return cursor.rowcount == 1

    def release_processed_message(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        purpose: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM processed_messages
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                  AND message_ts = ?
                  AND purpose = ?
                """,
                (workspace_name, channel_name, thread_ts, message_ts, purpose),
            )

    def upsert_outbound_intent(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        intent_key: str,
        action: str,
        text: str,
        delivered: bool = False,
        delivery_state: Optional[str] = None,
        message_ts: Optional[str] = None,
    ) -> None:
        now = int(time.time())
        desired_delivery_state = self._coerce_delivery_state(
            delivered=delivered,
            delivery_state=delivery_state,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO outbound_intents (
                    workspace_name,
                    channel_name,
                    thread_ts,
                    intent_key,
                    action,
                    text,
                    delivery_state,
                    delivered,
                    message_ts,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (workspace_name, channel_name, thread_ts, intent_key)
                DO UPDATE SET
                    action = excluded.action,
                    text = excluded.text,
                    delivery_state = CASE
                        WHEN outbound_intents.delivery_state = 'delivered' THEN 'delivered'
                        WHEN outbound_intents.delivery_state = 'attempted'
                             AND excluded.delivery_state = 'pending' THEN 'attempted'
                        ELSE excluded.delivery_state
                    END,
                    delivered = CASE
                        WHEN outbound_intents.delivered = 1 THEN 1
                        WHEN excluded.delivery_state = 'delivered' THEN 1
                        ELSE 0
                    END,
                    message_ts = COALESCE(outbound_intents.message_ts, excluded.message_ts),
                    updated_at = excluded.updated_at
                """,
                (
                    workspace_name,
                    channel_name,
                    thread_ts,
                    intent_key,
                    action,
                    text,
                    desired_delivery_state,
                    int(desired_delivery_state == self._OUTBOUND_DELIVERY_DELIVERED),
                    message_ts,
                    now,
                    now,
                ),
            )

    def list_pending_outbound_intents(self) -> List[OutboundIntentRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM outbound_intents
                WHERE delivery_state IN (?, ?)
                ORDER BY created_at ASC
                """,
                (
                    self._OUTBOUND_DELIVERY_PENDING,
                    self._OUTBOUND_DELIVERY_ATTEMPTED,
                ),
            ).fetchall()
        return [self._outbound_intent_record_from_row(row) for row in rows]

    def list_delivered_outbound_message_timestamps(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> List[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_ts
                FROM outbound_intents
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                  AND delivery_state = 'delivered'
                  AND message_ts IS NOT NULL
                ORDER BY created_at ASC
                """,
                (workspace_name, channel_name, thread_ts),
            ).fetchall()
        return [row["message_ts"] for row in rows]

    def list_outbound_intents_for_thread(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> List[OutboundIntentRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM outbound_intents
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                ORDER BY created_at ASC
                """,
                (workspace_name, channel_name, thread_ts),
            ).fetchall()
        return [self._outbound_intent_record_from_row(row) for row in rows]

    def upsert_channel_cursor(
        self,
        workspace_name: str,
        channel_name: str,
        latest_message_ts: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO channel_cursors (
                    workspace_name,
                    channel_name,
                    latest_message_ts,
                    updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT (workspace_name, channel_name)
                DO UPDATE SET
                    latest_message_ts = excluded.latest_message_ts,
                    updated_at = excluded.updated_at
                WHERE CAST(excluded.latest_message_ts AS REAL)
                    > CAST(channel_cursors.latest_message_ts AS REAL)
                """,
                (
                    workspace_name,
                    channel_name,
                    latest_message_ts,
                    int(time.time()),
                ),
            )

    def get_channel_cursor(
        self,
        workspace_name: str,
        channel_name: str,
    ) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT latest_message_ts
                FROM channel_cursors
                WHERE workspace_name = ?
                  AND channel_name = ?
                """,
                (workspace_name, channel_name),
            ).fetchone()
        if row is None:
            return None
        return row["latest_message_ts"]

    def upsert_thread_cursor(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        latest_message_ts: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO thread_cursors (
                    workspace_name,
                    channel_name,
                    thread_ts,
                    latest_message_ts,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (workspace_name, channel_name, thread_ts)
                DO UPDATE SET
                    latest_message_ts = excluded.latest_message_ts,
                    updated_at = excluded.updated_at
                WHERE CAST(excluded.latest_message_ts AS REAL)
                    > CAST(thread_cursors.latest_message_ts AS REAL)
                """,
                (
                    workspace_name,
                    channel_name,
                    thread_ts,
                    latest_message_ts,
                    int(time.time()),
                ),
            )

    def get_thread_cursor(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> Optional[str]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT latest_message_ts
                FROM thread_cursors
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                """,
                (workspace_name, channel_name, thread_ts),
            ).fetchone()
        if row is None:
            return None
        return row["latest_message_ts"]

    def mark_outbound_intent_attempted(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        intent_key: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE outbound_intents
                SET delivery_state = CASE
                        WHEN delivered = 1 THEN 'delivered'
                        ELSE 'attempted'
                    END,
                    updated_at = ?
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                  AND intent_key = ?
                """,
                (
                    int(time.time()),
                    workspace_name,
                    channel_name,
                    thread_ts,
                    intent_key,
                ),
            )

    def mark_outbound_intent_delivered(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        intent_key: str,
        message_ts: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE outbound_intents
                SET delivered = 1,
                    delivery_state = 'delivered',
                    message_ts = COALESCE(message_ts, ?),
                    updated_at = ?
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                  AND intent_key = ?
                """,
                (
                    message_ts,
                    int(time.time()),
                    workspace_name,
                    channel_name,
                    thread_ts,
                    intent_key,
                ),
            )

    def claim_due_reminders(self, now_epoch: int) -> List[SessionRecord]:
        claimed_rows: List[sqlite3.Row] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT *
                FROM sessions
                WHERE reminder_due_at IS NOT NULL
                  AND reminder_due_at <= ?
                  AND status IN (?, ?)
                ORDER BY reminder_due_at ASC
                """,
                (
                    now_epoch,
                    SessionStatus.WAITING_FOR_INPUT.value,
                    SessionStatus.WAITING_FOR_APPROVAL.value,
                ),
            ).fetchall()
            now = int(time.time())
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE sessions
                    SET reminder_due_at = NULL,
                        updated_at = ?
                    WHERE workspace_name = ?
                      AND channel_name = ?
                      AND thread_ts = ?
                      AND reminder_due_at = ?
                    """,
                    (
                        now,
                        row["workspace_name"],
                        row["channel_name"],
                        row["thread_ts"],
                        row["reminder_due_at"],
                    ),
                )
                if cursor.rowcount == 1:
                    claimed_rows.append(row)
        return [self._session_record_from_row(row) for row in claimed_rows]

    def claim_due_auto_closes(self, now_epoch: int) -> List[SessionRecord]:
        claimed_rows: List[sqlite3.Row] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT *
                FROM sessions
                WHERE auto_close_due_at IS NOT NULL
                  AND auto_close_due_at <= ?
                  AND status IN (?, ?)
                ORDER BY auto_close_due_at ASC
                """,
                (
                    now_epoch,
                    SessionStatus.WAITING_FOR_INPUT.value,
                    SessionStatus.WAITING_FOR_APPROVAL.value,
                ),
            ).fetchall()
            now = int(time.time())
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE sessions
                    SET auto_close_due_at = NULL,
                        updated_at = ?
                    WHERE workspace_name = ?
                      AND channel_name = ?
                      AND thread_ts = ?
                      AND auto_close_due_at = ?
                    """,
                    (
                        now,
                        row["workspace_name"],
                        row["channel_name"],
                        row["thread_ts"],
                        row["auto_close_due_at"],
                    ),
                )
                if cursor.rowcount == 1:
                    claimed_rows.append(row)
        return [self._session_record_from_row(row) for row in claimed_rows]

    def record_waiting_reminder(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        reminder_count: int,
        reminder_due_at: Optional[int],
    ) -> None:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET reminder_count = ?,
                    reminder_due_at = ?,
                    updated_at = ?
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                """,
                (
                    reminder_count,
                    reminder_due_at,
                    now,
                    workspace_name,
                    channel_name,
                    thread_ts,
                ),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _migrate_sessions_table(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "owner_actor_id" not in columns:
            connection.execute(
                "ALTER TABLE sessions ADD COLUMN owner_actor_id TEXT NOT NULL DEFAULT ''"
            )
        if "assistant_name" not in columns:
            connection.execute(
                "ALTER TABLE sessions ADD COLUMN assistant_name TEXT NOT NULL DEFAULT 'Bob'"
            )
        if "approval_request_id" not in columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN approval_request_id TEXT")
        if "approval_command_summary" not in columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN approval_command_summary TEXT")
        if "reminder_due_at" not in columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN reminder_due_at INTEGER")
        if "auto_close_due_at" not in columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN auto_close_due_at INTEGER")

    def _migrate_outbound_intents_table(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(outbound_intents)").fetchall()
        }
        if "delivery_state" not in columns:
            connection.execute(
                "ALTER TABLE outbound_intents ADD COLUMN delivery_state TEXT NOT NULL DEFAULT 'pending'"
            )
        connection.execute(
            """
            UPDATE outbound_intents
            SET delivery_state = CASE
                WHEN delivered = 1 THEN 'delivered'
                ELSE 'pending'
            END
            WHERE delivery_state IS NULL OR delivery_state = ''
            """
        )

    def _session_record_from_row(self, row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            workspace_name=row["workspace_name"],
            channel_name=row["channel_name"],
            thread_ts=row["thread_ts"],
            root_ts=row["root_ts"],
            codex_session_id=row["codex_session_id"],
            cwd=row["cwd"],
            owner_actor_id=row["owner_actor_id"],
            status=SessionStatus(row["status"]),
            assistant_name=row["assistant_name"] or DEFAULT_ASSISTANT_NAMES[0],
            waiting_message_ts=row["waiting_message_ts"],
            approval_request_id=row["approval_request_id"],
            approval_command_summary=row["approval_command_summary"],
            reminder_count=row["reminder_count"],
            reminder_due_at=row["reminder_due_at"],
            auto_close_due_at=row["auto_close_due_at"],
            last_summary=row["last_summary"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _outbound_intent_record_from_row(self, row: sqlite3.Row) -> OutboundIntentRecord:
        return OutboundIntentRecord(
            workspace_name=row["workspace_name"],
            channel_name=row["channel_name"],
            thread_ts=row["thread_ts"],
            intent_key=row["intent_key"],
            action=row["action"],
            text=row["text"],
            delivery_state=row["delivery_state"],
            delivered=bool(row["delivered"]),
            message_ts=row["message_ts"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _task_record_from_row(self, row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            workspace_name=row["workspace_name"],
            channel_name=row["channel_name"],
            thread_ts=row["thread_ts"],
            message_ts=row["message_ts"],
            author_actor_id=row["author_actor_id"],
            task_kind=row["task_kind"],
            prompt_text=row["prompt_text"],
            codex_session_id=row["codex_session_id"],
            status=TaskStatus(row["status"]),
            error_text=row["error_text"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            updated_at=row["updated_at"],
        )

    def _mark_task_finished(
        self,
        task_id: int,
        status: TaskStatus,
        error_text: Optional[str],
    ) -> None:
        now = int(time.time())
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE task_queue
                SET status = ?,
                    error_text = ?,
                    finished_at = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    status.value,
                    error_text,
                    now,
                    now,
                    task_id,
                ),
            )

    def _coerce_delivery_state(
        self,
        delivered: bool,
        delivery_state: Optional[str],
    ) -> str:
        if delivered:
            return self._OUTBOUND_DELIVERY_DELIVERED
        if delivery_state is None:
            return self._OUTBOUND_DELIVERY_PENDING
        normalized = delivery_state.strip().lower()
        if normalized not in (
            self._OUTBOUND_DELIVERY_PENDING,
            self._OUTBOUND_DELIVERY_ATTEMPTED,
            self._OUTBOUND_DELIVERY_DELIVERED,
        ):
            raise ValueError("Invalid outbound intent delivery_state")
        return normalized
