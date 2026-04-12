import pytest

from personal_slack_agent.config import ConfigError, dump_config, load_config


def test_defaults_include_slack_signin_url_when_omitted(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "default-signin.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert (
        config.defaults.slack_signin_url
        == "https://slack.com/signin?entry_point=nav_menu#/signin"
    )
    assert config.defaults.browser_mode == "dedicated_browser"
    assert config.defaults.browser_url == "http://127.0.0.1:9222"
    assert config.defaults.cdp_url == "http://127.0.0.1:9222"
    assert config.defaults.chrome_executable_path is None
    assert config.defaults.browser_user_data_dir is None


def test_defaults_include_browser_fields_when_configured(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    user_data_dir = tmp_path / "chrome-profile"
    chrome_bin = tmp_path / "chrome"
    chrome_bin.write_text("", encoding="utf-8")

    config_path = tmp_path / "browser-config.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        browser_mode = "shared_browser"
        browser_url = "http://127.0.0.1:9222"
        cdp_url = "http://127.0.0.1:9223"
        chrome_executable_path = "{chrome_bin}"
        browser_user_data_dir = "{user_data_dir}"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.browser_mode == "shared_browser"
    assert config.defaults.browser_url == "http://127.0.0.1:9222"
    assert config.defaults.cdp_url == "http://127.0.0.1:9223"
    assert config.defaults.chrome_executable_path == str(chrome_bin.resolve())
    assert config.defaults.browser_user_data_dir == str(user_data_dir.resolve())


def test_defaults_include_bob_codex_home_when_configured(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    bob_codex_home = tmp_path / "bob-codex-home"

    config_path = tmp_path / "bob-codex-home.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        bob_codex_home = "{bob_codex_home}"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.bob_codex_home == str(bob_codex_home.resolve())
    assert config.defaults.codex_home_mode == "default"


def test_defaults_include_codex_home_mode_when_configured(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "codex-home-mode.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        codex_home_mode = "isolated"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.codex_home_mode == "isolated"


def test_defaults_include_codex_sandbox_mode_when_configured(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "codex-sandbox-mode.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        codex_sandbox_mode = "danger-full-access"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.codex_sandbox_mode == "danger-full-access"


def test_workspace_slack_url_accepts_enterprise_domain(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "workspace-slack-url.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        slack_signin_url = "https://slack.com/signin?entry_point=nav_menu#/signin"

        [[workspaces]]
        name = "workspace"
        slack_url = "https://example.enterprise.slack.com/"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.workspaces[0].slack_url == "https://example.enterprise.slack.com/"


def test_workspace_slack_api_origin_and_token_are_loaded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "workspace-slack-api.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "workspace"
        slack_url = "https://example.enterprise.slack.com/"
        slack_api_origin = "https://example.enterprise.slack.com"
        slack_api_token = "xoxc-demo-token"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.workspaces[0].slack_api_origin == "https://example.enterprise.slack.com"
    assert config.workspaces[0].slack_api_token == "xoxc-demo-token"


def test_workspace_slack_url_must_use_https(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "workspace-slack-http.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "workspace"
        slack_url = "http://example.enterprise.slack.com/"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="https"):
        load_config(config_path)


def test_defaults_slack_signin_url_must_use_https(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "signin-http.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        slack_signin_url = "http://slack.com/signin"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="https"):
        load_config(config_path)


def test_defaults_browser_mode_must_be_known_value(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "bad-browser-mode.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        browser_mode = "unsupported"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="browser_mode"):
        load_config(config_path)


def test_channel_override_wins_over_global_default(tmp_path):
    default_root = tmp_path / "Code"
    channel_root = default_root / "OHAI" / "ctdm"
    default_root.mkdir()
    channel_root.mkdir(parents=True)

    config_path = tmp_path / "bob.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{default_root}"
        accept_root_bob_requests = true
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "oracle"

        [[workspaces.channels]]
        name = "yifanche-private"
        default_cwd = "{channel_root}"
        accept_root_bob_requests = false
        persistent_memory_mode = "owner_only"
        persistent_memory_owner = "yifanche"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    channel = config.workspaces[0].channels[0]

    assert channel.effective_default_cwd == str(channel_root.resolve())
    assert channel.effective_accept_root_bob_requests is False
    assert config.workspaces[0].allowed_actor_ids == ["U123"]


def test_channel_memory_policy_owner_only_is_loaded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "owner-only.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "oracle"

        [[workspaces.channels]]
        name = "yifanche-private"
        persistent_memory_mode = "owner_only"
        persistent_memory_owner = "yifanche"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    channel = config.workspaces[0].channels[0]

    assert channel.persistent_memory_mode == "owner_only"
    assert channel.persistent_memory_owner == "yifanche"
    assert channel.effective_codex_home_mode == "default"


def test_channel_codex_home_mode_override_is_loaded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "channel-codex-home-mode.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        codex_home_mode = "default"

        [[workspaces]]
        name = "oracle"

        [[workspaces.channels]]
        name = "yifanche-bob"
        codex_home_mode = "isolated"
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    channel = config.workspaces[0].channels[0]

    assert channel.codex_home_mode == "isolated"
    assert channel.effective_codex_home_mode == "isolated"


def test_channel_codex_sandbox_mode_override_is_loaded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "channel-codex-sandbox-mode.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        codex_sandbox_mode = "workspace-write"

        [[workspaces]]
        name = "oracle"

        [[workspaces.channels]]
        name = "yifanche-bob-test"
        codex_sandbox_mode = "danger-full-access"
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    channel = config.workspaces[0].channels[0]

    assert channel.codex_sandbox_mode == "danger-full-access"
    assert channel.effective_codex_sandbox_mode == "danger-full-access"


def test_channel_slack_channel_id_is_loaded_and_dumped(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "channel-id.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "oracle"
        slack_url = "https://app.slack.com/client/T12345678/C00000001"

        [[workspaces.channels]]
        name = "yifanche-bob-test"
        persistent_memory_mode = "disabled"
        slack_channel_id = "C0AS82WLCBU"
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    rendered = dump_config(loaded)
    rewritten = tmp_path / "rewritten.toml"
    rewritten.write_text(rendered, encoding="utf-8")
    reloaded = load_config(rewritten)

    assert reloaded.workspaces[0].channels[0].slack_channel_id == "C0AS82WLCBU"
    assert 'slack_channel_id = "C0AS82WLCBU"' in rendered


def test_channel_codex_sandbox_mode_is_dumped(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "channel-sandbox-dump.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        codex_sandbox_mode = "workspace-write"

        [[workspaces]]
        name = "oracle"

        [[workspaces.channels]]
        name = "yifanche-bob-test"
        codex_sandbox_mode = "danger-full-access"
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    rendered = dump_config(loaded)

    assert 'codex_sandbox_mode = "workspace-write"' in rendered
    assert rendered.count('codex_sandbox_mode = "danger-full-access"') == 1


def test_channel_memory_policy_disabled_rejects_owner(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "disabled-with-owner.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "oracle"

        [[workspaces.channels]]
        name = "yifanche-bob"
        persistent_memory_mode = "disabled"
        persistent_memory_owner = "yifanche"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="persistent_memory_owner"):
        load_config(config_path)


def test_channel_memory_policy_owner_only_requires_owner(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "missing-owner.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "oracle"

        [[workspaces.channels]]
        name = "yifanche-private"
        persistent_memory_mode = "owner_only"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="persistent_memory_owner"):
        load_config(config_path)


def test_channel_memory_policy_mode_is_required(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "missing-mode.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "oracle"

        [[workspaces.channels]]
        name = "yifanche-private"
        persistent_memory_owner = "yifanche"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="persistent_memory_mode"):
        load_config(config_path)


def test_channel_legacy_slack_url_is_ignored_and_not_dumped(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "legacy-channel-url.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "oracle"
        slack_url = "https://app.slack.com/client/T12345678/C00000001"

        [[workspaces.channels]]
        name = "yifanche-private"
        slack_url = "https://app.slack.com/client/T12345678/C12345678"
        persistent_memory_mode = "owner_only"
        persistent_memory_owner = "yifanche"
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    rendered = dump_config(loaded)

    assert 'slack_url = "https://app.slack.com/client/T12345678/C00000001"' in rendered
    assert 'slack_url = "https://app.slack.com/client/T12345678/C12345678"' not in rendered


def test_missing_workspace_channel_name_raises(tmp_path):
    default_root = tmp_path / "project"
    default_root.mkdir()

    config_path = tmp_path / "bad.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{default_root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "oracle"

        [[workspaces.channels]]
        default_cwd = "{default_root}"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_workspace_without_allowed_actor_ids_defaults_to_unrestricted_access(tmp_path):
    default_root = tmp_path / "project"
    default_root.mkdir()

    config_path = tmp_path / "no-actors.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{default_root}"

        [[workspaces]]
        name = "oracle"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.allowed_actor_ids == []
    assert config.workspaces[0].allowed_actor_ids == []


def test_workspace_with_empty_allowed_actor_ids_allows_unrestricted_access(tmp_path):
    default_root = tmp_path / "project"
    default_root.mkdir()

    config_path = tmp_path / "unrestricted.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{default_root}"
        allowed_actor_ids = []

        [[workspaces]]
        name = "oracle"
        allowed_actor_ids = []
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.allowed_actor_ids == []
    assert config.workspaces[0].allowed_actor_ids == []


def test_duplicate_workspace_and_channel_names_raise(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "duplicates.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "oracle"

        [[workspaces.channels]]
        name = "ops"
        persistent_memory_mode = "disabled"

        [[workspaces]]
        name = "oracle"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="Duplicate workspace"):
        load_config(config_path)


def test_bool_values_are_rejected_for_integer_timer_fields(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "timers.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        reminder_minutes = [true]
        auto_close_minutes = false
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_paths_are_expanded_and_resolved_relative_to_config(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    project_root = home_dir / "Code"
    home_dir.mkdir()
    project_root.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "bob.toml"
    config_path.write_text(
        """
        [defaults]
        default_cwd = "~/Code"
        allowed_actor_ids = ["U123"]
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.default_cwd == str(project_root.resolve())


def test_dump_config_round_trips_workspace_api_fields(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "workspace-slack-api.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "workspace"
        allowed_actor_ids = ["U123"]
        slack_url = "https://app.slack.com/client/T12345678/C12345678"
        slack_api_origin = "https://example.enterprise.slack.com"
        slack_api_token = "xoxc-demo-token"
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    rendered = dump_config(loaded)
    rewritten = tmp_path / "rewritten.toml"
    rewritten.write_text(rendered, encoding="utf-8")
    reloaded = load_config(rewritten)

    assert reloaded.workspaces[0].slack_api_origin == "https://example.enterprise.slack.com"
    assert reloaded.workspaces[0].slack_api_token == "xoxc-demo-token"
