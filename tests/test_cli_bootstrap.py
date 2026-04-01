import pytest

from personal_slack_agent.cli import ctl as ctl_module
from personal_slack_agent.cli.agent import build_parser as build_agent_parser
from personal_slack_agent.cli.agent import main as agent_main
from personal_slack_agent.cli.ctl import build_parser as build_ctl_parser
from personal_slack_agent.cli.ctl import main as ctl_main
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


def test_other_bootstrap_clis_fail_loudly_until_implemented(capsys, monkeypatch, tmp_path):
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
    assert wrapper_main(["--once"]) == 2

    captured = capsys.readouterr()
    assert captured.err.lower().count("not implemented") >= 1


def test_agent_default_config_path_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    args = build_agent_parser().parse_args([])

    assert args.config == str(default_config_file())
