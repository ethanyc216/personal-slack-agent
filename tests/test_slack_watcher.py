from concurrent.futures import ThreadPoolExecutor
import time

from personal_slack_agent.models import (
    AppConfig,
    ChannelConfig,
    DefaultSettings,
    SessionStatus,
    WatcherSettings,
    WorkspaceConfig,
)
from personal_slack_agent.slack import (
    SlackRootMessage,
    SlackSearchMessage,
    SlackThreadMessage,
    SlackThreadReplyMessage,
)
from personal_slack_agent.state import BobStateStore


class RecordingOrchestrator:
    def __init__(self) -> None:
        self.root_calls = []
        self.reply_calls = []
        self.ultimate_calls = []
        self._ultimate_seen = set()

    def handle_new_root_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
        author_actor_id: str,
        text: str,
    ) -> None:
        if channel_name.startswith("slack:") and text.strip().lower().startswith("bob"):
            self.ultimate_calls.append(
                {
                    "workspace_name": workspace_name,
                    "channel_name": channel_name,
                    "thread_ts": message_ts,
                    "message_ts": message_ts,
                    "author_actor_id": author_actor_id,
                    "text": text,
                }
            )
            return
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

    def handle_ultimate_invocation(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        author_actor_id: str,
        text: str,
    ) -> None:
        key = (workspace_name, channel_name, thread_ts, message_ts)
        if key in self._ultimate_seen:
            return
        self._ultimate_seen.add(key)
        self.ultimate_calls.append(
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
        self.accessible_conversation_ids = {}
        self.root_messages = {}
        self.root_message_errors = {}
        self.root_message_calls = []
        self.operations = []
        self.search_results = {}
        self.search_queries = []
        self.thread_messages = {}
        self.thread_replies = {}
        self.thread_reply_errors = {}
        self.thread_reply_calls = []
        self.frame_handlers = {}
        self.disconnect_handlers = {}

    def get_channel_id(self, workspace_name: str, channel_name: str) -> str:
        if channel_name.startswith("slack:"):
            return channel_name.split(":", 1)[1]
        return self.channel_ids[(workspace_name, channel_name)]

    def list_accessible_conversation_ids(self, workspace_name: str):
        self.operations.append(("accessible", workspace_name))
        value = self.accessible_conversation_ids.get(workspace_name, [])
        if isinstance(value, Exception):
            raise value
        return list(value)

    def search_messages(
        self,
        workspace_name: str,
        query: str,
        count: int = 20,
        page: int = 1,
        sort: str = None,
        sort_dir: str = None,
    ):
        self.operations.append(("search", workspace_name))
        self.search_queries.append(query)
        del count
        del page
        del sort
        del sort_dir
        return list(self.search_results.get(workspace_name, []))

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
        self.operations.append(("root", channel_name))
        self.root_message_calls.append((workspace_name, channel_name, oldest, latest, limit))
        error = self.root_message_errors.get((workspace_name, channel_name))
        if error is not None:
            raise error
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
        self.thread_reply_calls.append((workspace_name, channel_name, thread_ts, oldest, limit))
        error = self.thread_reply_errors.get((workspace_name, channel_name, thread_ts))
        if error is not None:
            raise error
        replies = list(self.thread_replies.get((workspace_name, channel_name, thread_ts), []))
        if oldest is not None:
            replies = [reply for reply in replies if float(reply.message_ts) > float(oldest)]
        return replies[:limit]

    def list_thread_messages(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ):
        explicit = self.thread_messages.get((workspace_name, channel_name, thread_ts))
        if explicit is not None:
            return list(explicit)
        messages = []
        for item in self.root_messages.get((workspace_name, channel_name), []):
            if item.message_ts == thread_ts:
                messages.append(
                    SlackThreadMessage(
                        workspace_name=item.workspace_name,
                        channel_name=item.channel_name,
                        thread_ts=thread_ts,
                        message_ts=item.message_ts,
                        author_actor_id=item.author_actor_id,
                        text=item.text,
                    )
                )
                break
        for item in self.thread_replies.get((workspace_name, channel_name, thread_ts), []):
            messages.append(
                SlackThreadMessage(
                    workspace_name=item.workspace_name,
                    channel_name=item.channel_name,
                    thread_ts=item.thread_ts,
                    message_ts=item.message_ts,
                    author_actor_id=item.author_actor_id,
                    text=item.text,
                )
            )
        return messages

    def emit_frame(self, workspace_name: str, raw_frame: str) -> None:
        self.frame_handlers[workspace_name](raw_frame)

    def emit_disconnect(self, workspace_name: str) -> None:
        self.disconnect_handlers[workspace_name]()


def _config(tmp_path):
    return AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        workspaces=[
            WorkspaceConfig(
                name="bob_company",
                channels=[ChannelConfig(name="bob_private_channel")],
            )
        ],
    )


def _ultimate_mode_config(tmp_path):
    config = _config(tmp_path)
    config.watcher = WatcherSettings(bob_ultimate_mode=True)
    return config


