from personal_slack_agent.cli.init_cmd import main as init_main
from personal_slack_agent.config import load_config
from personal_slack_agent.paths import default_config_file


def test_init_creates_config_directory_and_starter_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    exit_code = init_main([])

    config_file = default_config_file()
    assert exit_code == 0
    assert config_file.exists()
    assert config_file.parent.exists()

    contents = config_file.read_text(encoding="utf-8")
    assert "[defaults]" in contents
    assert "[browser]" in contents
    assert "[runner]" in contents
    assert "[lifecycle]" in contents
    assert "[orchestrator]" in contents
    assert "[watcher]" in contents
    assert "[workspaces.channel_defaults]" in contents
    assert "default_cwd" in contents
    assert "allowed_actor_ids" in contents
    assert "max_concurrent_tasks" in contents
    assert "root_batch_size" in contents
    assert "slack_signin_url" in contents
    assert "browser_mode" in contents
    assert "browser_url" in contents
    assert "cdp_url" in contents
    assert "chrome_executable_path" in contents
    assert "browser_user_data_dir" in contents
    assert "bob_codex_home" in contents
    assert "codex_exec_timeout_seconds" in contents
    assert "reminder_minutes" in contents
    assert "auto_close_minutes" in contents
    assert '# allowed_actor_ids = ["U01234567"]' in contents
    assert '# slack_url = "https://app.slack.com/client/T12345678/C12345678"' in contents
    assert '# name = "your-private-channel"' in contents
    assert load_config(config_file).workspaces == []


def test_init_refuses_to_overwrite_existing_file_without_force(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = default_config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("original", encoding="utf-8")

    exit_code = init_main([])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "already exists" in captured.err.lower()
    assert config_file.read_text(encoding="utf-8") == "original"


def test_init_force_overwrites_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = default_config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("original", encoding="utf-8")

    exit_code = init_main(["--force"])

    assert exit_code == 0
    assert config_file.read_text(encoding="utf-8") != "original"


def test_discover_slack_auth_updates_workspace_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / "bob.toml"
    config_file.write_text(
        "\n".join(
            [
                "[defaults]",
                'default_cwd = "{0}"'.format(tmp_path),
                'allowed_actor_ids = ["U123"]',
                "",
                "[[workspaces]]",
                'name = "bob_company"',
                'slack_url = "https://app.slack.com/client/T12345678/C12345678"',
            ]
        ),
        encoding="utf-8",
    )

    class FakeAdapter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_workspace_urls(self, workspace_urls):
            self.workspace_urls = dict(workspace_urls)

        def discover_api_session(self, workspace_name):
            assert workspace_name == "bob_company"
            return ("xoxc-demo-token", "https://example.enterprise.slack.com")

        def close(self):
            return None

    monkeypatch.setattr("personal_slack_agent.cli.init_cmd.PlaywrightSlackAdapter", FakeAdapter)

    exit_code = init_main(
        [
            "--discover-slack-auth",
            "--workspace",
            "bob_company",
            "--config",
            str(config_file),
        ]
    )

    assert exit_code == 0
    updated = load_config(config_file)
    assert updated.workspaces[0].slack_api_origin == "https://example.enterprise.slack.com"
    assert updated.workspaces[0].slack_api_token == "xoxc-demo-token"


def test_discover_slack_auth_requires_workspace_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / "bob.toml"
    config_file.write_text(
        "\n".join(
            [
                "[defaults]",
                'default_cwd = "{0}"'.format(tmp_path),
                'allowed_actor_ids = ["U123"]',
            ]
        ),
        encoding="utf-8",
    )

    exit_code = init_main(["--discover-slack-auth", "--config", str(config_file)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "workspace" in captured.err.lower()
