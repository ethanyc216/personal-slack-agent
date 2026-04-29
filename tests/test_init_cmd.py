from personal_slack_agent.cli.init_cmd import main as init_main
from personal_slack_agent.config import load_config
from personal_slack_agent.paths import default_config_file


def test_init_runs_interactive_wizard_and_writes_usable_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    answers = iter(
        [
            "Bob Owner",
            "Owner",
            "my-workspace",
            "https://app.slack.com/client/T12345678/C12345678",
            "U12345678, U87654321",
            "my-private-channel",
            "",
            str(project_dir),
            "owner_only",
            "bob_owner_handle",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    exit_code = init_main([])
    captured = capsys.readouterr()

    config_file = default_config_file()
    assert exit_code == 0
    assert config_file.exists()
    assert config_file.parent.exists()
    assert str(config_file) in captured.out
    assert "bob-init --discover-slack-auth --workspace my-workspace" in captured.out
    assert "bobctl doctor" in captured.out

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
    assert "browser_user_data_dir" in contents
    assert "bob_codex_home" in contents
    assert "codex_exec_timeout_seconds" in contents
    assert "reminder_minutes" in contents
    assert "auto_close_minutes" in contents
    assert 'owner_name = "Bob Owner"' in contents
    assert 'owner_preferred_name = "Owner"' in contents
    assert 'slack_url = "https://app.slack.com/client/T12345678/C12345678"' in contents
    assert 'allowed_actor_ids = ["U12345678", "U87654321"]' in contents
    assert 'persistent_memory_mode = "owner_only"' in contents
    assert 'persistent_memory_owner = "bob_owner_handle"' in contents

    config = load_config(config_file)
    assert config.defaults.owner_name == "Bob Owner"
    assert config.defaults.owner_preferred_name == "Owner"
    assert config.workspaces[0].name == "my-workspace"
    assert config.workspaces[0].channel_defaults.allowed_actor_ids == [
        "U12345678",
        "U87654321",
    ]
    assert config.workspaces[0].channels[0].name == "my-private-channel"
    assert config.workspaces[0].channels[0].effective_default_cwd == str(project_dir.resolve())
    assert config.workspaces[0].channels[0].effective_persistent_memory_mode == "owner_only"
    assert config.workspaces[0].channels[0].effective_persistent_memory_owner == "bob_owner_handle"
    assert config.workspaces[0].channels[0].effective_post_terminal_threads_here is True


def test_init_wizard_can_generate_disabled_memory_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    answers = iter(
        [
            "Bob Owner",
            "Owner",
            "workspace",
            "https://app.slack.com/client/T12345678/C12345678",
            "",
            "shared-bob",
            "C12345678",
            str(project_dir),
            "disabled",
            "n",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    exit_code = init_main([])

    assert exit_code == 0
    config = load_config(default_config_file())
    channel = config.workspaces[0].channels[0]
    assert config.workspaces[0].channel_defaults.allowed_actor_ids == []
    assert channel.slack_channel_id == "C12345678"
    assert channel.persistent_memory_mode == "disabled"
    assert channel.persistent_memory_owner is None
    assert channel.post_terminal_threads_here is False


def test_init_wizard_reprompts_for_invalid_default_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    answers = iter(
        [
            "Bob Owner",
            "Owner",
            "workspace",
            "https://app.slack.com/client/T12345678/C12345678",
            "",
            "bob-channel",
            "",
            str(tmp_path / "missing"),
            str(project_dir),
            "disabled",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    exit_code = init_main([])

    assert exit_code == 0
    config = load_config(default_config_file())
    assert config.workspaces[0].channels[0].effective_default_cwd == str(project_dir.resolve())


def test_init_wizard_uses_neutral_defaults_instead_of_local_username(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USER", "local_personal_handle")
    monkeypatch.setenv("LOGNAME", "local_personal_handle")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    prompts = []
    answers = iter(
        [
            "",
            "",
            "my-workspace",
            "https://app.slack.com/client/T12345678/C12345678",
            "",
            "my-private-channel",
            "",
            "",
            "owner_only",
            "",
            "",
        ]
    )
    monkeypatch.chdir(project_dir)

    def answer(prompt=""):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)

    exit_code = init_main([])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "local_personal_handle" not in captured.out
    config = load_config(default_config_file())
    assert config.defaults.owner_name == "Bob Owner"
    assert config.defaults.owner_preferred_name == "Owner"
    assert config.workspaces[0].channels[0].persistent_memory_owner == "bob_owner_handle"
    assert config.workspaces[0].channels[0].effective_default_cwd == str(project_dir.resolve())
    assert "Default working directory [.]: " in prompts


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
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    answers = iter(
        [
            "Bob Owner",
            "Owner",
            "workspace",
            "https://app.slack.com/client/T12345678/C12345678",
            "",
            "bob-channel",
            "",
            str(project_dir),
            "disabled",
            "",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

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
