from personal_slack_agent.models import (
    AppConfig,
    ChannelConfig,
    DefaultSettings,
    SessionStatus,
    WorkspaceConfig,
)
from personal_slack_agent.slack import SlackRootMessage, SlackThreadReplyMessage
from personal_slack_agent.state import BobStateStore


class RecordingOrchestrator:
    def __init__(self) -> None:
        self.root_calls = []
        self.reply_calls = []

    def handle_new_root_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
        author_actor_id: str,
        text: str,
    ) -> None:
        self.root_calls.append(
            {
                "workspace_name": workspace_name,
                "channel_name": channel_name,
                "message_ts": message_ts,
                "author_actor_id": author_actor_id,
                "text": text,
            }
        )

    def handle_thread_reply(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        author_actor_id: str,
        text: str,
    ) -> None:
        self.reply_calls.append(
            {
                "workspace_name": workspace_name,
                "channel_name": channel_name,
                "thread_ts": thread_ts,
                "message_ts": message_ts,
                "author_actor_id": author_actor_id,
                "text": text,
            }
        )


class FakeBrowser:
    def __init__(self) -> None:
        self.channel_ids = {}
        self.root_messages = {}
        self.thread_replies = {}
        self.frame_handlers = {}
        self.disconnect_handlers = {}

    def get_channel_id(self, workspace_name: str, channel_name: str) -> str:
        return self.channel_ids[(workspace_name, channel_name)]

    def subscribe_to_realtime_frames(self, workspace_name: str, on_frame, on_disconnect) -> None:
        self.frame_handlers[workspace_name] = on_frame
        self.disconnect_handlers[workspace_name] = on_disconnect

    def list_root_messages(
        self,
        workspace_name: str,
        channel_name: str,
        oldest: str = None,
        latest: str = None,
        limit: int = 50,
    ):
        messages = list(self.root_messages.get((workspace_name, channel_name), []))
        if oldest is not None:
            messages = [message for message in messages if float(message.message_ts) > float(oldest)]
        if latest is not None:
            messages = [message for message in messages if float(message.message_ts) < float(latest)]
        return messages[-limit:]

    def list_thread_replies(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        oldest: str = None,
        limit: int = 200,
    ):
        replies = list(self.thread_replies.get((workspace_name, channel_name, thread_ts), []))
        if oldest is not None:
            replies = [reply for reply in replies if float(reply.message_ts) > float(oldest)]
        return replies[:limit]

    def emit_frame(self, workspace_name: str, raw_frame: str) -> None:
        self.frame_handlers[workspace_name](raw_frame)

    def emit_disconnect(self, workspace_name: str) -> None:
        self.disconnect_handlers[workspace_name]()


def _config(tmp_path):
    return AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        workspaces=[
            WorkspaceConfig(
                name="oracle",
                allowed_actor_ids=["U123"],
                channels=[ChannelConfig(name="yifanche-private")],
            )
        ],
    )


