from dataclasses import asdict
from typing import Dict, List, Tuple

from personal_slack_agent.cli import agent as agent_module
from personal_slack_agent.cli.agent import run_once, run_poll_cycle
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
        self.root_calls: List[Dict[str, str]] = []
        self.reply_calls: List[Dict[str, str]] = []

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


class FakePollingBrowser:
    def __init__(self) -> None:
        self._root_messages: Dict[Tuple[str, str], List[SlackRootMessage]] = {}
        self._thread_replies: Dict[Tuple[str, str, str], List[SlackThreadReplyMessage]] = {}
        self.root_list_calls: List[Tuple[str, str]] = []
        self.thread_list_calls: List[Tuple[str, str, str]] = []

    def set_root_messages(
        self,
        workspace_name: str,
        channel_name: str,
        messages: List[SlackRootMessage],
    ) -> None:
        self._root_messages[(workspace_name, channel_name)] = list(messages)

    def set_thread_replies(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        replies: List[SlackThreadReplyMessage],
    ) -> None:
        self._thread_replies[(workspace_name, channel_name, thread_ts)] = list(replies)

    def list_root_messages(
        self,
        workspace_name: str,
        channel_name: str,
    ) -> List[SlackRootMessage]:
        self.root_list_calls.append((workspace_name, channel_name))
        return list(self._root_messages.get((workspace_name, channel_name), []))

    def list_thread_replies(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> List[SlackThreadReplyMessage]:
        key = (workspace_name, channel_name, thread_ts)
        self.thread_list_calls.append(key)
        return list(self._thread_replies.get(key, []))

    def close(self) -> None:
        return None


def test_slack_message_contract_dataclasses_preserve_required_fields():
    root = SlackRootMessage(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, summarize this",
    )
    reply = SlackThreadReplyMessage(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461010.000001",
        author_actor_id="U123",
        text="Please continue",
    )

    assert asdict(root) == {
        "workspace_name": "oracle",
        "channel_name": "yifanche-private",
        "thread_ts": "1743461000.000001",
        "message_ts": "1743461000.000001",
        "author_actor_id": "U123",
        "text": "Bob, summarize this",
    }
    assert asdict(reply) == {
        "workspace_name": "oracle",
        "channel_name": "yifanche-private",
        "thread_ts": "1743461000.000001",
        "message_ts": "1743461010.000001",
        "author_actor_id": "U123",
        "text": "Please continue",
    }


def test_run_poll_cycle_feeds_root_messages_and_managed_thread_replies(tmp_path):
    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        workspaces=[
            WorkspaceConfig(
                name="oracle",
                allowed_actor_ids=["U123"],
                channels=[
                    ChannelConfig(name="yifanche-private"),
                    ChannelConfig(name="team-channel"),
                ],
            )
        ],
    )
    state.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000100",
        root_ts="1743461000.000100",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
    )

    browser = FakePollingBrowser()
    browser.set_root_messages(
        "oracle",
        "yifanche-private",
        [
            SlackRootMessage(
                workspace_name="oracle",
                channel_name="yifanche-private",
                thread_ts="1743461000.000001",
                message_ts="1743461000.000001",
                author_actor_id="U123",
                text="Bob, hello",
            )
        ],
    )
    browser.set_thread_replies(
        "oracle",
        "yifanche-private",
        "1743461000.000100",
        [
                SlackThreadReplyMessage(
                    workspace_name="oracle",
                    channel_name="yifanche-private",
                    thread_ts="1743461000.000100",
                    message_ts="9999999999.000001",
                    author_actor_id="U123",
                    text="follow-up",
                )
            ],
    )
    orchestrator = RecordingOrchestrator()

    run_poll_cycle(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
    )

    assert browser.root_list_calls == [
        ("oracle", "yifanche-private"),
        ("oracle", "team-channel"),
    ]
    assert browser.thread_list_calls == [("oracle", "yifanche-private", "1743461000.000100")]
    assert orchestrator.root_calls == [
        {
            "workspace_name": "oracle",
            "channel_name": "yifanche-private",
            "message_ts": "1743461000.000001",
            "author_actor_id": "U123",
            "text": "Bob, hello",
        }
    ]
    assert orchestrator.reply_calls == [
        {
                "workspace_name": "oracle",
                "channel_name": "yifanche-private",
                "thread_ts": "1743461000.000100",
                "message_ts": "9999999999.000001",
                "author_actor_id": "U123",
                "text": "follow-up",
            }
        ]