def test_reply_filter_ignores_generated_configured_alias_status():
    from personal_slack_agent.slack.watcher import _should_route_reply

    reply = SlackThreadReplyMessage(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1.0",
        message_ts="10.0",
        author_actor_id="U123",
        text="_*bObBy is working on it :arrows_counterclockwise::*_ session=`abc`",
    )

    assert not _should_route_reply(reply, 1, set(), set(), ["Bob", "Bobby"])


def test_watcher_ignores_generated_reply_from_stored_alias_after_config_alias_changes(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1.0",
        root_ts="1.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
        assistant_name="Bob",
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.thread_replies[("bob_company", "bob_private_channel", "1.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1.0",
            message_ts="10.0",
            author_actor_id="U123",
            text="_*Bob is working on it :arrows_counterclockwise::*_ session=`abc`",
        )
    ]
    config = _config(tmp_path)
    config.defaults.assistant_names = ["Arctic"]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
    )
    watcher._initialized = True

    watcher.reconcile_thread_since_cursor("bob_company", "bob_private_channel", "1.0")

    assert orchestrator.reply_calls == []


def test_reply_filter_does_not_ignore_user_text_that_mentions_working_on_it():
    from personal_slack_agent.slack.watcher import _should_route_reply

    reply = SlackThreadReplyMessage(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1.0",
        message_ts="10.0",
        author_actor_id="U123",
        text="the other tool is working on it",
    )

    assert _should_route_reply(reply, 1, set(), set(), ["Bob", "Bobby"])


def test_watcher_reconciles_root_messages_since_channel_cursor(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_channel_cursor("bob_company", "bob_private_channel", "1.0")
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.root_messages[("bob_company", "bob_private_channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1.0",
            message_ts="1.0",
            author_actor_id="U123",
            text="old",
        ),
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
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
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "message_ts": "2.0",
            "author_actor_id": "U123",
            "text": "Bob, hi",
        }
    ]
    assert state.get_channel_cursor("bob_company", "bob_private_channel") == "2.0"


def test_watcher_hydrates_root_event_from_websocket_signal(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_channel_cursor("bob_company", "bob_private_channel", "1.0")
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.root_messages[("bob_company", "bob_private_channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
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
    browser.root_messages[("bob_company", "bob_private_channel")].append(
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="2.0",
            message_ts="2.0",
            author_actor_id="U123",
            text="Bob, websocket",
        )
    )
    browser.emit_frame(
        "bob_company",
        '{"type":"message","channel":"C123","ts":"2.0","text":"Bob, websocket"}',
    )

    watcher.run_cycle()

    assert orchestrator.root_calls[-1]["message_ts"] == "2.0"
    assert orchestrator.root_calls[-1]["text"] == "Bob, websocket"
    assert state.get_channel_cursor("bob_company", "bob_private_channel") == "2.0"


def test_watcher_hydrates_thread_reply_event_for_tracked_session(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    state.record_processed_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        message_ts="10.0",
        author_actor_id="U123",
        purpose="root_request",
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()
    browser.thread_replies[("bob_company", "bob_private_channel", "10.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="10.0",
            message_ts="9999999999.0",
            author_actor_id="U123",
            text="follow-up",
        )
    ]
    browser.emit_frame(
        "bob_company",
        '{"type":"message","subtype":"message_replied","message":{"channel":"C123","thread_ts":"10.0","latest_reply":"9999999999.0"}}',
    )

    watcher.run_cycle()

    assert orchestrator.reply_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "thread_ts": "10.0",
            "message_ts": "9999999999.0",
            "author_actor_id": "U123",
            "text": "follow-up",
        }
    ]


def test_watcher_routes_unconfigured_root_message_to_ultimate_mode(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.accessible_conversation_ids["bob_company"] = ["C999"]
    browser.root_messages[("bob_company", "slack:C999")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="2.0",
            message_ts="2.0",
            author_actor_id="U123",
            text="bob review this",
        )
    ]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )

    watcher.run_cycle()

    assert orchestrator.ultimate_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "slack:C999",
            "thread_ts": "2.0",
            "message_ts": "2.0",
            "author_actor_id": "U123",
            "text": "bob review this",
        }
    ]


def test_watcher_routes_unconfigured_bob_reply_to_ultimate_mode(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.accessible_conversation_ids["bob_company"] = ["C999"]
    browser.thread_replies[("bob_company", "slack:C999", "10.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="10.0",
            message_ts="10.1",
            author_actor_id="U123",
            text="bob can you do it?",
        )
    ]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )

    watcher.run_cycle()
    browser.emit_frame(
        "bob_company",
        '{"type":"message","channel":"C999","ts":"10.1","thread_ts":"10.0","text":"bob can you do it?"}',
    )
    watcher.run_cycle()

    assert orchestrator.ultimate_calls[-1] == {
        "workspace_name": "bob_company",
        "channel_name": "slack:C999",
        "thread_ts": "10.0",
        "message_ts": "10.1",
        "author_actor_id": "U123",
        "text": "bob can you do it?",
    }


def test_watcher_ultimate_mode_ignores_non_bob_reply(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.accessible_conversation_ids["bob_company"] = ["C999"]
    browser.thread_replies[("bob_company", "slack:C999", "10.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="10.0",
            message_ts="10.2",
            author_actor_id="U999",
            text="plain follow-up",
        )
    ]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )

    watcher.run_cycle()

    assert orchestrator.reply_calls == []
    assert orchestrator.ultimate_calls == []


