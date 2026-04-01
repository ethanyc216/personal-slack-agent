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
    assert "default_cwd" in contents
    assert "allowed_actor_ids" in contents
    assert "slack_signin_url" in contents
    assert "browser_mode" in contents
    assert "browser_url" in contents
    assert "cdp_url" in contents
    assert "chrome_executable_path" in contents
    assert "browser_user_data_dir" in contents
    assert 'slack_url = "https://app.slack.com/client/T12345678/C12345678"' in contents
    assert load_config(config_file).defaults.allowed_actor_ids


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