def test_run_once_builds_runtime_stack_and_executes_poll_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / "bob.toml"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config_file.write_text(
        "\n".join(
            [
                "[defaults]",
                'default_cwd = "{0}"'.format(workspace_root),
                'allowed_actor_ids = ["U123"]',
                'browser_mode = "shared_browser"',
                "",
                "[[workspaces]]",
                'name = "oracle"',
                'allowed_actor_ids = ["U123"]',
                'slack_url = "https://app.slack.com/client/T12345678/C12345678"',
                "",
                "[[workspaces.channels]]",
                'name = "yifanche-private"',
            ]
        ),
        encoding="utf-8",
    )

    calls = {"poll": 0, "workspace_urls": None, "workspace_api_contexts": None}

    class FakeBrowser:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_workspace_urls(self, workspace_urls):
            calls["workspace_urls"] = dict(workspace_urls)

        def set_workspace_api_contexts(self, workspace_api_contexts):
            calls["workspace_api_contexts"] = dict(workspace_api_contexts)

        def close(self):
            return None

    class FakeRunner:
        pass

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def fake_poll_cycle(browser, orchestrator, state_store, config, logger=None):
        calls["poll"] += 1
        assert browser is not None
        assert orchestrator is not None
        assert state_store is not None
        assert config.workspaces[0].name == "oracle"

    monkeypatch.setattr(agent_module, "PlaywrightSlackAdapter", FakeBrowser)
    monkeypatch.setattr(agent_module, "SubprocessCodexRunner", FakeRunner)
    monkeypatch.setattr(agent_module, "BobOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(agent_module, "run_poll_cycle", fake_poll_cycle)

    exit_code = run_once(config_file)

    assert exit_code == 0
    assert calls["poll"] == 1
    assert calls["workspace_urls"] == {
        "oracle": "https://app.slack.com/client/T12345678/C12345678"
    }
    assert calls["workspace_api_contexts"] == {}


def test_run_poll_cycle_ignores_historical_and_bob_owned_thread_messages(tmp_path):
    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        workspaces=[
            WorkspaceConfig(
                name="oracle",
                allowed_actor_ids=["U123"],
                channels=[ChannelConfig(name="yifanche-private")],
            )
        ],
    )
    state.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1774999116.837699",
        root_ts="1774999116.837699",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    state.upsert_outbound_intent(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1774999116.837699",
        intent_key="final",
        action="post_thread_reply",
        text="codex Bob: hi",
    )
    state.mark_outbound_intent_delivered(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1774999116.837699",
        intent_key="final",
        message_ts="1775022338.821099",
    )

    browser = FakePollingBrowser()
    browser.set_thread_replies(
        "oracle",
        "yifanche-private",
        "1774999116.837699",
        [
            SlackThreadReplyMessage(
                workspace_name="oracle",
                channel_name="yifanche-private",
                thread_ts="1774999116.837699",
                message_ts="1.000000",
                author_actor_id="U123",
                text="old historical reply",
            ),
            SlackThreadReplyMessage(
                workspace_name="oracle",
                channel_name="yifanche-private",
                thread_ts="1774999116.837699",
                message_ts="1775022338.821099",
                author_actor_id="U123",
                text="codex Bob: hi",
            ),
            SlackThreadReplyMessage(
                workspace_name="oracle",
                channel_name="yifanche-private",
                thread_ts="1774999116.837699",
                message_ts="9999999999.000001",
                author_actor_id="U123",
                text="fresh user reply",
            ),
        ],
    )
    orchestrator = RecordingOrchestrator()

    run_poll_cycle(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
    )

    assert orchestrator.reply_calls == [
        {
            "workspace_name": "oracle",
            "channel_name": "yifanche-private",
            "thread_ts": "1774999116.837699",
            "message_ts": "9999999999.000001",
            "author_actor_id": "U123",
            "text": "fresh user reply",
        }
    ]


def test_run_poll_cycle_ignores_bob_prefixed_thread_messages_even_when_timestamp_is_unseen(tmp_path):
    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        workspaces=[
            WorkspaceConfig(
                name="oracle",
                allowed_actor_ids=["U123"],
                channels=[ChannelConfig(name="yifanche-private")],
            )
        ],
    )
    state.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1774999116.837699",
        root_ts="1774999116.837699",
        codex_session_id="session-123",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )

    browser = FakePollingBrowser()
    browser.set_thread_replies(
        "oracle",
        "yifanche-private",
        "1774999116.837699",
        [
            SlackThreadReplyMessage(
                workspace_name="oracle",
                channel_name="yifanche-private",
                thread_ts="1774999116.837699",
                message_ts="9999999999.000001",
                author_actor_id="U123",
                text="codex Bob: hi",
            ),
            SlackThreadReplyMessage(
                workspace_name="oracle",
                channel_name="yifanche-private",
                thread_ts="1774999116.837699",
                message_ts="9999999999.000002",
                author_actor_id="U123",
                text="Bob hit an error: example",
            ),
        ],
    )
    orchestrator = RecordingOrchestrator()

    run_poll_cycle(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
    )

    assert orchestrator.reply_calls == []