def test_watcher_routes_configured_channel_bob_reply_to_ultimate_mode(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.thread_replies[("bob_company", "bob_private_channel", "20.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="20.0",
            message_ts="20.1",
            author_actor_id="U123",
            text="bob do it here too",
        )
    ]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )

    watcher.run_cycle()
    browser.emit_frame(
        "bob_company",
        '{"type":"message","channel":"C123","ts":"20.1","thread_ts":"20.0","text":"bob do it here too"}',
    )
    watcher.run_cycle()

    assert orchestrator.ultimate_calls[-1] == {
        "workspace_name": "bob_company",
        "channel_name": "bob_private_channel",
        "thread_ts": "20.0",
        "message_ts": "20.1",
        "author_actor_id": "U123",
        "text": "bob do it here too",
    }


def test_watcher_routes_configured_channel_bob_reply_to_thread_handler_when_thread_is_legacy_session(
    tmp_path,
):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="30.0",
        root_ts="30.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    state.record_processed_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="30.0",
        message_ts="30.0",
        author_actor_id="U123",
        purpose="root_request",
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )

    watcher.run_cycle()
    browser.thread_replies[("bob_company", "bob_private_channel", "30.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="30.0",
            message_ts="9999999999.0",
            author_actor_id="U123",
            text="bob continue",
        )
    ]
    browser.emit_frame(
        "bob_company",
        '{"type":"message","channel":"C123","ts":"9999999999.0","thread_ts":"30.0","text":"bob continue"}',
    )
    watcher.run_cycle()

    assert orchestrator.reply_calls == [
        {
                "workspace_name": "bob_company",
                "channel_name": "bob_private_channel",
                "thread_ts": "30.0",
                "message_ts": "9999999999.0",
                "author_actor_id": "U123",
                "text": "bob continue",
            }
        ]
    assert orchestrator.ultimate_calls == []


def test_watcher_ultimate_mode_tolerates_runtime_channel_listing_failure(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.accessible_conversation_ids["bob_company"] = RuntimeError("enterprise_is_restricted")
    browser.root_messages[("bob_company", "bob_private_channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1.0",
            message_ts="1.0",
            author_actor_id="U123",
            text="Bob, hi",
        )
    ]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )

    watcher.run_cycle()

    assert orchestrator.root_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "message_ts": "1.0",
            "author_actor_id": "U123",
            "text": "Bob, hi",
        }
    ]


def test_watcher_search_fallback_routes_recent_ultimate_reply(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.search_results["bob_company"] = [
        SlackSearchMessage(
            workspace_name="bob_company",
            channel_id="C123",
            message_ts="1777007562.458519",
            thread_ts="1777006365.616769",
            author_actor_id="U123",
            text="bob please reply with exactly ultimate mode test 1 ok and nothing else.",
        )
    ]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )
    watcher._initialized = True
    watcher._channel_name_by_id[("bob_company", "C123")] = "bob_private_channel"
    watcher._ultimate_search_cursor["bob_company"] = 1777007560.0

    watcher.run_cycle()

    assert orchestrator.ultimate_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "thread_ts": "1777006365.616769",
            "message_ts": "1777007562.458519",
            "author_actor_id": "U123",
            "text": "bob please reply with exactly ultimate mode test 1 ok and nothing else.",
        }
    ]


def test_watcher_search_fallback_routes_configured_alias(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.search_results["bob_company"] = [
        SlackSearchMessage(
            workspace_name="bob_company",
            channel_id="C123",
            message_ts="1777007562.458519",
            thread_ts="1777006365.616769",
            author_actor_id="U123",
            text="Bobby please reply with exactly alias mode ok and nothing else.",
        )
    ]
    config = _ultimate_mode_config(tmp_path)
    config.defaults.assistant_names = ["Bob", "Bobby"]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
    )
    watcher._initialized = True
    watcher._channel_name_by_id[("bob_company", "C123")] = "bob_private_channel"
    watcher._ultimate_search_cursor["bob_company"] = 1777007560.0

    watcher.run_cycle()

    assert browser.search_queries[:2] == ["Bob", "Bobby"]
    assert set(browser.search_queries) == {"Bob", "Bobby"}
    assert orchestrator.ultimate_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "thread_ts": "1777006365.616769",
            "message_ts": "1777007562.458519",
            "author_actor_id": "U123",
            "text": "Bobby please reply with exactly alias mode ok and nothing else.",
        }
    ]


