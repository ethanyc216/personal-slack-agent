import sqlite3

from personal_slack_agent.models import SessionStatus, TaskStatus
from personal_slack_agent.state import BobStateStore
from personal_slack_agent import state as state_module


def test_state_store_persists_thread_mapping_and_session_fields(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/Users/bob_owner_handle/Code/OHAI/ctdm",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_APPROVAL,
        assistant_name="Bobby",
        approval_request_id="APR-001",
        approval_command_summary="git status -sb",
        reminder_due_at=1711846800,
        auto_close_due_at=1711850400,
    )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.codex_session_id == "session-123"
    assert record.owner_actor_id == "U123"
    assert record.assistant_name == "Bobby"
    assert record.status is SessionStatus.WAITING_FOR_APPROVAL
    assert record.approval_request_id == "APR-001"
    assert record.approval_command_summary == "git status -sb"
    assert record.reminder_due_at == 1711846800
    assert record.auto_close_due_at == 1711850400


def test_state_store_updates_session_assistant_name(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.RUNNING,
        assistant_name="Bob",
    )

    store.update_assistant_name(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        assistant_name="bObBy",
    )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.assistant_name == "bObBy"


def test_due_waiting_sessions_are_returned_for_reminder(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        reminder_due_at=1,
        auto_close_due_at=9999999999,
    )

    due = store.list_due_reminders(now_epoch=5)
    assert [item.thread_ts for item in due] == ["1743461000.000001"]


def test_processed_messages_support_inbound_dedupe(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    assert not store.has_processed_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461000.000001",
        purpose="root_ingest",
    )

    store.record_processed_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        purpose="root_ingest",
    )
    store.record_processed_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        purpose="root_ingest",
    )

    assert store.has_processed_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461000.000001",
        purpose="root_ingest",
    )


def test_claim_processed_message_is_atomic(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    claimed = store.claim_processed_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        purpose="root_ingest",
    )
    assert claimed is True

    claimed_again = store.claim_processed_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        purpose="root_ingest",
    )
    assert claimed_again is False