def test_run_poll_cycle_does_not_fetch_thread_replies_for_running_sessions(tmp_path):
    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        workspaces=[
            WorkspaceConfig(
                name="oracle",
                allowed_actor_ids=["U123"],
                channels=[ChannelConfig(name="yifanche-private")],
            )
        ],
    )
    state.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1775023074.173999",
        root_ts="1775023074.173999",
        codex_session_id="session-running",
        cwd=str(tmp_path),
        owner_actor_id="U123",
        status=SessionStatus.RUNNING,
    )

    browser = FakePollingBrowser()
    orchestrator = RecordingOrchestrator()

    run_poll_cycle(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=config,
    )

    assert browser.thread_list_calls == []
    assert orchestrator.reply_calls == []


def test_agent_parser_exposes_poll_interval_flag_with_default():
    args = agent_module.build_parser().parse_args([])
    assert args.poll_interval_seconds == 30.0

    overridden = agent_module.build_parser().parse_args(["--poll-interval-seconds", "5"])
    assert overridden.poll_interval_seconds == 5.0


def test_agent_parser_uses_environment_default_poll_interval(monkeypatch):
    monkeypatch.setenv("BOB_POLL_INTERVAL_SECONDS", "11")
    args = agent_module.build_parser().parse_args([])
    assert args.poll_interval_seconds == 11.0


def test_run_poll_loop_repeats_until_interrupted(tmp_path, monkeypatch):
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        workspaces=[
            WorkspaceConfig(
                name="oracle",
                allowed_actor_ids=["U123"],
                channels=[ChannelConfig(name="yifanche-private")],
            )
        ],
    )
    calls = {"poll": 0, "sleep": []}

    def fake_poll_cycle(browser, orchestrator, state_store, config, logger=None):
        calls["poll"] += 1
        if calls["poll"] == 3:
            raise KeyboardInterrupt()

    monkeypatch.setattr(agent_module, "run_poll_cycle", fake_poll_cycle)

    stop_request_path = tmp_path / "bob.stop"
    lock_file = tmp_path / "bob.lock"
    pid_file = tmp_path / "bob.pid"
    agent_module.run_poll_loop(
        browser=object(),
        orchestrator=object(),
        state_store=object(),
        config=config,
        poll_interval_seconds=7.5,
        lock_file=lock_file,
        pid_file=pid_file,
        stop_request_path=stop_request_path,
        sleep_fn=calls["sleep"].append,
    )

    assert calls["poll"] == 3
    assert calls["sleep"]
    assert all(duration <= 1.0 for duration in calls["sleep"])


def test_run_poll_loop_stops_when_stop_request_file_exists(tmp_path, monkeypatch):
    config = AppConfig(
        defaults=DefaultSettings(default_cwd=str(tmp_path), allowed_actor_ids=["U123"]),
        workspaces=[
            WorkspaceConfig(
                name="oracle",
                allowed_actor_ids=["U123"],
                channels=[ChannelConfig(name="yifanche-private")],
            )
        ],
    )
    calls = {"poll": 0, "sleep": []}
    stop_request_path = tmp_path / "bob.stop"
    lock_file = tmp_path / "bob.lock"
    pid_file = tmp_path / "bob.pid"

    def fake_poll_cycle(browser, orchestrator, state_store, config, logger=None):
        calls["poll"] += 1
        stop_request_path.write_text("stop\n", encoding="utf-8")

    monkeypatch.setattr(agent_module, "run_poll_cycle", fake_poll_cycle)

    agent_module.run_poll_loop(
        browser=object(),
        orchestrator=object(),
        state_store=object(),
        config=config,
        poll_interval_seconds=7.5,
        lock_file=lock_file,
        pid_file=pid_file,
        stop_request_path=stop_request_path,
        sleep_fn=calls["sleep"].append,
    )

    assert calls["poll"] == 1
    assert lock_file.exists()
    assert pid_file.exists()