def test_watcher_search_fallback_recovers_late_indexed_reply_after_newer_hit(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )
    watcher._initialized = True
    watcher._channel_name_by_id[("bob_company", "C123")] = "bob_private_channel"
    watcher._ultimate_search_cursor["bob_company"] = 1777007000.0

    browser.search_results["bob_company"] = [
        SlackSearchMessage(
            workspace_name="bob_company",
            channel_id="C123",
            message_ts="1777007562.458519",
            thread_ts="1777006365.616769",
            author_actor_id="U123",
            text="bob first visible hit",
        )
    ]
    watcher.run_cycle()

    assert orchestrator.ultimate_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "thread_ts": "1777006365.616769",
            "message_ts": "1777007562.458519",
            "author_actor_id": "U123",
            "text": "bob first visible hit",
        }
    ]

    orchestrator.ultimate_calls.clear()
    browser.search_results["bob_company"] = [
        SlackSearchMessage(
            workspace_name="bob_company",
            channel_id="C123",
            message_ts="1777007561.000001",
            thread_ts="1777006365.616769",
            author_actor_id="U123",
            text="bob late indexed older hit",
        )
    ]
    watcher.run_cycle()

    assert orchestrator.ultimate_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "thread_ts": "1777006365.616769",
            "message_ts": "1777007561.000001",
            "author_actor_id": "U123",
            "text": "bob late indexed older hit",
        }
    ]


def test_watcher_search_fallback_runs_again_after_reconcile_work(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "first-channel")] = "C111"
    browser.root_messages[("bob_company", "first-channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="first-channel",
            thread_ts="1.0",
            message_ts="1.0",
            author_actor_id="U123",
            text="hello",
        )
    ]
    orchestrator = RecordingOrchestrator()
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        watcher=WatcherSettings(bob_ultimate_mode=True),
        workspaces=[
            WorkspaceConfig(
                name="bob_company",
                channels=[ChannelConfig(name="first-channel")],
            )
        ],
    )

    class LateSearchBrowser(FakeBrowser):
        def __init__(self):
            super().__init__()
            self.root_calls = 0

        def list_root_messages(
            self,
            workspace_name: str,
            channel_name: str,
            oldest: str = None,
            latest: str = None,
            limit: int = 50,
            ):
                messages = super().list_root_messages(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    oldest=oldest,
                    latest=latest,
                    limit=limit,
                )
                self.root_calls += 1
                if self.root_calls >= 1:
                    self.search_results["bob_company"] = [
                        SlackSearchMessage(
                            workspace_name="bob_company",
                            channel_id="C111",
                            message_ts="1777011778.087299",
                            thread_ts="1776987128.600299",
                            author_actor_id="U123",
                            text="bob late in cycle",
                        )
                    ]
                return messages

    late_browser = LateSearchBrowser()
    late_browser.channel_ids = browser.channel_ids
    late_browser.root_messages = browser.root_messages

    watcher = SlackWatcher(
        browser=late_browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
    )
    watcher._initialized = True
    watcher._channel_name_by_id[("bob_company", "C111")] = "first-channel"
    watcher._ultimate_search_cursor["bob_company"] = 1777011700.0

    watcher.run_cycle()

    assert orchestrator.ultimate_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "first-channel",
            "thread_ts": "1776987128.600299",
            "message_ts": "1777011778.087299",
            "author_actor_id": "U123",
            "text": "bob late in cycle",
        }
    ]


def test_watcher_stops_between_channel_reconciles_when_requested(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    stop_flag = {"value": False}
    orchestrator = RecordingOrchestrator()
    original_handle_new_root_message = orchestrator.handle_new_root_message

    def stopping_handle_new_root_message(**kwargs):
        original_handle_new_root_message(**kwargs)
        if kwargs.get("channel_name") == "first-channel":
            stop_flag["value"] = True

    class StoppingBrowser(FakeBrowser):
        def list_root_messages(
            self,
            workspace_name: str,
            channel_name: str,
            oldest: str = None,
            latest: str = None,
            limit: int = 50,
        ):
            messages = super().list_root_messages(
                workspace_name=workspace_name,
                channel_name=channel_name,
                oldest=oldest,
                latest=latest,
                limit=limit,
            )
            return messages

    browser = StoppingBrowser()
    browser.channel_ids[("bob_company", "first-channel")] = "C111"
    browser.channel_ids[("bob_company", "second-channel")] = "C222"
    browser.root_messages[("bob_company", "first-channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="first-channel",
            thread_ts="1.0",
            message_ts="1.0",
            author_actor_id="U123",
            text="Bob, first",
        )
    ]
    browser.root_messages[("bob_company", "second-channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="second-channel",
            thread_ts="2.0",
            message_ts="2.0",
            author_actor_id="U123",
            text="Bob, second",
        )
    ]
    orchestrator.handle_new_root_message = stopping_handle_new_root_message
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        workspaces=[
            WorkspaceConfig(
                name="bob_company",
                channels=[
                    ChannelConfig(name="first-channel"),
                    ChannelConfig(name="second-channel"),
                ],
            )
        ],
    )
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
        should_stop=lambda: stop_flag["value"],
    )

    watcher.run_cycle()

    assert orchestrator.root_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "first-channel",
            "message_ts": "1.0",
            "author_actor_id": "U123",
            "text": "Bob, first",
        }
    ]