def test_task_queue_round_trip_and_claim(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    first_task_id = store.enqueue_task(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        task_kind="new_root",
        prompt_text="Bob, hi there",
    )
    second_task_id = store.enqueue_task(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461001.000001",
        message_ts="1743461001.000001",
        author_actor_id="U123",
        task_kind="new_root",
        prompt_text="Bob, another task",
    )

    queued = store.list_tasks(status=TaskStatus.QUEUED)
    assert [item.task_id for item in queued] == [first_task_id, second_task_id]

    claimed = store.claim_task(first_task_id)
    assert claimed is not None
    assert claimed.task_id == first_task_id
    assert claimed.status is TaskStatus.RUNNING

    claimed_again = store.claim_task(first_task_id)
    assert claimed_again is None


def test_requeue_running_tasks_moves_running_rows_back_to_queue(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    task_id = store.enqueue_task(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        task_kind="new_root",
        prompt_text="Bob, hi there",
    )
    claimed = store.claim_task(task_id)
    assert claimed is not None
    assert claimed.status is TaskStatus.RUNNING

    assert store.requeue_running_tasks() == 1

    queued = store.list_tasks(status=TaskStatus.QUEUED)
    assert [item.task_id for item in queued] == [task_id]


def test_mark_task_failed_records_error_text(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    task_id = store.enqueue_task(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        task_kind="new_root",
        prompt_text="Bob, hi there",
    )
    assert store.claim_task(task_id) is not None

    store.mark_task_failed(task_id, "codex unavailable")

    failed = store.list_tasks(status=TaskStatus.FAILED)
    assert len(failed) == 1
    assert failed[0].task_id == task_id
    assert failed[0].error_text == "codex unavailable"


def test_outbound_intents_support_retry_and_reconciliation(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    store.upsert_outbound_intent(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        intent_key="intent-1",
        action="post_reply",
        text="Bob needs input",
        delivered=False,
        message_ts=None,
    )
    pending = store.list_pending_outbound_intents()
    assert len(pending) == 1
    assert pending[0].intent_key == "intent-1"
    assert pending[0].delivered is False
    assert pending[0].delivery_state == "pending"

    store.mark_outbound_intent_attempted(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        intent_key="intent-1",
    )
    pending_after_attempt = store.list_pending_outbound_intents()
    assert len(pending_after_attempt) == 1
    assert pending_after_attempt[0].delivery_state == "attempted"

    store.mark_outbound_intent_delivered(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        intent_key="intent-1",
        message_ts="1743461005.000001",
    )
    pending_after = store.list_pending_outbound_intents()
    assert pending_after == []


def test_initialize_migrates_legacy_sessions_table(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    connection = sqlite3.connect(str(db_path))
    connection.execute(
        """
        CREATE TABLE sessions (
            workspace_name TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            thread_ts TEXT NOT NULL,
            root_ts TEXT NOT NULL,
            codex_session_id TEXT NOT NULL,
            cwd TEXT NOT NULL,
            status TEXT NOT NULL,
            waiting_message_ts TEXT,
            reminder_count INTEGER NOT NULL DEFAULT 0,
            last_summary TEXT,
            last_error TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (workspace_name, channel_name, thread_ts)
        )
        """
    )
    connection.commit()
    connection.close()

    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        reminder_due_at=1,
    )

    due = store.list_due_reminders(now_epoch=5)
    assert [item.thread_ts for item in due] == ["1743461000.000001"]


def test_due_waiting_sessions_are_returned_for_auto_close(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        auto_close_due_at=1,
    )
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000002",
        root_ts="1743461000.000002",
        codex_session_id="session-456",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.RUNNING,
        auto_close_due_at=1,
    )

    due = store.list_due_auto_closes(now_epoch=5)
    assert [item.thread_ts for item in due] == ["1743461000.000001"]


def test_claim_due_reminders_returns_and_clears_due_rows(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        reminder_due_at=1,
    )

    claimed = store.claim_due_reminders(now_epoch=5)
    assert [item.thread_ts for item in claimed] == ["1743461000.000001"]

    claimed_again = store.claim_due_reminders(now_epoch=5)
    assert claimed_again == []

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.reminder_due_at is None


def test_claim_due_auto_closes_returns_and_clears_due_rows(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        auto_close_due_at=1,
    )

    claimed = store.claim_due_auto_closes(now_epoch=5)
    assert [item.thread_ts for item in claimed] == ["1743461000.000001"]

    claimed_again = store.claim_due_auto_closes(now_epoch=5)
    assert claimed_again == []

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.auto_close_due_at is None


def test_outbound_intent_reupsert_does_not_reopen_delivered_intent(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_outbound_intent(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        intent_key="intent-1",
        action="post_reply",
        text="Bob needs input",
    )
    store.mark_outbound_intent_delivered(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        intent_key="intent-1",
        message_ts="1743461005.000001",
    )

    store.upsert_outbound_intent(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        intent_key="intent-1",
        action="post_reply",
        text="Bob needs input",
    )
    pending_after = store.list_pending_outbound_intents()
    assert pending_after == []


def test_mark_outbound_intent_delivered_keeps_first_message_ts(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_outbound_intent(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        intent_key="intent-1",
        action="post_reply",
        text="Bob needs input",
    )
    store.mark_outbound_intent_delivered(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        intent_key="intent-1",
        message_ts="1743461005.000001",
    )
    store.mark_outbound_intent_delivered(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        intent_key="intent-1",
        message_ts="1743461006.000001",
    )

    connection = sqlite3.connect(str(db_path))
    row = connection.execute(
        """
        SELECT delivered, message_ts
        FROM outbound_intents
        WHERE workspace_name = ?
          AND channel_name = ?
          AND thread_ts = ?
          AND intent_key = ?
        """,
        ("bob_company", "bob_private_channel", "1743461000.000001", "intent-1"),
    ).fetchone()
    connection.close()

    assert row == (1, "1743461005.000001")


def test_update_status_clears_waiting_metadata_by_default(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_APPROVAL,
        approval_request_id="APR-001",
        approval_command_summary="git status -sb",
        reminder_due_at=1,
        auto_close_due_at=10,
    )

    store.update_status(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        status=SessionStatus.RUNNING,
    )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.RUNNING
    assert record.approval_request_id is None
    assert record.approval_command_summary is None
    assert record.reminder_due_at is None
    assert record.auto_close_due_at is None


def test_list_delivered_outbound_message_timestamps_returns_only_delivered_entries(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_outbound_intent(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1774999116.837699",
        intent_key="start",
        action="post_thread_reply",
        text="Bob is working on it",
    )
    store.mark_outbound_intent_delivered(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1774999116.837699",
        intent_key="start",
        message_ts="1775022338.395209",
    )
    store.upsert_outbound_intent(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1774999116.837699",
        intent_key="pending",
        action="post_thread_reply",
        text="Pending",
    )

    timestamps = store.list_delivered_outbound_message_timestamps(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1774999116.837699",
    )

    assert timestamps == ["1775022338.395209"]


def test_upsert_session_rejects_rebinding_existing_thread(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project-a",
        owner_actor_id="U123",
        status=SessionStatus.RUNNING,
    )

    try:
        store.upsert_session(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1743461000.000001",
            root_ts="1743461000.000001",
            codex_session_id="session-456",
            cwd="/tmp/project-b",
            owner_actor_id="U456",
            status=SessionStatus.WAITING_FOR_INPUT,
            reminder_due_at=1,
        )
        assert False, "Expected ValueError when rebinding existing thread identity"
    except ValueError:
        pass

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.codex_session_id == "session-123"
    assert record.cwd == "/tmp/project-a"
    assert record.owner_actor_id == "U123"
    assert record.status is SessionStatus.RUNNING


def test_delete_session_removes_existing_thread_mapping(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project-a",
        owner_actor_id="U123",
        status=SessionStatus.RUNNING,
    )

    store.delete_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
    )

    assert store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001") is None


def test_set_waiting_state_updates_wait_fields_safely(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.RUNNING,
    )

    store.set_waiting_state(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        status=SessionStatus.WAITING_FOR_APPROVAL,
        waiting_message_ts="1743461002.000001",
        approval_request_id="APR-001",
        approval_command_summary="git status -sb",
        reminder_due_at=10,
        auto_close_due_at=20,
    )
    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.WAITING_FOR_APPROVAL
    assert record.waiting_message_ts == "1743461002.000001"
    assert record.approval_request_id == "APR-001"
    assert record.approval_command_summary == "git status -sb"
    assert record.reminder_due_at == 10
    assert record.auto_close_due_at == 20

    try:
        store.set_waiting_state(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1743461000.000001",
            status=SessionStatus.RUNNING,
            waiting_message_ts=None,
            approval_request_id=None,
            approval_command_summary=None,
            reminder_due_at=None,
            auto_close_due_at=None,
        )
        assert False, "Expected ValueError for non-waiting status"
    except ValueError:
        pass


def test_channel_cursor_round_trip(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    store.upsert_channel_cursor("workspace", "channel", "1775029000.698249")

    assert store.get_channel_cursor("workspace", "channel") == "1775029000.698249"


def test_channel_cursor_does_not_move_backward(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    store.upsert_channel_cursor("workspace", "channel", "1775029000.698249")
    store.upsert_channel_cursor("workspace", "channel", "1775028999.000001")

    assert store.get_channel_cursor("workspace", "channel") == "1775029000.698249"


def test_thread_cursor_round_trip(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    store.upsert_thread_cursor(
        "workspace", "channel", "thread-1", "1775029017.231629"
    )

    assert (
        store.get_thread_cursor("workspace", "channel", "thread-1")
        == "1775029017.231629"
    )


def test_thread_cursor_does_not_move_backward(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    store.upsert_thread_cursor(
        "workspace", "channel", "thread-1", "1775029017.231629"
    )
    store.upsert_thread_cursor(
        "workspace", "channel", "thread-1", "1775029000.000001"
    )

    assert (
        store.get_thread_cursor("workspace", "channel", "thread-1")
        == "1775029017.231629"
    )


def test_watcher_lease_blocks_other_owner_until_expired(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    assert store.try_acquire_watcher_lease(
        scope="channel:bob_company:C123",
        owner="configured",
        now_epoch=100,
        ttl_seconds=30,
    )
    assert not store.try_acquire_watcher_lease(
        scope="channel:bob_company:C123",
        owner="runtime",
        now_epoch=120,
        ttl_seconds=30,
    )
    assert store.try_acquire_watcher_lease(
        scope="channel:bob_company:C123",
        owner="runtime",
        now_epoch=131,
        ttl_seconds=30,
    )


def test_watcher_lease_owner_can_refresh_and_release(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()

    assert store.try_acquire_watcher_lease(
        scope="channel:bob_company:C123",
        owner="configured",
        now_epoch=100,
        ttl_seconds=30,
    )
    assert store.try_acquire_watcher_lease(
        scope="channel:bob_company:C123",
        owner="configured",
        now_epoch=120,
        ttl_seconds=30,
    )
    assert not store.release_watcher_lease(
        scope="channel:bob_company:C123",
        owner="runtime",
    )
    assert store.release_watcher_lease(
        scope="channel:bob_company:C123",
        owner="configured",
    )
    assert store.try_acquire_watcher_lease(
        scope="channel:bob_company:C123",
        owner="runtime",
        now_epoch=121,
        ttl_seconds=30,
    )


def test_initialize_legacy_db_still_supports_cursor_persistence(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    connection = sqlite3.connect(str(db_path))
    connection.execute(
        """
        CREATE TABLE sessions (
            workspace_name TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            thread_ts TEXT NOT NULL,
            root_ts TEXT NOT NULL,
            codex_session_id TEXT NOT NULL,
            cwd TEXT NOT NULL,
            status TEXT NOT NULL,
            waiting_message_ts TEXT,
            reminder_count INTEGER NOT NULL DEFAULT 0,
            last_summary TEXT,
            last_error TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (workspace_name, channel_name, thread_ts)
        )
        """
    )
    connection.commit()
    connection.close()

    store = BobStateStore(db_path)
    store.initialize()

    store.upsert_channel_cursor("workspace", "channel", "1775029000.698249")
    store.upsert_thread_cursor(
        "workspace", "channel", "thread-1", "1775029017.231629"
    )

    assert store.get_channel_cursor("workspace", "channel") == "1775029000.698249"
    assert (
        store.get_thread_cursor("workspace", "channel", "thread-1")
        == "1775029017.231629"
    )


def test_state_store_closes_sqlite_connections_after_each_operation(tmp_path, monkeypatch):
    db_path = tmp_path / "bob.sqlite3"
    opened_connections = []
    closed_connections = []
    original_connect = sqlite3.connect

    class TrackingConnection(sqlite3.Connection):
        def close(self):
            closed_connections.append(self)
            return super().close()

    def tracking_connect(*args, **kwargs):
        kwargs.setdefault("factory", TrackingConnection)
        connection = original_connect(*args, **kwargs)
        opened_connections.append(connection)
        return connection

    monkeypatch.setattr(state_module.sqlite3, "connect", tracking_connect)

    store = BobStateStore(db_path)
    store.initialize()
    store.upsert_channel_cursor("workspace", "channel", "1775029000.698249")
    assert store.get_channel_cursor("workspace", "channel") == "1775029000.698249"

    assert opened_connections
    assert closed_connections == opened_connections