def test_watcher_reconciles_root_messages_since_channel_cursor(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_channel_cursor("oracle", "yifanche-private", "1.0")
    browser = FakeBrowser()
    browser.channel_ids[("oracle", "yifanche-private")] = "C123"
    browser.root_messages[("oracle", "yifanche-private")] = [
        SlackRootMessage(
            workspace_name="oracle",
            channel_name="yifanche-private",
            thread_ts="1.0",
            message_ts="1.0",
            author_actor_id="U123",
            text="old",
        ),
        SlackRootMessage(
            workspace_name="oracle",
            channel_name="yifanche-private",
            thread_ts="2.0",
            message_ts="2.0",
            author_actor_id="U123",
            text="Bob, hi",
        ),
    ]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()

    assert orchestrator.root_calls == [
        {
            "workspace_name": "oracle",
            "channel_name": "yifanche-private",
            "message_ts": "2.0",
            "author_actor_id": "U123",
            "text": "Bob, hi",
        }
    ]
    assert state.get_channel_cursor("oracle", "yifanche-private") == "2.0"


def test_watcher_hydrates_root_event_from_websocket_signal(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_channel_cursor("oracle", "yifanche-private", "1.0")
    browser = FakeBrowser()
    browser.channel_ids[("oracle", "yifanche-private")] = "C123"
    browser.root_messages[("oracle", "yifanche-private")] = [
        SlackRootMessage(
            workspace_name="oracle",
            channel_name="yifanche-private",
            thread_ts="1.0",
            message_ts="1.0",
            author_actor_id="U123",
            text="old",
        )
    ]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()
    browser.root_messages[("oracle", "yifanche-private")].append(
        SlackRootMessage(
            workspace_name="oracle",
            channel_name="yifanche-private",
            thread_ts="2.0",
            message_ts="2.0",
            author_actor_id="U123",
            text="Bob, websocket",
        )
    )
    browser.emit_frame(
        "oracle",
        '{"type":"message","channel":"C123","ts":"2.0","text":"Bob, websocket"}',
    )

    watcher.run_cycle()

    assert orchestrator.root_calls[-1]["message_ts"] == "2.0"
    assert orchestrator.root_calls[-1]["text"] == "Bob, websocket"
    assert state.get_channel_cursor("oracle", "yifanche-private") == "2.0"


def test_watcher_hydrates_thread_reply_event_for_tracked_session(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser = FakeBrowser()
    browser.channel_ids[("oracle", "yifanche-private")] = "C123"
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()
    browser.thread_replies[("oracle", "yifanche-private", "10.0")] = [
        SlackThreadReplyMessage(
            workspace_name="oracle",
            channel_name="yifanche-private",
            thread_ts="10.0",
            message_ts="9999999999.0",
            author_actor_id="U123",
            text="follow-up",
        )
    ]
    browser.emit_frame(
        "oracle",
        '{"type":"message","subtype":"message_replied","message":{"channel":"C123","thread_ts":"10.0","latest_reply":"9999999999.0"}}',
    )

    watcher.run_cycle()

    assert orchestrator.reply_calls == [
        {
            "workspace_name": "oracle",
            "channel_name": "yifanche-private",
            "thread_ts": "10.0",
            "message_ts": "9999999999.0",
            "author_actor_id": "U123",
            "text": "follow-up",
        }
    ]
    assert state.get_thread_cursor("oracle", "yifanche-private", "10.0") == "9999999999.0"


def test_watcher_reconciles_root_messages_across_multiple_history_pages(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_channel_cursor("oracle", "yifanche-private", "0.0")
    browser = FakeBrowser()
    browser.channel_ids[("oracle", "yifanche-private")] = "C123"
    browser.root_messages[("oracle", "yifanche-private")] = [
        SlackRootMessage(
            workspace_name="oracle",
            channel_name="yifanche-private",
            thread_ts="{0}.0".format(index),
            message_ts="{0}.0".format(index),
            author_actor_id="U123",
            text="Bob, message {0}".format(index),
        )
        for index in range(1, 56)
    ]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()

    assert len(orchestrator.root_calls) == 55
    assert orchestrator.root_calls[-1]["message_ts"] == "55.0"
    assert state.get_channel_cursor("oracle", "yifanche-private") == "55.0"


def test_watcher_reconciles_thread_replies_across_multiple_pages(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser = FakeBrowser()
    browser.channel_ids[("oracle", "yifanche-private")] = "C123"
    browser.thread_replies[("oracle", "yifanche-private", "10.0")] = [
        SlackThreadReplyMessage(
            workspace_name="oracle",
            channel_name="yifanche-private",
            thread_ts="10.0",
            message_ts="{0}.0".format(9000000000 + index),
            author_actor_id="U123",
            text="reply {0}".format(index),
        )
        for index in range(1, 206)
    ]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()

    assert len(orchestrator.reply_calls) == 205
    assert orchestrator.reply_calls[-1]["message_ts"] == "9000000205.0"
    assert state.get_thread_cursor("oracle", "yifanche-private", "10.0") == "9000000205.0"