def test_watcher_reconciles_all_configured_channels_before_limited_runtime_backfill(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "first-channel")] = "C111"
    browser.channel_ids[("bob_company", "second-channel")] = "C222"
    browser.accessible_conversation_ids["bob_company"] = ["C999", "C888"]
    browser.root_messages[("bob_company", "first-channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="first-channel",
            thread_ts="1.0",
            message_ts="1.0",
            author_actor_id="U123",
            text="Bob, first",
        )
    ]
    browser.root_messages[("bob_company", "second-channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="second-channel",
            thread_ts="2.0",
            message_ts="2.0",
            author_actor_id="U123",
            text="Bob, second",
        )
    ]
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        watcher=WatcherSettings(bob_ultimate_mode=True),
        workspaces=[
            WorkspaceConfig(
                name="bob_company",
                channels=[
                    ChannelConfig(name="first-channel"),
                    ChannelConfig(name="second-channel"),
                ],
            )
        ],
    )
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
    )

    watcher.run_cycle()

    assert [call[1] for call in browser.root_message_calls] == [
        "first-channel",
        "second-channel",
        "slack:C999",
    ]
    assert orchestrator.root_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "first-channel",
            "message_ts": "1.0",
            "author_actor_id": "U123",
            "text": "Bob, first",
        },
        {
            "workspace_name": "bob_company",
            "channel_name": "second-channel",
            "message_ts": "2.0",
            "author_actor_id": "U123",
            "text": "Bob, second",
        },
    ]


def test_watcher_initial_cycle_prioritizes_search_then_configured_before_runtime_discovery(
    tmp_path,
):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.accessible_conversation_ids["bob_company"] = ["C999"]
    browser.root_messages[("bob_company", "bob_private_channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1.0",
            message_ts="1.0",
            author_actor_id="U123",
            text="Bob, configured first",
        )
    ]
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=RecordingOrchestrator(),
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )

    watcher.run_cycle()

    assert browser.operations[0] == ("search", "bob_company")
    assert browser.operations.index(("root", "bob_private_channel")) < browser.operations.index(
        ("accessible", "bob_company")
    )


def test_watcher_steady_cycle_checks_ultimate_search_before_configured_channels(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.root_messages[("bob_company", "bob_private_channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1.0",
            message_ts="1.0",
            author_actor_id="U123",
            text="Bob, configured first",
        )
    ]
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=RecordingOrchestrator(),
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )
    watcher._initialized = True
    watcher._channel_name_by_id[("bob_company", "C123")] = "bob_private_channel"

    watcher.run_cycle()

    assert browser.operations[:2] == [
        ("search", "bob_company"),
        ("root", "bob_private_channel"),
    ]


def test_watcher_processes_event_hydration_before_runtime_backfill(tmp_path):
    from personal_slack_agent.slack.events import SlackRealtimeEvent
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.accessible_conversation_ids["bob_company"] = ["C999"]
    browser.root_messages[("bob_company", "bob_private_channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1.0",
            message_ts="1.0",
            author_actor_id="U123",
            text="Bob, configured",
        )
    ]
    browser.root_messages[("bob_company", "slack:C999")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="2.0",
            message_ts="2.0",
            author_actor_id="U123",
            text="bob event",
        )
    ]
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=RecordingOrchestrator(),
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )
    watcher._initialized = True
    watcher._channel_name_by_id[("bob_company", "C123")] = "bob_private_channel"
    watcher._event_queue.append(
        (
            "bob_company",
            SlackRealtimeEvent(
                kind="root_message_seen",
                channel_id="C999",
                thread_ts=None,
                message_ts="2.0",
            ),
        )
    )

    watcher.run_cycle()

    assert browser.operations.index(("root", "bob_private_channel")) < browser.operations.index(
        ("root", "slack:C999")
    )
    assert browser.operations.index(("root", "slack:C999")) < browser.operations.index(
        ("accessible", "bob_company")
    )


def test_pending_thread_reconcile_keeps_retry_when_lease_is_busy(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=RecordingOrchestrator(),
        state_store=state,
        config=_config(tmp_path),
    )
    key = ("bob_company", "bob_private_channel", "10.0")
    watcher._threads_pending_reconcile.add(key)
    lease_scope = watcher._thread_lease_scope(*key)
    assert state.try_acquire_watcher_lease(
        scope=lease_scope,
        owner="other-worker",
        now_epoch=int(time.time()),
        ttl_seconds=30,
    )

    watcher._reconcile_pending_threads()

    assert key in watcher._threads_pending_reconcile
    assert browser.thread_reply_calls == []

    assert state.release_watcher_lease(scope=lease_scope, owner="other-worker")
    watcher._reconcile_pending_threads()

    assert key not in watcher._threads_pending_reconcile
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "10.0", None, 200)
    ]


def test_watcher_lease_blocks_same_lane_from_another_worker_thread(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=RecordingOrchestrator(),
        state_store=state,
        config=_config(tmp_path),
    )
    lease_scope = watcher._channel_lease_scope("bob_company", "bob_private_channel")

    assert watcher._try_acquire_watcher_lease(lease_scope, "runtime-backfill")
    with ThreadPoolExecutor(max_workers=1) as pool:
        blocked = pool.submit(
            watcher._try_acquire_watcher_lease,
            lease_scope,
            "runtime-backfill",
        ).result()

    assert not blocked
    assert watcher._release_watcher_lease(lease_scope, "runtime-backfill")


