from pathlib import Path
import sys

import pytest

from personal_slack_agent.cli.agent import main as agent_main
from personal_slack_agent.cli import ctl as ctl_module
from personal_slack_agent.cli.ctl import build_runtime_paths
from personal_slack_agent.cli.ctl import main as ctl_main
from personal_slack_agent.lock import SingleInstanceLockError
from personal_slack_agent.lock import acquire_single_instance_lock


def test_build_runtime_paths_uses_state_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    paths = build_runtime_paths()

    assert str(paths.lock_file).endswith("personal-slack-agent/bob.lock")
    assert str(paths.pid_file).endswith("personal-slack-agent/bob.pid")
    assert str(paths.log_file).endswith("personal-slack-agent/logs/bob.log")


def test_status_reports_not_running_when_lock_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code = ctl_main(["status"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "not running" in captured.out.lower()


def test_status_reports_running_from_pid_file_when_lock_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    paths = build_runtime_paths()
    paths.pid_file.parent.mkdir(parents=True, exist_ok=True)
    paths.pid_file.write_text("43210", encoding="utf-8")
    monkeypatch.setattr(ctl_module, "_is_pid_running", lambda pid: pid == 43210)

    exit_code = ctl_main(["status"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "running" in captured.out.lower()


def test_doctor_prints_runtime_paths(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code = ctl_main(["doctor"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "state_dir" in captured.out
    assert "log_file" in captured.out
    assert "lock_file" in captured.out
    assert "pid_file" in captured.out
    assert str(tmp_path / ".local" / "share" / "personal-slack-agent") in captured.out


def test_doctor_reports_config_and_cdp_health(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace_root = tmp_path / "work"
    workspace_root.mkdir()
    config_file = tmp_path / ".config" / "personal-slack-agent" / "bob.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        "\n".join(
            [
                "[defaults]",
                'default_cwd = "{0}"'.format(workspace_root),
                'allowed_actor_ids = ["U123"]',
                'cdp_url = "http://127.0.0.1:9222"',
                "",
                "[[workspaces]]",
                'name = "oracle"',
                'allowed_actor_ids = ["U123"]',
                'slack_url = "https://app.slack.com/client/T12345678/C12345678"',
                "",
                "[[workspaces.channels]]",
                'name = "yifanche-bob"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ctl_module, "_is_cdp_reachable", lambda url: url == "http://127.0.0.1:9222")

    exit_code = ctl_main(["doctor"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "config_loaded: True" in captured.out
    assert "cdp_url: http://127.0.0.1:9222" in captured.out
    assert "cdp_reachable: True" in captured.out
    assert "workspace_count: 1" in captured.out
    assert "channel_count: 1" in captured.out
    assert "oracle:yifanche-bob" in captured.out


def test_smoke_test_reports_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / ".config" / "personal-slack-agent" / "bob.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[defaults]\nallowed_actor_ids=[\"U123\"]\n", encoding="utf-8")

    calls = {}

    def fake_run_smoke_test(*, paths, workspace_name, channel_name, text, timeout_seconds, poll_interval_seconds):
        calls["workspace_name"] = workspace_name
        calls["channel_name"] = channel_name
        calls["text"] = text
        calls["timeout_seconds"] = timeout_seconds
        calls["poll_interval_seconds"] = poll_interval_seconds
        return {
            "thread_ts": "1775717794.417429",
            "session_id": "session-123",
            "final_message": "_*codex Bob :white_check_mark::*_ smoke ok",
        }

    monkeypatch.setattr(ctl_module, "_run_smoke_test", fake_run_smoke_test)

    exit_code = ctl_main(
        [
            "smoke-test",
            "--workspace",
            "oracle",
            "--channel",
            "yifanche-bob",
            "--text",
            "Bob, smoke ok",
            "--timeout-seconds",
            "20",
            "--poll-interval-seconds",
            "2",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls == {
        "workspace_name": "oracle",
        "channel_name": "yifanche-bob",
        "text": "Bob, smoke ok",
        "timeout_seconds": 20.0,
        "poll_interval_seconds": 2.0,
    }
    assert "smoke test passed" in captured.out.lower()
    assert "1775717794.417429" in captured.out
    assert "session-123" in captured.out


def test_wait_for_smoke_result_returns_session_and_final_message(tmp_path):
    paths = build_runtime_paths(state_dir=tmp_path / "state", config_file=tmp_path / "bob.toml")
    store = ctl_module.BobStateStore(paths.state_dir / "bob.sqlite3")
    store.initialize()
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-bob",
        thread_ts="1775717794.417429",
        root_ts="1775717794.417429",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=ctl_module.SessionStatus.CLOSED_IDLE,
    )
    store.upsert_outbound_intent(
        workspace_name="oracle",
        channel_name="yifanche-bob",
        thread_ts="1775717794.417429",
        intent_key="final-session-123",
        action="post_thread_reply",
        text="_*codex Bob :white_check_mark::*_ smoke ok",
        delivered=True,
        message_ts="1775717816.033009",
    )

    result = ctl_module._wait_for_smoke_result(
        paths=paths,
        workspace_name="oracle",
        channel_name="yifanche-bob",
        thread_ts="1775717794.417429",
        timeout_seconds=1.0,
        poll_interval_seconds=0.01,
        sleep_fn=lambda _seconds: None,
    )

    assert result["thread_ts"] == "1775717794.417429"
    assert result["session_id"] == "session-123"
    assert result["final_message"] == "_*codex Bob :white_check_mark::*_ smoke ok"


def test_tail_log_prints_useful_message_when_log_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code = ctl_main(["tail-log"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "no log file found" in captured.out.lower()


def test_tail_log_prints_recent_lines(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    paths = build_runtime_paths()
    paths.log_file.parent.mkdir(parents=True, exist_ok=True)
    paths.log_file.write_text(
        "\n".join("line-{0}".format(index) for index in range(1, 61)),
        encoding="utf-8",
    )

    exit_code = ctl_main(["tail-log"])
    captured = capsys.readouterr()

    assert exit_code == 0
    rendered = "\n{0}\n".format(captured.out.strip())
    assert "\nline-60\n" in rendered
    assert "\nline-1\n" not in rendered


def test_show_config_prints_path_and_contents_when_file_exists(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / ".config" / "personal-slack-agent" / "bob.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[defaults]\nallowed_actor_ids=[\"U123\"]\n", encoding="utf-8")

    exit_code = ctl_main(["show-config"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert str(config_file) in captured.out
    assert "allowed_actor_ids" in captured.out


def test_show_config_redacts_slack_api_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / ".config" / "personal-slack-agent" / "bob.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        "[defaults]\nallowed_actor_ids=[\"U123\"]\nslack_api_token=\"xoxc-secret\"\n",
        encoding="utf-8",
    )

    exit_code = ctl_main(["show-config"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "***REDACTED***" in captured.out
    assert "xoxc-secret" not in captured.out


def test_show_config_prints_not_found_message_when_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code = ctl_main(["show-config"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "not found" in captured.out.lower()
    assert str(tmp_path / ".config" / "personal-slack-agent" / "bob.toml") in captured.out


def test_agent_once_loads_config_and_initializes_runtime_files(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_file = tmp_path / "bob.toml"
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

    exit_code = agent_main(["--config", str(config_file), "--once"])

    assert exit_code == 0
    paths = build_runtime_paths()
    assert paths.log_file.exists()
    assert paths.lock_file.exists()
    assert not paths.pid_file.exists()


def test_acquire_single_instance_lock_rejects_second_lock(tmp_path):
    lock_file = tmp_path / "bob.lock"
    lock_handle = acquire_single_instance_lock(lock_file)
    try:
        with pytest.raises(SingleInstanceLockError):
            acquire_single_instance_lock(lock_file)
    finally:
        lock_handle.close()


def test_start_spawns_bob_agent_process_with_config_and_interval(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / ".config" / "personal-slack-agent" / "bob.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[defaults]\nallowed_actor_ids=[\"U123\"]\n", encoding="utf-8")

    calls = {}
    paths = build_runtime_paths()

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            calls["cmd"] = list(cmd)
            calls["kwargs"] = dict(kwargs)
            self.pid = 22222
            paths.pid_file.parent.mkdir(parents=True, exist_ok=True)
            paths.pid_file.write_text(str(self.pid), encoding="utf-8")

    monkeypatch.setattr(ctl_module.subprocess, "Popen", FakePopen)

    exit_code = ctl_main(["start", "--poll-interval-seconds", "12"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert calls["cmd"] == [
        str(Path(sys.executable)),
        "-m",
        "personal_slack_agent.cli.agent",
        "--config",
        str(config_file),
        "--poll-interval-seconds",
        "12.0",
    ]
    assert calls["kwargs"]["start_new_session"] is True
    assert "started" in captured.out.lower()


def test_restart_stops_then_starts_with_same_config_and_interval(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / ".config" / "personal-slack-agent" / "bob.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[defaults]\nallowed_actor_ids=[\"U123\"]\n", encoding="utf-8")
    paths = build_runtime_paths()
    paths.pid_file.parent.mkdir(parents=True, exist_ok=True)
    paths.pid_file.write_text("43210", encoding="utf-8")

    spawned = {}
    running_states = iter([True, False, False])

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            spawned["cmd"] = list(cmd)
            spawned["kwargs"] = dict(kwargs)
            self.pid = 33333
            paths.pid_file.write_text(str(self.pid), encoding="utf-8")

    monkeypatch.setattr(ctl_module, "_is_pid_running", lambda pid: next(running_states, False))
    monkeypatch.setattr(ctl_module.subprocess, "Popen", FakePopen)

    exit_code = ctl_main(["restart", "--poll-interval-seconds", "12"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "stopped" in captured.out.lower()
    assert "started" in captured.out.lower()
    assert spawned["cmd"] == [
        str(Path(sys.executable)),
        "-m",
        "personal_slack_agent.cli.agent",
        "--config",
        str(config_file),
        "--poll-interval-seconds",
        "12.0",
    ]


def test_start_removes_stale_stop_request_file_before_spawn(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / ".config" / "personal-slack-agent" / "bob.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("[defaults]\nallowed_actor_ids=[\"U123\"]\n", encoding="utf-8")
    paths = build_runtime_paths()
    paths.stop_request_file.parent.mkdir(parents=True, exist_ok=True)
    paths.stop_request_file.write_text("stop\n", encoding="utf-8")

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            self.pid = 33333
            paths.pid_file.parent.mkdir(parents=True, exist_ok=True)
            paths.pid_file.write_text(str(self.pid), encoding="utf-8")

    monkeypatch.setattr(ctl_module.subprocess, "Popen", FakePopen)

    exit_code = ctl_main(["start"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert not paths.stop_request_file.exists()
    assert "started" in captured.out.lower()


def test_stop_requests_cooperative_shutdown_for_running_lock_pid(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    paths = build_runtime_paths()
    paths.lock_file.parent.mkdir(parents=True, exist_ok=True)
    paths.lock_file.write_text("43210", encoding="utf-8")
    paths.pid_file.write_text("43210", encoding="utf-8")

    monkeypatch.setattr(ctl_module, "_is_pid_running", lambda pid: True)

    exit_code = ctl_main(["stop"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert paths.stop_request_file.exists()
    assert "requested" in captured.out.lower()


def test_stop_removes_stale_lock_pid(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    paths = build_runtime_paths()
    paths.lock_file.parent.mkdir(parents=True, exist_ok=True)
    paths.lock_file.write_text("54321", encoding="utf-8")
    paths.pid_file.write_text("54321", encoding="utf-8")

    monkeypatch.setattr(ctl_module, "_is_pid_running", lambda pid: False)

    exit_code = ctl_main(["stop"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert not paths.lock_file.exists()
    assert not paths.pid_file.exists()
    assert "stale lock" in captured.out.lower()
