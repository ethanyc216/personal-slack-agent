from personal_slack_agent.paths import default_log_file, default_state_dir


def test_default_paths_use_bob_state_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    state_dir = default_state_dir()
    log_file = default_log_file()

    assert str(state_dir).endswith(".local/share/personal-slack-agent")
    assert str(log_file).endswith(".local/share/personal-slack-agent/logs/bob.log")