def test_watcher_runtime_backfill_fetches_only_one_root_page_per_channel_per_cycle(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.accessible_conversation_ids["bob_company"] = ["C999"]
    browser.root_messages[("bob_company", "slack:C999")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1.0",
            message_ts="1.0",
            author_actor_id="U123",
            text="bob older runtime request",
        ),
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="2.0",
            message_ts="2.0",
            author_actor_id="U123",
            text="bob latest runtime request",
        ),
    ]
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        watcher=WatcherSettings(bob_ultimate_mode=True, root_batch_size=1),
        workspaces=[
            WorkspaceConfig(
                name="bob_company",
                channels=[
                    ChannelConfig(name="bob_private_channel"),
                ],
            )
        ],
    )
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
    )

    watcher.run_cycle()

    runtime_calls = [call for call in browser.root_message_calls if call[1] == "slack:C999"]
    assert runtime_calls == [("bob_company", "slack:C999", None, None, 1)]
    assert orchestrator.ultimate_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "slack:C999",
            "thread_ts": "2.0",
            "message_ts": "2.0",
            "author_actor_id": "U123",
            "text": "bob latest runtime request",
        }
    ]


def test_watcher_skips_root_history_failure_and_continues_configured_channels(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "first-channel")] = "C111"
    browser.channel_ids[("bob_company", "second-channel")] = "C222"
    browser.root_message_errors[("bob_company", "first-channel")] = RuntimeError(
        "Slack API conversations.history failed: internal_error"
    )
    browser.root_messages[("bob_company", "second-channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="second-channel",
            thread_ts="2.0",
            message_ts="2.0",
            author_actor_id="U123",
            text="Bob, second",
        )
    ]
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        workspaces=[
            WorkspaceConfig(
                name="bob_company",
                channels=[
                    ChannelConfig(name="first-channel"),
                    ChannelConfig(name="second-channel"),
                ],
            )
        ],
    )
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
    )

    watcher.run_cycle()

    assert [call[1] for call in browser.root_message_calls] == [
        "first-channel",
        "second-channel",
    ]
    assert orchestrator.root_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "second-channel",
            "message_ts": "2.0",
            "author_actor_id": "U123",
            "text": "Bob, second",
        }
    ]
    assert state.get_channel_cursor("bob_company", "first-channel") is None
    assert state.get_channel_cursor("bob_company", "second-channel") == "2.0"


def test_watcher_skips_root_event_history_failure_and_continues_event_queue(tmp_path):
    from personal_slack_agent.slack.events import SlackRealtimeEvent
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "first-channel")] = "C111"
    browser.channel_ids[("bob_company", "second-channel")] = "C222"
    browser.root_message_errors[("bob_company", "first-channel")] = RuntimeError(
        "Slack API conversations.history failed: internal_error"
    )
    browser.root_messages[("bob_company", "second-channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="second-channel",
            thread_ts="2.0",
            message_ts="2.0",
            author_actor_id="U123",
            text="Bob, second",
        )
    ]
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        workspaces=[
            WorkspaceConfig(
                name="bob_company",
                channels=[
                    ChannelConfig(name="first-channel"),
                    ChannelConfig(name="second-channel"),
                ],
            )
        ],
    )
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
    )
    watcher._initialized = True
    watcher._channel_name_by_id[("bob_company", "C111")] = "first-channel"
    watcher._channel_name_by_id[("bob_company", "C222")] = "second-channel"

    watcher._event_queue.append(
        (
            "bob_company",
            SlackRealtimeEvent(
                kind="root_message_seen",
                channel_id="C111",
                thread_ts=None,
                message_ts="1.0",
            ),
        )
    )
    watcher._event_queue.append(
        (
            "bob_company",
            SlackRealtimeEvent(
                kind="root_message_seen",
                channel_id="C222",
                thread_ts=None,
                message_ts="2.0",
            ),
        )
    )
    watcher.run_cycle()

    assert orchestrator.root_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "second-channel",
            "message_ts": "2.0",
            "author_actor_id": "U123",
            "text": "Bob, second",
        }
    ]
    assert state.get_channel_cursor("bob_company", "first-channel") is None
    assert state.get_channel_cursor("bob_company", "second-channel") == "2.0"


