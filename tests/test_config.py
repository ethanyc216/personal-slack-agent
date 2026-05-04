import pytest
from pathlib import Path

from personal_slack_agent.config import ConfigError, dump_config, load_config


def test_repo_sample_config_loads_successfully():
    sample_path = Path(__file__).resolve().parents[1] / "config" / "bob.sample.toml"

    config = load_config(sample_path)

    assert config.defaults.owner_name == "Bob Owner"
    assert config.defaults.owner_preferred_name == "Owner"
    assert config.workspaces
    assert config.workspaces[0].channel_defaults.default_cwd
    assert config.workspaces[0].channels


def test_config_loads_without_defaults_when_workspace_channel_defaults_provide_required_fields(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "no-defaults.toml"
    config_path.write_text(
        f"""
        [browser]
        slack_signin_url = "https://slack.com/signin?entry_point=nav_menu#/signin"
        browser_mode = "shared_browser"
        browser_url = "http://127.0.0.1:9222"
        cdp_url = "http://127.0.0.1:9222"

        [[workspaces]]
        name = "bob_company"
        slack_url = "https://app.slack.com/client/T12345678/C12345678"

        [workspaces.channel_defaults]
        default_cwd = "{root}"
        persistent_memory_mode = "disabled"

        [[workspaces.channels]]
        name = "bob_channel"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.default_cwd is None
    assert config.workspaces[0].channels[0].effective_default_cwd == str(root.resolve())
    assert config.workspaces[0].channels[0].effective_persistent_memory_mode == "disabled"


def test_defaults_assistant_names_default_to_bob(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "assistant-defaults.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.assistant_names == ["Bob"]


def test_defaults_assistant_names_load_and_dump_round_trip(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "assistant-names.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        assistant_names = ["Bob", "Bobby", "Copilot"]
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    rendered = dump_config(config)
    round_tripped_path = tmp_path / "round-trip.toml"
    round_tripped_path.write_text(rendered, encoding="utf-8")
    round_tripped = load_config(round_tripped_path)

    assert config.defaults.assistant_names == ["Bob", "Bobby", "Copilot"]
    assert round_tripped.defaults.assistant_names == ["Bob", "Bobby", "Copilot"]


def test_defaults_assistant_names_empty_list_falls_back_to_bob(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "assistant-empty.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        assistant_names = []
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.assistant_names == ["Bob"]


def test_defaults_assistant_names_reject_case_insensitive_duplicates(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "assistant-duplicates.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        assistant_names = ["Bob", "bob"]
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="defaults.assistant_names"):
        load_config(config_path)


def test_browser_settings_use_defaults_when_omitted(tmp_path):
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
        config.browser.slack_signin_url
        == "https://slack.com/signin?entry_point=nav_menu#/signin"
    )
    assert config.browser.browser_mode == "dedicated_browser"
    assert config.browser.browser_url == "http://127.0.0.1:9222"
    assert config.browser.cdp_url == "http://127.0.0.1:9222"
    assert config.browser.chrome_executable_path is None
    assert config.browser.browser_user_data_dir is None
    assert config.browser.slack_reauth_cooldown_seconds == 60.0


def test_browser_settings_load_from_browser_section(tmp_path):
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

        [browser]
        browser_mode = "shared_browser"
        browser_url = "http://127.0.0.1:9222"
        cdp_url = "http://127.0.0.1:9223"
        chrome_executable_path = "{chrome_bin}"
        browser_user_data_dir = "{user_data_dir}"
        slack_reauth_cooldown_seconds = 45
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.browser.browser_mode == "shared_browser"
    assert config.browser.browser_url == "http://127.0.0.1:9222"
    assert config.browser.cdp_url == "http://127.0.0.1:9223"
    assert config.browser.chrome_executable_path == str(chrome_bin.resolve())
    assert config.browser.browser_user_data_dir == str(user_data_dir.resolve())
    assert config.browser.slack_reauth_cooldown_seconds == 45.0


def test_browser_settings_fallback_to_legacy_defaults_keys(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    user_data_dir = tmp_path / "chrome-profile"
    chrome_bin = tmp_path / "chrome"
    chrome_bin.write_text("", encoding="utf-8")

    config_path = tmp_path / "legacy-browser-config.toml"
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

    assert config.browser.browser_mode == "shared_browser"
    assert config.browser.browser_url == "http://127.0.0.1:9222"
    assert config.browser.cdp_url == "http://127.0.0.1:9223"
    assert config.browser.chrome_executable_path == str(chrome_bin.resolve())
    assert config.browser.browser_user_data_dir == str(user_data_dir.resolve())


def test_runner_settings_include_bob_codex_home_when_configured(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    bob_codex_home = tmp_path / "bob-codex-home"

    config_path = tmp_path / "bob-codex-home.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [runner]
        bob_codex_home = "{bob_codex_home}"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.runner.bob_codex_home == str(bob_codex_home.resolve())
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


def test_runner_settings_include_codex_exec_timeout_seconds_when_configured(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "codex-exec-timeout.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [runner]
        codex_exec_timeout_seconds = 1200
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.runner.codex_exec_timeout_seconds == 1200.0


def test_runner_settings_fallback_to_legacy_defaults_keys(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    bob_codex_home = tmp_path / "bob-codex-home"

    config_path = tmp_path / "legacy-runner.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        bob_codex_home = "{bob_codex_home}"
        codex_exec_timeout_seconds = 1200
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.runner.bob_codex_home == str(bob_codex_home.resolve())
    assert config.runner.codex_exec_timeout_seconds == 1200.0


def test_defaults_include_workspace_write_writable_roots_when_configured(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    sandbox_root = tmp_path / "workspace"
    scratch_root = tmp_path / "scratch"
    sandbox_root.mkdir()
    scratch_root.mkdir()

    config_path = tmp_path / "codex-writable-roots.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        codex_workspace_write_writable_roots = ["{sandbox_root}", "{scratch_root}", "/tmp"]
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.codex_workspace_write_writable_roots == [
        str(sandbox_root.resolve()),
        str(scratch_root.resolve()),
        str(Path("/tmp").resolve()),
    ]


def test_orchestrator_settings_use_defaults_when_omitted(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "concurrency-defaults.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.orchestrator.max_concurrent_tasks == 1
    assert config.orchestrator.max_concurrent_per_thread == 1


def test_orchestrator_settings_load_from_orchestrator_section(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "concurrency-values.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [orchestrator]
        max_concurrent_tasks = 5
        max_concurrent_per_thread = 2
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.orchestrator.max_concurrent_tasks == 5
    assert config.orchestrator.max_concurrent_per_thread == 2


def test_orchestrator_settings_fallback_to_legacy_defaults_keys(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "legacy-concurrency-values.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        max_concurrent_tasks = 4
        max_concurrent_per_thread = 2
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.orchestrator.max_concurrent_tasks == 4
    assert config.orchestrator.max_concurrent_per_thread == 2


def test_watcher_settings_load_from_watcher_section(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "watcher-values.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [watcher]
        root_batch_size = 25
        thread_batch_size = 125
        thread_reply_rate_limit_backoff_seconds = 45
        recent_terminal_thread_reconcile_limit = 8
        periodic_terminal_thread_reconcile_batch_size = 3
        historical_terminal_thread_reconcile_base_interval_seconds = 90
        historical_terminal_thread_reconcile_max_interval_seconds = 1200
        bob_ultimate_mode = true
        bob_ultimate_mode_codex_home_mode = "default"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.watcher.root_batch_size == 25
    assert config.watcher.thread_batch_size == 125
    assert config.watcher.thread_reply_rate_limit_backoff_seconds == 45.0
    assert config.watcher.recent_terminal_thread_reconcile_limit == 8
    assert config.watcher.periodic_terminal_thread_reconcile_batch_size == 3
    assert config.watcher.historical_terminal_thread_reconcile_base_interval_seconds == 90.0
    assert config.watcher.historical_terminal_thread_reconcile_max_interval_seconds == 1200.0
    assert config.watcher.bob_ultimate_mode is True
    assert config.watcher.bob_ultimate_mode_codex_home_mode == "default"


def test_watcher_settings_fallback_to_legacy_defaults_keys(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "legacy-watcher-values.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        root_batch_size = 40
        thread_batch_size = 180
        thread_reply_rate_limit_backoff_seconds = 30
        recent_terminal_thread_reconcile_limit = 5
        periodic_terminal_thread_reconcile_batch_size = 2
        historical_terminal_thread_reconcile_base_interval_seconds = 75
        historical_terminal_thread_reconcile_max_interval_seconds = 600
        bob_ultimate_mode = true
        bob_ultimate_mode_codex_home_mode = "isolated"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.watcher.root_batch_size == 40
    assert config.watcher.thread_batch_size == 180
    assert config.watcher.thread_reply_rate_limit_backoff_seconds == 30.0
    assert config.watcher.recent_terminal_thread_reconcile_limit == 5
    assert config.watcher.periodic_terminal_thread_reconcile_batch_size == 2
    assert config.watcher.historical_terminal_thread_reconcile_base_interval_seconds == 75.0
    assert config.watcher.historical_terminal_thread_reconcile_max_interval_seconds == 600.0
    assert config.watcher.bob_ultimate_mode is True
    assert config.watcher.bob_ultimate_mode_codex_home_mode == "isolated"


def test_dump_config_emits_orchestrator_and_watcher_sections(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "legacy-layout.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        max_concurrent_tasks = 4
        max_concurrent_per_thread = 2
        root_batch_size = 40
        thread_batch_size = 180
        thread_reply_rate_limit_backoff_seconds = 30
        recent_terminal_thread_reconcile_limit = 5
        periodic_terminal_thread_reconcile_batch_size = 2
        historical_terminal_thread_reconcile_base_interval_seconds = 75
        historical_terminal_thread_reconcile_max_interval_seconds = 600
        bob_ultimate_mode = true
        bob_ultimate_mode_codex_home_mode = "default"
        """,
        encoding="utf-8",
    )

    rendered = dump_config(load_config(config_path))

    assert "[orchestrator]" in rendered
    assert "max_concurrent_tasks = 4" in rendered
    assert "max_concurrent_per_thread = 2" in rendered
    assert "[watcher]" in rendered
    assert "root_batch_size = 40" in rendered
    assert "thread_batch_size = 180" in rendered
    assert "thread_reply_rate_limit_backoff_seconds = 30" in rendered
    assert "recent_terminal_thread_reconcile_limit = 5" in rendered
    assert "periodic_terminal_thread_reconcile_batch_size = 2" in rendered
    assert "historical_terminal_thread_reconcile_base_interval_seconds = 75" in rendered
    assert "historical_terminal_thread_reconcile_max_interval_seconds = 600" in rendered
    assert "bob_ultimate_mode = true" in rendered
    assert 'bob_ultimate_mode_codex_home_mode = "default"' in rendered


def test_dump_config_emits_browser_runner_and_lifecycle_sections(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    bob_codex_home = tmp_path / "bob-codex-home"

    config_path = tmp_path / "legacy-layout-sections.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        browser_mode = "shared_browser"
        browser_url = "http://127.0.0.1:9222"
        cdp_url = "http://127.0.0.1:9223"
        slack_signin_url = "https://slack.com/signin?entry_point=nav_menu#/signin"
        browser_user_data_dir = "{tmp_path / "chrome-profile"}"
        bob_codex_home = "{bob_codex_home}"
        codex_exec_timeout_seconds = 1200
        reminder_minutes = [30, 60]
        auto_close_minutes = 180
        """,
        encoding="utf-8",
    )

    rendered = dump_config(load_config(config_path))

    assert "[browser]" in rendered
    assert 'browser_mode = "shared_browser"' in rendered
    assert 'browser_url = "http://127.0.0.1:9222"' in rendered
    assert 'cdp_url = "http://127.0.0.1:9223"' in rendered
    assert "slack_reauth_cooldown_seconds = 60" in rendered
    assert "[runner]" in rendered
    assert 'bob_codex_home = "{0}"'.format(str(bob_codex_home.resolve())) in rendered
    assert "codex_exec_timeout_seconds = 1200" in rendered
    assert "[lifecycle]" in rendered
    assert "reminder_minutes = [30, 60]" in rendered
    assert "auto_close_minutes = 180" in rendered


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


def test_browser_slack_signin_url_must_use_https(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "signin-http.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [browser]
        slack_signin_url = "http://slack.com/signin"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="https"):
        load_config(config_path)


def test_browser_mode_must_be_known_value(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "bad-browser-mode.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [browser]
        browser_mode = "unsupported"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="browser_mode"):
        load_config(config_path)


def test_browser_slack_reauth_cooldown_must_be_positive(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "bad-reauth-cooldown.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [browser]
        slack_reauth_cooldown_seconds = 0
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="browser.slack_reauth_cooldown_seconds"):
        load_config(config_path)


def test_channel_override_wins_over_workspace_channel_defaults(tmp_path):
    default_root = tmp_path / "Code"
    channel_root = default_root / "OHAI" / "ctdm"
    default_root.mkdir()
    channel_root.mkdir(parents=True)

    config_path = tmp_path / "bob.toml"
    config_path.write_text(
        f"""
        [defaults]
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "bob_company"

        [workspaces.channel_defaults]
        default_cwd = "{default_root}"
        accept_root_bob_requests = true

        [[workspaces.channels]]
        name = "bob_private_channel"
        default_cwd = "{channel_root}"
        accept_root_bob_requests = false
        persistent_memory_mode = "owner_only"
        persistent_memory_owner = "bob_owner_handle"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    channel = config.workspaces[0].channels[0]

    assert channel.effective_default_cwd == str(channel_root.resolve())
    assert channel.effective_accept_root_bob_requests is False
    assert config.workspaces[0].channel_defaults.allowed_actor_ids == ["U123"]


def test_channel_additional_roots_override_can_be_empty(tmp_path):
    default_root = tmp_path / "Code"
    default_root.mkdir()
    extra_root = tmp_path / "extra"
    extra_root.mkdir()

    config_path = tmp_path / "channel-additional-roots.toml"
    config_path.write_text(
        f"""
        [defaults]
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "bob_company"

        [workspaces.channel_defaults]
        default_cwd = "{default_root}"
        additional_roots = ["{extra_root}"]

        [[workspaces.channels]]
        name = "bob_channel"
        additional_roots = []
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    channel = config.workspaces[0].channels[0]

    assert channel.additional_roots == []
    assert channel.effective_additional_roots == []


def test_channel_memory_policy_owner_only_is_loaded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "owner-only.toml"
    config_path.write_text(
        f"""
        [defaults]
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "bob_company"

        [workspaces.channel_defaults]
        default_cwd = "{root}"

        [[workspaces.channels]]
        name = "bob_private_channel"
        persistent_memory_mode = "owner_only"
        persistent_memory_owner = "bob_owner_handle"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    channel = config.workspaces[0].channels[0]

    assert channel.persistent_memory_mode == "owner_only"
    assert channel.persistent_memory_owner == "bob_owner_handle"
    assert channel.effective_codex_home_mode == "default"


def test_owner_names_load_from_defaults_and_round_trip_with_workspaces(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "owner-names.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]
        owner_name = "Bob Owner"
        owner_preferred_name = "Owner"

        [[workspaces]]
        name = "bob_company"

        [workspaces.channel_defaults]
        persistent_memory_mode = "disabled"

        [[workspaces.channels]]
        name = "bob_channel"
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    rendered = dump_config(loaded)
    rewritten = tmp_path / "rewritten.toml"
    rewritten.write_text(rendered, encoding="utf-8")
    reloaded = load_config(rewritten)

    assert reloaded.defaults.owner_name == "Bob Owner"
    assert reloaded.defaults.owner_preferred_name == "Owner"
    assert 'owner_name = "Bob Owner"' in rendered
    assert 'owner_preferred_name = "Owner"' in rendered


def test_channel_memory_policy_can_default_from_workspace_channel_defaults(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "workspace-default-memory-policy.toml"
    config_path.write_text(
        f"""
        [defaults]
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "bob_company"

        [workspaces.channel_defaults]
        default_cwd = "{root}"
        persistent_memory_mode = "disabled"

        [[workspaces.channels]]
        name = "bob_channel"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    channel = config.workspaces[0].channels[0]

    assert channel.effective_persistent_memory_mode == "disabled"


def test_channel_codex_home_mode_override_is_loaded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "channel-codex-home-mode.toml"
    config_path.write_text(
        f"""
        [defaults]
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "bob_company"

        [workspaces.channel_defaults]
        default_cwd = "{root}"
        codex_home_mode = "default"

        [[workspaces.channels]]
        name = "bob_channel"
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
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "bob_company"

        [workspaces.channel_defaults]
        default_cwd = "{root}"
        codex_sandbox_mode = "workspace-write"

        [[workspaces.channels]]
        name = "bob_test_channel"
        codex_sandbox_mode = "danger-full-access"
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    channel = config.workspaces[0].channels[0]

    assert channel.codex_sandbox_mode == "danger-full-access"
    assert channel.effective_codex_sandbox_mode == "danger-full-access"


def test_channel_allowed_actor_ids_override_is_loaded_and_dumped(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "channel-allowed-actors.toml"
    config_path.write_text(
        f"""
        [defaults]

        [[workspaces]]
        name = "bob_company"

        [workspaces.channel_defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces.channels]]
        name = "bob_test_channel"
        allowed_actor_ids = ["U999"]
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    rendered = dump_config(loaded)
    rewritten = tmp_path / "rewritten-channel-allowed-actors.toml"
    rewritten.write_text(rendered, encoding="utf-8")
    reloaded = load_config(rewritten)

    assert reloaded.workspaces[0].channels[0].allowed_actor_ids == ["U999"]
    assert 'allowed_actor_ids = ["U999"]' in rendered


def test_workspace_channel_defaults_allowed_actor_ids_is_loaded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "workspace-channel-default-actors.toml"
    config_path.write_text(
        f"""
        [defaults]

        [[workspaces]]
        name = "bob_company"

        [workspaces.channel_defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces.channels]]
        name = "bob_test_channel"
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)

    assert loaded.workspaces[0].channel_defaults.allowed_actor_ids == ["U123"]


def test_workspace_allowed_actor_ids_is_rejected(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "legacy-workspace-actors.toml"
    config_path.write_text(
        f"""
        [defaults]
        allowed_actor_ids = []

        [[workspaces]]
        name = "bob_company"
        allowed_actor_ids = ["U123"]

        [workspaces.channel_defaults]
        default_cwd = "{root}"

        [[workspaces.channels]]
        name = "bob_test_channel"
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="workspaces.allowed_actor_ids"):
        load_config(config_path)


def test_channel_workspace_write_writable_roots_override_is_loaded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    workspace_root = tmp_path / "workspace"
    scratch_root = tmp_path / "scratch"
    workspace_root.mkdir()
    scratch_root.mkdir()

    config_path = tmp_path / "channel-codex-writable-roots.toml"
    config_path.write_text(
        f"""
        [defaults]
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "bob_company"

        [workspaces.channel_defaults]
        default_cwd = "{root}"
        codex_workspace_write_writable_roots = ["{root}"]

        [[workspaces.channels]]
        name = "bob_test_channel"
        codex_workspace_write_writable_roots = ["{workspace_root}", "{scratch_root}", "/tmp"]
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    channel = config.workspaces[0].channels[0]

    assert channel.codex_workspace_write_writable_roots == [
        str(workspace_root.resolve()),
        str(scratch_root.resolve()),
        str(Path("/tmp").resolve()),
    ]
    assert channel.effective_codex_workspace_write_writable_roots == [
        str(workspace_root.resolve()),
        str(scratch_root.resolve()),
        str(Path("/tmp").resolve()),
    ]


def test_dump_config_emits_workspace_channel_defaults_from_legacy_defaults(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    extra_root = tmp_path / "extra"
    extra_root.mkdir()

    config_path = tmp_path / "workspace-channel-defaults-dump.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        additional_roots = ["{extra_root}"]
        accept_root_bob_requests = true
        allowed_actor_ids = ["U123"]
        codex_home_mode = "isolated"
        codex_sandbox_mode = "workspace-write"

        [[workspaces]]
        name = "bob_company"

        [[workspaces.channels]]
        name = "bob_channel"
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    rendered = dump_config(load_config(config_path))

    assert "[workspaces.channel_defaults]" in rendered
    assert 'default_cwd = "{0}"'.format(str(root.resolve())) in rendered
    assert 'additional_roots = ["{0}"]'.format(str(extra_root.resolve())) in rendered
    assert "accept_root_bob_requests = true" in rendered
    assert 'codex_home_mode = "isolated"' in rendered
    assert 'codex_sandbox_mode = "workspace-write"' in rendered


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
        name = "bob_company"
        slack_url = "https://app.slack.com/client/T12345678/C00000001"

        [[workspaces.channels]]
        name = "bob_test_channel"
        persistent_memory_mode = "disabled"
        slack_channel_id = "bob_channel"
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    rendered = dump_config(loaded)
    rewritten = tmp_path / "rewritten.toml"
    rewritten.write_text(rendered, encoding="utf-8")
    reloaded = load_config(rewritten)

    assert reloaded.workspaces[0].channels[0].slack_channel_id == "bob_channel"
    assert 'slack_channel_id = "bob_channel"' in rendered


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
        name = "bob_company"

        [[workspaces.channels]]
        name = "bob_test_channel"
        codex_sandbox_mode = "danger-full-access"
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    rendered = dump_config(loaded)

    assert 'codex_sandbox_mode = "workspace-write"' in rendered
    assert rendered.count('codex_sandbox_mode = "danger-full-access"') == 1


def test_channel_workspace_write_writable_roots_is_dumped(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    workspace_root = tmp_path / "workspace"
    scratch_root = tmp_path / "scratch"
    workspace_root.mkdir()
    scratch_root.mkdir()

    config_path = tmp_path / "channel-writable-roots-dump.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "bob_company"

        [[workspaces.channels]]
        name = "bob_test_channel"
        codex_workspace_write_writable_roots = ["{workspace_root}", "{scratch_root}", "/tmp"]
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    rendered = dump_config(loaded)

    assert "codex_workspace_write_writable_roots =" in rendered
    assert str(workspace_root.resolve()) in rendered
    assert str(scratch_root.resolve()) in rendered
    assert '"{0}"'.format(str(Path("/tmp").resolve())) in rendered


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
        name = "bob_company"

        [[workspaces.channels]]
        name = "bob_channel"
        persistent_memory_mode = "disabled"
        persistent_memory_owner = "bob_owner_handle"
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
        name = "bob_company"

        [[workspaces.channels]]
        name = "bob_private_channel"
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
        name = "bob_company"

        [[workspaces.channels]]
        name = "bob_private_channel"
        persistent_memory_owner = "bob_owner_handle"
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
        name = "bob_company"
        slack_url = "https://app.slack.com/client/T12345678/C00000001"

        [[workspaces.channels]]
        name = "bob_private_channel"
        slack_url = "https://app.slack.com/client/T12345678/C12345678"
        persistent_memory_mode = "owner_only"
        persistent_memory_owner = "bob_owner_handle"
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
        name = "bob_company"

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
        name = "bob_company"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.allowed_actor_ids == []
    assert config.workspaces[0].channel_defaults.allowed_actor_ids == []


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
        name = "bob_company"

        [workspaces.channel_defaults]
        allowed_actor_ids = []
        default_cwd = "{default_root}"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.defaults.allowed_actor_ids == []
    assert config.workspaces[0].channel_defaults.allowed_actor_ids == []


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
        name = "bob_company"

        [[workspaces.channels]]
        name = "ops"
        persistent_memory_mode = "disabled"

        [[workspaces]]
        name = "bob_company"
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

        [lifecycle]
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


def test_dump_config_round_trips_runner_codex_exec_timeout_seconds(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "codex-exec-timeout-roundtrip.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [runner]
        codex_exec_timeout_seconds = 900
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)
    rendered = dump_config(loaded)
    rewritten = tmp_path / "rewritten-timeout.toml"
    rewritten.write_text(rendered, encoding="utf-8")
    reloaded = load_config(rewritten)

    assert reloaded.runner.codex_exec_timeout_seconds == 900.0
