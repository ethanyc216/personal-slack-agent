import pytest

from personal_slack_agent.cli import ctl as ctl_module
from personal_slack_agent.cli.agent import build_parser as build_agent_parser
from personal_slack_agent.cli.agent import main as agent_main
from personal_slack_agent.cli.ctl import build_parser as build_ctl_parser
from personal_slack_agent.cli.ctl import main as ctl_main
from personal_slack_agent.cli.wrapper import build_parser as build_wrapper_parser
from personal_slack_agent.cli.wrapper import main as wrapper_main
from personal_slack_agent.paths import default_config_file


def test_bobctl_requires_a_subcommand():
    with pytest.raises(SystemExit) as exc_info:
        build_ctl_parser().parse_args([])

    assert exc_info.value.code == 2


def test_bobctl_start_returns_success(capsys, monkeypatch):
    monkeypatch.setenv("HOME", "/tmp/test-bobctl-start")

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            self.pid = 1001

    monkeypatch.setattr(ctl_module.subprocess, "Popen", FakePopen)

    exit_code = ctl_main(["start"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "started" in captured.out.lower()


def test_agent_bootstrap_still_runs(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_file = tmp_path / ".config" / "personal-slack-agent" / "bob.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        "\n".join(
            [
                "[defaults]",
                'default_cwd = "{0}"'.format(project_dir),
                'allowed_actor_ids = ["U123"]',
            ]
        ),
        encoding="utf-8",
    )

    def fake_run_runtime(config_path, once, poll_interval_seconds):
        return 0

    monkeypatch.setattr("personal_slack_agent.cli.agent._run_runtime", fake_run_runtime)
    assert agent_main([]) == 0


def test_wrapper_runs_terminal_request_with_explicit_target(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_file = tmp_path / ".config" / "personal-slack-agent" / "bob.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        "\n".join(
            [
                "[defaults]",
                'default_cwd = "{0}"'.format(project_dir),
                'allowed_actor_ids = ["U123"]',
                "",
                "[[workspaces]]",
                'name = "oracle"',
                'allowed_actor_ids = ["U123"]',
                "",
                "[[workspaces.channels]]",
                'name = "yifanche-bob"',
                'persistent_memory_mode = "disabled"',
                "post_terminal_threads_here = true",
            ]
        ),
        encoding="utf-8",
    )

    calls = {}

    def fake_run_smoke_test(*, paths, workspace_name, channel_name, text, timeout_seconds, poll_interval_seconds):
        calls["workspace_name"] = workspace_name
        calls["channel_name"] = channel_name
        calls["text"] = text
        calls["timeout_seconds"] = timeout_seconds
        calls["poll_interval_seconds"] = poll_interval_seconds
        return {
            "thread_ts": "1775718000.000001",
            "session_id": "session-123",
            "final_message": "_*codex Bob :white_check_mark::*_ wrapper ok",
        }

    monkeypatch.setattr("personal_slack_agent.cli.wrapper._run_smoke_test", fake_run_smoke_test)

    exit_code = wrapper_main(
        [
            "--workspace",
            "oracle",
            "--channel",
            "yifanche-bob",
            "--timeout-seconds",
            "20",
            "--poll-interval-seconds",
            "2",
            "please",
            "help",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == {
        "workspace_name": "oracle",
        "channel_name": "yifanche-bob",
        "text": "Bob, please help",
        "timeout_seconds": 20.0,
        "poll_interval_seconds": 2.0,
    }
    assert "wrapper ok" in captured.out


def test_wrapper_uses_unique_terminal_channel_when_not_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_file = tmp_path / ".config" / "personal-slack-agent" / "bob.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        "\n".join(
            [
                "[defaults]",
                'default_cwd = "{0}"'.format(project_dir),
                'allowed_actor_ids = ["U123"]',
                "",
                "[[workspaces]]",
                'name = "oracle"',
                'allowed_actor_ids = ["U123"]',
                "",
                "[[workspaces.channels]]",
                'name = "yifanche-bob"',
                'persistent_memory_mode = "disabled"',
                "post_terminal_threads_here = true",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "personal_slack_agent.cli.wrapper._run_smoke_test",
        lambda **kwargs: {
            "thread_ts": "1775718000.000001",
            "session_id": "session-123",
            "final_message": "done",
        },
    )

    assert wrapper_main(["hello"]) == 0


def test_agent_default_config_path_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    args = build_agent_parser().parse_args([])

    assert args.config == str(default_config_file())


def test_wrapper_uses_longer_default_timeout():
    args = build_wrapper_parser().parse_args(["hello"])

    assert args.timeout_seconds == 1800.0
