from dataclasses import asdict

from personal_slack_agent.cli import agent as agent_module
from personal_slack_agent.cli.agent import run_once
from personal_slack_agent.models import (
    ChannelConfig,
    DefaultSettings,
    WorkspaceConfig,
)
from personal_slack_agent.slack import SlackRootMessage, SlackThreadReplyMessage


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


def test_run_once_builds_runtime_stack_and_executes_watcher_cycle(tmp_path, monkeypatch):
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
                'persistent_memory_mode = "owner_only"',
                'persistent_memory_owner = "yifanche"',
            ]
        ),
        encoding="utf-8",
    )

    calls = {
        "cycle": 0,
        "workspace_urls": None,
        "workspace_api_contexts": None,
        "channel_urls": None,
        "runner_kwargs": None,
    }

    class FakeBrowser:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_workspace_urls(self, workspace_urls):
            calls["workspace_urls"] = dict(workspace_urls)

        def set_workspace_api_contexts(self, workspace_api_contexts):
            calls["workspace_api_contexts"] = dict(workspace_api_contexts)

        def set_channel_urls(self, channel_urls):
            calls["channel_urls"] = dict(channel_urls)

        def close(self):
            return None

    class FakeRunner:
        def __init__(self, **kwargs):
            calls["runner_kwargs"] = kwargs

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def process_scheduled_actions(self):
            return None

    class FakeWatcher:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_cycle(self):
            calls["cycle"] += 1

    monkeypatch.setattr(agent_module, "PlaywrightSlackAdapter", FakeBrowser)
    monkeypatch.setattr(agent_module, "SubprocessCodexRunner", FakeRunner)
    monkeypatch.setattr(agent_module, "BobOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(agent_module, "SlackWatcher", FakeWatcher)

    exit_code = run_once(config_file)

    assert exit_code == 0
    assert calls["cycle"] == 1
    assert calls["workspace_urls"] == {
        "oracle": "https://app.slack.com/client/T12345678/C12345678"
    }
    assert calls["workspace_api_contexts"] == {}
    assert calls["channel_urls"] == {}
    assert calls["runner_kwargs"]["env_overrides"]["CODEX_HOME"].endswith("/codex-home")


def test_prepare_bob_codex_home_links_config_without_hooks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text('model = "gpt-5.4"\n', encoding="utf-8")
    (codex_home / "hooks.json").write_text('{"hooks":{}}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    bob_home = agent_module._prepare_bob_codex_home(tmp_path / "state" / "codex-home")

    assert bob_home == tmp_path / "state" / "codex-home"
    assert (bob_home / "config.toml").exists()
    assert (bob_home / "config.toml").read_text(encoding="utf-8") == 'model = "gpt-5.4"\n'
    assert not (bob_home / "hooks.json").exists()


def test_run_once_uses_configured_bob_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / "bob.toml"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    configured_bob_home = tmp_path / "custom-bob-codex-home"
    config_file.write_text(
        "\n".join(
            [
                "[defaults]",
                'default_cwd = "{0}"'.format(workspace_root),
                'allowed_actor_ids = ["U123"]',
                'browser_mode = "shared_browser"',
                'bob_codex_home = "{0}"'.format(configured_bob_home),
                "",
                "[[workspaces]]",
                'name = "oracle"',
                'allowed_actor_ids = ["U123"]',
                'slack_url = "https://app.slack.com/client/T12345678/C12345678"',
                "",
                "[[workspaces.channels]]",
                'name = "yifanche-private"',
                'persistent_memory_mode = "owner_only"',
                'persistent_memory_owner = "yifanche"',
            ]
        ),
        encoding="utf-8",
    )

    calls = {"runner_kwargs": None}

    class FakeBrowser:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_workspace_urls(self, workspace_urls):
            return None

        def set_workspace_api_contexts(self, workspace_api_contexts):
            return None

        def set_channel_urls(self, channel_urls):
            return None

        def close(self):
            return None

    class FakeRunner:
        def __init__(self, **kwargs):
            calls["runner_kwargs"] = kwargs

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def process_scheduled_actions(self):
            return None

    class FakeWatcher:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_cycle(self):
            return None

    monkeypatch.setattr(agent_module, "PlaywrightSlackAdapter", FakeBrowser)
    monkeypatch.setattr(agent_module, "SubprocessCodexRunner", FakeRunner)
    monkeypatch.setattr(agent_module, "BobOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(agent_module, "SlackWatcher", FakeWatcher)

    exit_code = run_once(config_file)

    assert exit_code == 0
    assert calls["runner_kwargs"]["env_overrides"]["CODEX_HOME"] == str(
        configured_bob_home.resolve()
    )


def test_run_once_seeds_explicit_channel_urls_when_channel_ids_are_configured(tmp_path, monkeypatch):
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
                'slack_url = "https://app.slack.com/client/E655JKQRX/C040C3N43B8"',
                "",
                "[[workspaces.channels]]",
                'name = "yifanche-private"',
                'persistent_memory_mode = "owner_only"',
                'persistent_memory_owner = "yifanche"',
                "",
                "[[workspaces.channels]]",
                'name = "yifanche-bob-test"',
                'persistent_memory_mode = "disabled"',
                'slack_channel_id = "C0AS82WLCBU"',
            ]
        ),
        encoding="utf-8",
    )

    calls = {"channel_urls": None}

    class FakeBrowser:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_workspace_urls(self, workspace_urls):
            return None

        def set_workspace_api_contexts(self, workspace_api_contexts):
            return None

        def set_channel_urls(self, channel_urls):
            calls["channel_urls"] = dict(channel_urls)

        def close(self):
            return None

    class FakeRunner:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def process_scheduled_actions(self):
            return None

    class FakeWatcher:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_cycle(self):
            return None

    monkeypatch.setattr(agent_module, "PlaywrightSlackAdapter", FakeBrowser)
    monkeypatch.setattr(agent_module, "SubprocessCodexRunner", FakeRunner)
    monkeypatch.setattr(agent_module, "BobOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(agent_module, "SlackWatcher", FakeWatcher)

    exit_code = run_once(config_file)

    assert exit_code == 0
    assert calls["channel_urls"] == {
        ("oracle", "yifanche-bob-test"): "https://app.slack.com/client/E655JKQRX/C0AS82WLCBU"
    }


def test_agent_parser_exposes_poll_interval_flag_with_default():
    args = agent_module.build_parser().parse_args([])
    assert args.poll_interval_seconds == 30.0

    overridden = agent_module.build_parser().parse_args(["--poll-interval-seconds", "5"])
    assert overridden.poll_interval_seconds == 5.0


def test_agent_parser_uses_environment_default_poll_interval(monkeypatch):
    monkeypatch.setenv("BOB_POLL_INTERVAL_SECONDS", "11")
    args = agent_module.build_parser().parse_args([])
    assert args.poll_interval_seconds == 11.0


def test_run_poll_loop_repeats_until_interrupted(tmp_path):
    calls = {"cycle": 0, "sleep": []}
    stop_request_path = tmp_path / "bob.stop"
    lock_file = tmp_path / "bob.lock"
    pid_file = tmp_path / "bob.pid"

    class FakeWatcher:
        def run_cycle(self):
            calls["cycle"] += 1
            if calls["cycle"] == 3:
                raise KeyboardInterrupt()

    class FakeOrchestrator:
        def process_scheduled_actions(self):
            return None

    agent_module.run_poll_loop(
        watcher=FakeWatcher(),
        orchestrator=FakeOrchestrator(),
        poll_interval_seconds=7.5,
        lock_file=lock_file,
        pid_file=pid_file,
        stop_request_path=stop_request_path,
        sleep_fn=calls["sleep"].append,
    )

    assert calls["cycle"] == 3
    assert calls["sleep"]
    assert all(duration <= 1.0 for duration in calls["sleep"])


def test_run_poll_cycle_processes_scheduled_actions_after_watcher():
    calls = []

    class FakeWatcher:
        def run_cycle(self):
            calls.append("watcher")

    class FakeOrchestrator:
        def process_scheduled_actions(self):
            calls.append("scheduled")

    agent_module.run_poll_cycle(
        watcher=FakeWatcher(),
        orchestrator=FakeOrchestrator(),
    )

    assert calls == ["watcher", "scheduled"]


def test_run_poll_cycle_consumes_reconcile_requests(tmp_path):
    calls = []
    reconcile_file = tmp_path / "bob.reconcile"
    reconcile_file.write_text("oracle\n", encoding="utf-8")

    class FakeWatcher:
        def request_workspace_reconcile(self, workspace_name):
            calls.append(("reconcile", workspace_name))

        def run_cycle(self):
            calls.append(("watcher", None))

    class FakeOrchestrator:
        def process_scheduled_actions(self):
            calls.append(("scheduled", None))

    agent_module.run_poll_cycle(
        watcher=FakeWatcher(),
        orchestrator=FakeOrchestrator(),
        reconcile_request_path=reconcile_file,
    )

    assert calls == [("reconcile", "oracle"), ("watcher", None), ("scheduled", None)]
    assert not reconcile_file.exists()


def test_run_poll_loop_stops_when_stop_request_file_exists(tmp_path):
    calls = {"cycle": 0, "sleep": []}
    stop_request_path = tmp_path / "bob.stop"
    lock_file = tmp_path / "bob.lock"
    pid_file = tmp_path / "bob.pid"

    class FakeWatcher:
        def run_cycle(self):
            calls["cycle"] += 1
            stop_request_path.write_text("stop\n", encoding="utf-8")

    class FakeOrchestrator:
        def process_scheduled_actions(self):
            return None

    agent_module.run_poll_loop(
        watcher=FakeWatcher(),
        orchestrator=FakeOrchestrator(),
        poll_interval_seconds=7.5,
        lock_file=lock_file,
        pid_file=pid_file,
        stop_request_path=stop_request_path,
        sleep_fn=calls["sleep"].append,
    )

    assert calls["cycle"] == 1
    assert lock_file.exists()
    assert pid_file.exists()


def test_run_poll_loop_continues_after_non_interrupt_cycle_error(tmp_path):
    calls = {"cycle": 0, "sleep": []}
    stop_request_path = tmp_path / "bob.stop"
    lock_file = tmp_path / "bob.lock"
    pid_file = tmp_path / "bob.pid"

    class FakeLogger:
        def __init__(self):
            self.exception_messages = []

        def exception(self, message, *args):
            if args:
                self.exception_messages.append(message % args)
                return
            self.exception_messages.append(message)

    logger = FakeLogger()

    class FakeWatcher:
        def run_cycle(self):
            calls["cycle"] += 1
            if calls["cycle"] == 1:
                raise RuntimeError("transient failure")
            if calls["cycle"] == 3:
                raise KeyboardInterrupt()

    class FakeOrchestrator:
        def process_scheduled_actions(self):
            return None

    agent_module.run_poll_loop(
        watcher=FakeWatcher(),
        orchestrator=FakeOrchestrator(),
        poll_interval_seconds=7.5,
        lock_file=lock_file,
        pid_file=pid_file,
        stop_request_path=stop_request_path,
        sleep_fn=calls["sleep"].append,
        logger=logger,
    )

    assert calls["cycle"] == 3
    assert logger.exception_messages == [
        "bob-agent poll cycle failed; continuing after 7.500s"
    ]