def test_watcher_skips_thread_reply_with_slack_normalized_bob_prefix(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    state.upsert_outbound_intent(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        intent_key="final-session-123",
        action="post_thread_reply",
        text="_*Bob :white_check_mark::*_ Final answer",
        delivery_state="pending",
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.thread_replies[("bob_company", "bob_private_channel", "10.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="10.0",
            message_ts="9999999999.0",
            author_actor_id="U123",
            text="Bob :white_check_mark: Final answer",
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

    assert orchestrator.reply_calls == []
    assert state.get_thread_cursor("bob_company", "bob_private_channel", "10.0") == "9999999999.0"


def test_watcher_skips_ratelimited_thread_reconcile_without_aborting_cycle(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.thread_reply_errors[("bob_company", "bob_private_channel", "10.0")] = RuntimeError(
        "Slack API conversations.replies failed: ratelimited"
    )
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()

    assert orchestrator.reply_calls == []
    assert state.get_thread_cursor("bob_company", "bob_private_channel", "10.0") is None


def test_watcher_backs_off_workspace_after_ratelimited_thread_reply_call(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="11.0",
        root_ts="11.0",
        codex_session_id="session-456",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.thread_reply_errors[("bob_company", "bob_private_channel", "10.0")] = RuntimeError(
        "Slack API conversations.replies failed: ratelimited"
    )
    browser.thread_replies[("bob_company", "bob_private_channel", "11.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="11.0",
            message_ts="20.0",
            author_actor_id="U123",
            text="follow-up",
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

    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "10.0", None, 200)
    ]
    assert orchestrator.reply_calls == []


def test_watcher_reconciles_recent_reply_even_when_old_tracked_thread_rate_limits(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-stale",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="11.0",
        root_ts="11.0",
        codex_session_id="session-recent",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    with state._connect() as connection:
        connection.execute(
            """
            UPDATE sessions
            SET updated_at = ?, created_at = ?
            WHERE workspace_name = ?
              AND channel_name = ?
              AND thread_ts = ?
            """,
            (1, 1, "bob_company", "bob_private_channel", "10.0"),
        )

    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.thread_reply_errors[("bob_company", "bob_private_channel", "10.0")] = RuntimeError(
        "Slack API conversations.replies failed: ratelimited"
    )
    browser.thread_replies[("bob_company", "bob_private_channel", "11.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="11.0",
            message_ts="9999999999.0",
            author_actor_id="U123",
            text="recent follow-up",
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

    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "11.0", None, 200),
    ]
    assert orchestrator.reply_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "thread_ts": "11.0",
            "message_ts": "9999999999.0",
            "author_actor_id": "U123",
            "text": "recent follow-up",
        }
    ]


def test_watcher_spaces_terminal_thread_reconcile_across_cycles(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    now_epoch = int(time.time())
    for index, updated_at in ((10, now_epoch - 10), (11, now_epoch - 20), (12, now_epoch - 30)):
        state.upsert_session(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="{0}.0".format(index),
            root_ts="{0}.0".format(index),
            codex_session_id="session-{0}".format(index),
            cwd=str(tmp_path),
            owner_actor_id="U123",
            status=SessionStatus.CLOSED_IDLE,
        )
        with state._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET updated_at = ?, created_at = ?
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                """,
                (
                    updated_at,
                    updated_at,
                    "bob_company",
                    "bob_private_channel",
                    "{0}.0".format(index),
                ),
            )

    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "10.0", None, 200)
    ]

    browser.thread_reply_calls.clear()
    watcher.run_cycle()
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "11.0", None, 200)
    ]

    browser.thread_reply_calls.clear()
    watcher.run_cycle()
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "12.0", None, 200)
    ]


def test_watcher_eventually_reconciles_old_tracked_terminal_threads(tmp_path, monkeypatch):
    from personal_slack_agent.slack.watcher import SlackWatcher

    now_epoch = 2_000_000
    monotonic_now = 10_000.0
    monkeypatch.setattr("personal_slack_agent.slack.watcher.time.time", lambda: now_epoch)
    monkeypatch.setattr(
        "personal_slack_agent.slack.watcher.time.monotonic", lambda: monotonic_now
    )

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    for index, updated_at in (
        (10, now_epoch - 10),
        (11, now_epoch - 20),
        (12, now_epoch - 30),
        (13, now_epoch - 40),
        (14, now_epoch - 50),
        (15, now_epoch - 60),
        (16, now_epoch - 4_000),
        (17, now_epoch - 5_000),
    ):
        state.upsert_session(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="{0}.0".format(index),
            root_ts="{0}.0".format(index),
            codex_session_id="session-{0}".format(index),
            cwd=str(tmp_path),
            owner_actor_id="U123",
            status=SessionStatus.CLOSED_IDLE,
        )
        with state._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET updated_at = ?, created_at = ?
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                """,
                (
                    updated_at,
                    updated_at,
                    "bob_company",
                    "bob_private_channel",
                    "{0}.0".format(index),
                ),
            )

    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "10.0", None, 200),
        ("bob_company", "bob_private_channel", "16.0", None, 200),
    ]

    browser.thread_reply_calls.clear()
    watcher.run_cycle()
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "11.0", None, 200),
    ]

    monotonic_now += 60.0
    browser.thread_reply_calls.clear()
    watcher.run_cycle()
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "12.0", None, 200),
        ("bob_company", "bob_private_channel", "17.0", None, 200),
    ]


def test_watcher_revisits_recently_active_terminal_thread_before_historical_window(tmp_path, monkeypatch):
    from personal_slack_agent.slack.watcher import SlackWatcher

    now_epoch = 2_000_000
    monotonic_now = 10_000.0
    monkeypatch.setattr("personal_slack_agent.slack.watcher.time.time", lambda: now_epoch)
    monkeypatch.setattr(
        "personal_slack_agent.slack.watcher.time.monotonic", lambda: monotonic_now
    )

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    for index, updated_at in (
        (10, now_epoch - 7_200),
        (11, now_epoch - 7_260),
    ):
        state.upsert_session(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="{0}.0".format(index),
            root_ts="{0}.0".format(index),
            codex_session_id="session-{0}".format(index),
            cwd=str(tmp_path),
            owner_actor_id="U123",
            status=SessionStatus.CLOSED_IDLE,
        )
        with state._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET updated_at = ?, created_at = ?
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                """,
                (
                    updated_at,
                    updated_at,
                    "bob_company",
                    "bob_private_channel",
                    "{0}.0".format(index),
                ),
            )

    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "10.0", None, 200),
    ]

    browser.thread_replies[("bob_company", "bob_private_channel", "10.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="10.0",
            message_ts="9999999999.0",
            author_actor_id="U123",
            text="late follow-up",
        )
    ]

    monotonic_now += 30.0
    browser.thread_reply_calls.clear()
    watcher.run_cycle()
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "11.0", None, 200),
    ]

    monotonic_now += 30.0
    browser.thread_reply_calls.clear()
    watcher.run_cycle()

    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "10.0", None, 200),
    ]
    assert orchestrator.reply_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "thread_ts": "10.0",
            "message_ts": "9999999999.0",
            "author_actor_id": "U123",
            "text": "late follow-up",
        }
    ]


def test_watcher_historical_sweep_backs_off_after_rate_limit(tmp_path, monkeypatch):
    from personal_slack_agent.slack.watcher import SlackWatcher

    now_epoch = 2_000_000
    monotonic_now = 10_000.0
    monkeypatch.setattr("personal_slack_agent.slack.watcher.time.time", lambda: now_epoch)
    monkeypatch.setattr(
        "personal_slack_agent.slack.watcher.time.monotonic", lambda: monotonic_now
    )

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    for index, updated_at in (
        (10, now_epoch - 10),
        (11, now_epoch - 20),
        (12, now_epoch - 30),
        (13, now_epoch - 40),
        (14, now_epoch - 50),
        (15, now_epoch - 60),
        (16, now_epoch - 4_000),
    ):
        state.upsert_session(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="{0}.0".format(index),
            root_ts="{0}.0".format(index),
            codex_session_id="session-{0}".format(index),
            cwd=str(tmp_path),
            owner_actor_id="U123",
            status=SessionStatus.CLOSED_IDLE,
        )
        with state._connect() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET updated_at = ?, created_at = ?
                WHERE workspace_name = ?
                  AND channel_name = ?
                  AND thread_ts = ?
                """,
                (
                    updated_at,
                    updated_at,
                    "bob_company",
                    "bob_private_channel",
                    "{0}.0".format(index),
                ),
            )

    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.thread_reply_errors[("bob_company", "bob_private_channel", "16.0")] = RuntimeError(
        "Slack API conversations.replies failed: ratelimited"
    )
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "10.0", None, 200),
        ("bob_company", "bob_private_channel", "16.0", None, 200),
    ]

    monotonic_now += 60.0
    browser.thread_reply_calls.clear()
    watcher.run_cycle()
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "11.0", None, 200),
    ]

    monotonic_now += 60.0
    browser.thread_reply_calls.clear()
    watcher.run_cycle()
    assert browser.thread_reply_calls == [
        ("bob_company", "bob_private_channel", "12.0", None, 200),
        ("bob_company", "bob_private_channel", "16.0", None, 200),
    ]


def test_watcher_periodically_reconciles_follow_up_replies_without_websocket_event(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_config(tmp_path),
    )

    watcher.run_cycle()
    browser.thread_replies[("bob_company", "bob_private_channel", "10.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="10.0",
            message_ts="9999999999.0",
            author_actor_id="U123",
            text="follow-up without event",
        )
    ]

    watcher.run_cycle()

    assert orchestrator.reply_calls == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "thread_ts": "10.0",
            "message_ts": "9999999999.0",
            "author_actor_id": "U123",
            "text": "follow-up without event",
        }
    ]
    assert state.get_thread_cursor("bob_company", "bob_private_channel", "10.0") == "9999999999.0"


def test_watcher_ignores_empty_text_thread_artifacts(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.thread_replies[("bob_company", "bob_private_channel", "10.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="10.0",
            message_ts="9999999999.0",
            author_actor_id="U123",
            text="",
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

    assert orchestrator.reply_calls == []


def test_watcher_ignores_escaped_thread_reply(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.thread_replies[("bob_company", "bob_private_channel", "10.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="10.0",
            message_ts="9999999999.0",
            author_actor_id="U999",
            text="## \n bob should ignore",
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

    assert orchestrator.reply_calls == []


def test_watcher_reconciles_root_messages_across_multiple_history_pages(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_channel_cursor("bob_company", "bob_private_channel", "0.0")
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.root_messages[("bob_company", "bob_private_channel")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
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
    assert state.get_channel_cursor("bob_company", "bob_private_channel") == "55.0"


def test_watcher_reconciles_thread_replies_across_multiple_pages(tmp_path):
    from personal_slack_agent.slack.watcher import SlackWatcher

    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    state.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="10.0",
        root_ts="10.0",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser = FakeBrowser()
    browser.channel_ids[("bob_company", "bob_private_channel")] = "C123"
    browser.thread_replies[("bob_company", "bob_private_channel", "10.0")] = [
        SlackThreadReplyMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
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
    assert state.get_thread_cursor("bob_company", "bob_private_channel", "10.0") == "9000000205.0"
