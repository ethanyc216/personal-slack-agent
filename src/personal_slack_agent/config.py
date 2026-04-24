from pathlib import Path
from typing import Any, List, Mapping, Optional, Union
from urllib.parse import urlparse

from .models import (
    DEFAULT_BROWSER_CDP_URL,
    CODEX_HOME_MODE_DEFAULT,
    CODEX_HOME_MODE_ISOLATED,
    CODEX_SANDBOX_MODE_DANGER_FULL_ACCESS,
    CODEX_SANDBOX_MODE_READ_ONLY,
    CODEX_SANDBOX_MODE_WORKSPACE_WRITE,
    DEDICATED_BROWSER_MODE,
    DEFAULT_SLACK_SIGNIN_URL,
    PERSISTENT_MEMORY_MODE_DISABLED,
    PERSISTENT_MEMORY_MODE_OWNER_ONLY,
    SHARED_BROWSER_MODE,
    AppConfig,
    BrowserSettings,
    ChannelConfig,
    DefaultSettings,
    LifecycleSettings,
    OrchestratorSettings,
    RunnerSettings,
    WatcherSettings,
    WorkspaceChannelDefaults,
    WorkspaceConfig,
)

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]


class ConfigError(ValueError):
    pass


RUNTIME_CHANNEL_PREFIX = "slack:"


def apply_channel_defaults(
    defaults: DefaultSettings,
    channel_defaults: WorkspaceChannelDefaults,
    channel: ChannelConfig,
) -> ChannelConfig:
    channel.effective_allowed_actor_ids = (
        list(channel.allowed_actor_ids)
        if channel.allowed_actor_ids is not None
        else (
            list(channel_defaults.allowed_actor_ids)
            if channel_defaults.allowed_actor_ids is not None
            else list(defaults.allowed_actor_ids)
        )
    )
    channel.effective_default_cwd = channel.default_cwd or channel_defaults.default_cwd or ""
    channel.effective_additional_roots = (
        list(channel.additional_roots)
        if channel.additional_roots is not None
        else (
            list(channel_defaults.additional_roots)
            if channel_defaults.additional_roots is not None
            else list(defaults.additional_roots)
        )
    )
    channel.effective_accept_root_bob_requests = (
        channel.accept_root_bob_requests
        if channel.accept_root_bob_requests is not None
        else (
            channel_defaults.accept_root_bob_requests
            if channel_defaults.accept_root_bob_requests is not None
            else defaults.accept_root_bob_requests
        )
    )
    channel.effective_post_terminal_threads_here = (
        channel.post_terminal_threads_here
        if channel.post_terminal_threads_here is not None
        else (
            channel_defaults.post_terminal_threads_here
            if channel_defaults.post_terminal_threads_here is not None
            else False
        )
    )
    channel.effective_codex_home_mode = (
        channel.codex_home_mode
        or channel_defaults.codex_home_mode
        or defaults.codex_home_mode
    )
    channel.effective_codex_sandbox_mode = (
        channel.codex_sandbox_mode
        if channel.codex_sandbox_mode is not None
        else (
            channel_defaults.codex_sandbox_mode
            if channel_defaults.codex_sandbox_mode is not None
            else defaults.codex_sandbox_mode
        )
    )
    channel.effective_codex_workspace_write_writable_roots = (
        list(channel.codex_workspace_write_writable_roots)
        if channel.codex_workspace_write_writable_roots is not None
        else (
            list(channel_defaults.codex_workspace_write_writable_roots)
            if channel_defaults.codex_workspace_write_writable_roots is not None
            else (
                list(defaults.codex_workspace_write_writable_roots)
                if defaults.codex_workspace_write_writable_roots is not None
                else None
            )
        )
    )
    channel.effective_persistent_memory_mode = (
        channel.persistent_memory_mode
        if channel.persistent_memory_mode is not None
        else channel_defaults.persistent_memory_mode
    )
    channel.effective_persistent_memory_owner = (
        channel.persistent_memory_owner
        if channel.persistent_memory_owner is not None
        else channel_defaults.persistent_memory_owner
    )
    channel.effective_slack_channel_id = (
        channel.slack_channel_id
        if channel.slack_channel_id is not None
        else channel_defaults.slack_channel_id
    )
    return channel


def runtime_channel_name(slack_channel_id: str) -> str:
    return "{0}{1}".format(RUNTIME_CHANNEL_PREFIX, slack_channel_id)


def slack_channel_id_from_runtime_channel_name(channel_name: str) -> Optional[str]:
    if not channel_name.startswith(RUNTIME_CHANNEL_PREFIX):
        return None
    channel_id = channel_name[len(RUNTIME_CHANNEL_PREFIX):].strip()
    return channel_id or None


def build_runtime_channel(
    defaults: DefaultSettings,
    workspace: WorkspaceConfig,
    channel_name: str,
) -> Optional[ChannelConfig]:
    slack_channel_id = slack_channel_id_from_runtime_channel_name(channel_name)
    if slack_channel_id is None:
        return None
    channel = ChannelConfig(
        name=channel_name,
        slack_channel_id=slack_channel_id,
    )
    resolved = apply_channel_defaults(defaults, workspace.channel_defaults, channel)
    return _validate_channel_memory_policy(resolved)


def load_config(config_path: Union[str, Path]) -> AppConfig:
    path = Path(config_path).expanduser().resolve()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    defaults = _parse_defaults(raw.get("defaults"), path.parent)
    browser = _parse_browser(
        raw.get("browser"),
        raw.get("defaults"),
        path.parent,
    )
    runner = _parse_runner(
        raw.get("runner"),
        raw.get("defaults"),
        path.parent,
    )
    lifecycle = _parse_lifecycle(
        raw.get("lifecycle"),
        raw.get("defaults"),
    )
    orchestrator = _parse_orchestrator(
        raw.get("orchestrator"),
        raw.get("defaults"),
    )
    watcher = _parse_watcher(
        raw.get("watcher"),
        raw.get("defaults"),
    )
    workspaces = _parse_workspaces(raw.get("workspaces"), defaults, path.parent)
    return AppConfig(
        defaults=defaults,
        browser=browser,
        runner=runner,
        lifecycle=lifecycle,
        orchestrator=orchestrator,
        watcher=watcher,
        workspaces=workspaces,
    )


def dump_config(config: AppConfig) -> str:
    lines = ["[defaults]"]
    if not config.workspaces and config.defaults.allowed_actor_ids:
        lines.append(
            "allowed_actor_ids = [{0}]".format(
                ", ".join('"{}"'.format(_toml_escape(item)) for item in config.defaults.allowed_actor_ids)
            )
        )
    if not config.workspaces:
        if config.defaults.default_cwd is not None:
            lines.append('default_cwd = "{0}"'.format(_toml_escape(config.defaults.default_cwd)))
        if config.defaults.additional_roots:
            lines.append(
                "additional_roots = [{0}]".format(
                    ", ".join('"{}"'.format(_toml_escape(item)) for item in config.defaults.additional_roots)
                )
            )
        lines.append(
            "accept_root_bob_requests = {0}".format(
                "true" if config.defaults.accept_root_bob_requests else "false"
            )
        )
        lines.append('codex_home_mode = "{0}"'.format(_toml_escape(config.defaults.codex_home_mode)))
        if config.defaults.codex_sandbox_mode is not None:
            lines.append(
                'codex_sandbox_mode = "{0}"'.format(_toml_escape(config.defaults.codex_sandbox_mode))
            )
        if config.defaults.codex_workspace_write_writable_roots is not None:
            lines.append(
                "codex_workspace_write_writable_roots = [{0}]".format(
                    ", ".join(
                        '"{}"'.format(_toml_escape(item))
                        for item in config.defaults.codex_workspace_write_writable_roots
                    )
                )
            )

    lines.extend(
        [
            "",
            "[browser]",
            'slack_signin_url = "{0}"'.format(_toml_escape(config.browser.slack_signin_url)),
            'browser_mode = "{0}"'.format(_toml_escape(config.browser.browser_mode)),
            'browser_url = "{0}"'.format(_toml_escape(config.browser.browser_url)),
            'cdp_url = "{0}"'.format(_toml_escape(config.browser.cdp_url)),
        ]
    )
    if config.browser.chrome_executable_path is not None:
        lines.append(
            'chrome_executable_path = "{0}"'.format(
                _toml_escape(config.browser.chrome_executable_path)
            )
        )
    if config.browser.browser_user_data_dir is not None:
        lines.append(
            'browser_user_data_dir = "{0}"'.format(
                _toml_escape(config.browser.browser_user_data_dir)
            )
        )
    lines.extend(
        [
            "",
            "[runner]",
        ]
    )
    if config.runner.bob_codex_home is not None:
        lines.append('bob_codex_home = "{0}"'.format(_toml_escape(config.runner.bob_codex_home)))
    if config.runner.codex_exec_timeout_seconds is not None:
        lines.append(
            "codex_exec_timeout_seconds = {0}".format(
                _render_number(config.runner.codex_exec_timeout_seconds)
            )
        )
    lines.extend(
        [
            "",
            "[lifecycle]",
        ]
    )
    if config.lifecycle.reminder_minutes:
        lines.append(
            "reminder_minutes = [{0}]".format(", ".join(str(item) for item in config.lifecycle.reminder_minutes))
        )
    if config.lifecycle.auto_close_minutes is not None:
        lines.append("auto_close_minutes = {0}".format(config.lifecycle.auto_close_minutes))
    lines.extend(
        [
            "",
            "[orchestrator]",
            "max_concurrent_tasks = {0}".format(config.orchestrator.max_concurrent_tasks),
            "max_concurrent_per_thread = {0}".format(
                config.orchestrator.max_concurrent_per_thread
            ),
            "",
            "[watcher]",
            "root_batch_size = {0}".format(config.watcher.root_batch_size),
            "thread_batch_size = {0}".format(config.watcher.thread_batch_size),
            "thread_reply_rate_limit_backoff_seconds = {0}".format(
                _render_number(config.watcher.thread_reply_rate_limit_backoff_seconds)
            ),
            "recent_terminal_thread_reconcile_limit = {0}".format(
                config.watcher.recent_terminal_thread_reconcile_limit
            ),
            "periodic_terminal_thread_reconcile_batch_size = {0}".format(
                config.watcher.periodic_terminal_thread_reconcile_batch_size
            ),
            "historical_terminal_thread_reconcile_base_interval_seconds = {0}".format(
                _render_number(
                    config.watcher.historical_terminal_thread_reconcile_base_interval_seconds
                )
            ),
            "historical_terminal_thread_reconcile_max_interval_seconds = {0}".format(
                _render_number(
                    config.watcher.historical_terminal_thread_reconcile_max_interval_seconds
                )
            ),
            "bob_ultimate_mode = {0}".format(
                "true" if config.watcher.bob_ultimate_mode else "false"
            ),
        ]
    )

    for workspace in config.workspaces:
        lines.extend(
            [
                "",
                "[[workspaces]]",
                'name = "{0}"'.format(_toml_escape(workspace.name)),
            ]
        )
        if workspace.slack_url is not None:
            lines.append('slack_url = "{0}"'.format(_toml_escape(workspace.slack_url)))
        if workspace.slack_api_origin is not None:
            lines.append('slack_api_origin = "{0}"'.format(_toml_escape(workspace.slack_api_origin)))
        if workspace.slack_api_token is not None:
            lines.append('slack_api_token = "{0}"'.format(_toml_escape(workspace.slack_api_token)))
        if _has_workspace_channel_defaults(workspace.channel_defaults):
            lines.extend(
                [
                    "",
                    "[workspaces.channel_defaults]",
                ]
            )
            workspace_default_allowed_actor_ids = workspace.channel_defaults.allowed_actor_ids
            if workspace_default_allowed_actor_ids is not None:
                lines.append(
                    "allowed_actor_ids = [{0}]".format(
                        ", ".join(
                            '"{}"'.format(_toml_escape(item))
                            for item in workspace_default_allowed_actor_ids
                        )
                    )
                )
            if workspace.channel_defaults.default_cwd is not None:
                lines.append(
                    'default_cwd = "{0}"'.format(
                        _toml_escape(workspace.channel_defaults.default_cwd)
                    )
                )
            if workspace.channel_defaults.additional_roots:
                lines.append(
                    "additional_roots = [{0}]".format(
                        ", ".join(
                            '"{}"'.format(_toml_escape(item))
                            for item in workspace.channel_defaults.additional_roots
                        )
                    )
                )
            lines.append(
                "accept_root_bob_requests = {0}".format(
                    "true" if workspace.channel_defaults.accept_root_bob_requests else "false"
                )
            )
            if workspace.channel_defaults.post_terminal_threads_here is not None:
                lines.append(
                    "post_terminal_threads_here = {0}".format(
                        "true" if workspace.channel_defaults.post_terminal_threads_here else "false"
                    )
                )
            lines.append(
                'codex_home_mode = "{0}"'.format(
                    _toml_escape(workspace.channel_defaults.codex_home_mode)
                )
            )
            if workspace.channel_defaults.codex_sandbox_mode is not None:
                lines.append(
                    'codex_sandbox_mode = "{0}"'.format(
                        _toml_escape(workspace.channel_defaults.codex_sandbox_mode)
                    )
                )
            if workspace.channel_defaults.codex_workspace_write_writable_roots is not None:
                lines.append(
                    "codex_workspace_write_writable_roots = [{0}]".format(
                        ", ".join(
                            '"{}"'.format(_toml_escape(item))
                            for item in workspace.channel_defaults.codex_workspace_write_writable_roots
                        )
                    )
                )
            if workspace.channel_defaults.persistent_memory_mode is not None:
                lines.append(
                    'persistent_memory_mode = "{0}"'.format(
                        _toml_escape(workspace.channel_defaults.persistent_memory_mode)
                    )
                )
            if workspace.channel_defaults.persistent_memory_owner is not None:
                lines.append(
                    'persistent_memory_owner = "{0}"'.format(
                        _toml_escape(workspace.channel_defaults.persistent_memory_owner)
                    )
                )
            if workspace.channel_defaults.slack_channel_id is not None:
                lines.append(
                    'slack_channel_id = "{0}"'.format(
                        _toml_escape(workspace.channel_defaults.slack_channel_id)
                    )
                )
        for channel in workspace.channels:
            lines.extend(
                [
                    "",
                    "[[workspaces.channels]]",
                    'name = "{0}"'.format(_toml_escape(channel.name)),
                ]
            )
            if channel.allowed_actor_ids is not None:
                lines.append(
                    "allowed_actor_ids = [{0}]".format(
                        ", ".join('"{}"'.format(_toml_escape(item)) for item in channel.allowed_actor_ids)
                    )
                )
            if channel.default_cwd is not None:
                lines.append('default_cwd = "{0}"'.format(_toml_escape(channel.default_cwd)))
            if channel.additional_roots is not None:
                lines.append(
                    "additional_roots = [{0}]".format(
                        ", ".join('"{}"'.format(_toml_escape(item)) for item in channel.additional_roots)
                    )
                )
            if channel.accept_root_bob_requests is not None:
                lines.append(
                    "accept_root_bob_requests = {0}".format(
                        "true" if channel.accept_root_bob_requests else "false"
                    )
                )
            if channel.post_terminal_threads_here is not None:
                lines.append(
                    "post_terminal_threads_here = {0}".format(
                        "true" if channel.post_terminal_threads_here else "false"
                    )
                )
            if channel.codex_home_mode is not None:
                lines.append(
                    'codex_home_mode = "{0}"'.format(_toml_escape(channel.codex_home_mode))
                )
            if channel.codex_sandbox_mode is not None:
                lines.append(
                    'codex_sandbox_mode = "{0}"'.format(
                        _toml_escape(channel.codex_sandbox_mode)
                    )
                )
            if channel.codex_workspace_write_writable_roots is not None:
                lines.append(
                    "codex_workspace_write_writable_roots = [{0}]".format(
                        ", ".join(
                            '"{}"'.format(_toml_escape(item))
                            for item in channel.codex_workspace_write_writable_roots
                        )
                    )
                )
            if channel.persistent_memory_mode is not None:
                lines.append(
                    'persistent_memory_mode = "{0}"'.format(
                        _toml_escape(channel.persistent_memory_mode)
                    )
                )
            if channel.persistent_memory_owner is not None:
                lines.append(
                    'persistent_memory_owner = "{0}"'.format(
                        _toml_escape(channel.persistent_memory_owner)
                    )
                )
            if channel.slack_channel_id is not None:
                lines.append(
                    'slack_channel_id = "{0}"'.format(_toml_escape(channel.slack_channel_id))
                )
    lines.append("")
    return "\n".join(lines)


def _parse_defaults(raw_defaults: Any, base_dir: Path) -> DefaultSettings:
    if raw_defaults is None:
        raw_defaults = {}
    if not isinstance(raw_defaults, Mapping):
        raise ConfigError("defaults must be a table.")

    return DefaultSettings(
        default_cwd=_optional_directory_path(
            raw_defaults.get("default_cwd"),
            "defaults.default_cwd",
            base_dir,
        ),
        additional_roots=_directory_list(
            raw_defaults.get("additional_roots"),
            "defaults.additional_roots",
            base_dir,
        ),
        accept_root_bob_requests=_optional_bool(raw_defaults.get("accept_root_bob_requests"), "defaults.accept_root_bob_requests", default=True),
        allowed_actor_ids=_string_list(raw_defaults.get("allowed_actor_ids"), "defaults.allowed_actor_ids"),
        codex_home_mode=_codex_home_mode(
            raw_defaults.get("codex_home_mode"),
            "defaults.codex_home_mode",
            default=CODEX_HOME_MODE_DEFAULT,
        ),
        codex_sandbox_mode=_optional_codex_sandbox_mode(
            raw_defaults.get("codex_sandbox_mode"),
            "defaults.codex_sandbox_mode",
        ),
        codex_workspace_write_writable_roots=_optional_directory_list(
            raw_defaults.get("codex_workspace_write_writable_roots"),
            "defaults.codex_workspace_write_writable_roots",
            base_dir,
        ),
    )


def _parse_browser(
    raw_browser: Any,
    raw_defaults: Any,
    base_dir: Path,
) -> BrowserSettings:
    if raw_browser is None:
        raw_browser = {}
    if not isinstance(raw_browser, Mapping):
        raise ConfigError("browser must be a table.")
    legacy_defaults = raw_defaults if isinstance(raw_defaults, Mapping) else {}
    return BrowserSettings(
        slack_signin_url=_optional_https_url(
            raw_browser.get("slack_signin_url", legacy_defaults.get("slack_signin_url")),
            "browser.slack_signin_url",
            default=DEFAULT_SLACK_SIGNIN_URL,
        ),
        browser_mode=_browser_mode(
            raw_browser.get("browser_mode", legacy_defaults.get("browser_mode")),
            "browser.browser_mode",
            default=DEDICATED_BROWSER_MODE,
        ),
        browser_url=_optional_url(
            raw_browser.get("browser_url", legacy_defaults.get("browser_url")),
            "browser.browser_url",
            default=DEFAULT_BROWSER_CDP_URL,
        )
        or DEFAULT_BROWSER_CDP_URL,
        cdp_url=_optional_url(
            raw_browser.get("cdp_url", legacy_defaults.get("cdp_url")),
            "browser.cdp_url",
            default=DEFAULT_BROWSER_CDP_URL,
        )
        or DEFAULT_BROWSER_CDP_URL,
        chrome_executable_path=_optional_path(
            raw_browser.get("chrome_executable_path", legacy_defaults.get("chrome_executable_path")),
            "browser.chrome_executable_path",
            base_dir=base_dir,
        ),
        browser_user_data_dir=_optional_path(
            raw_browser.get("browser_user_data_dir", legacy_defaults.get("browser_user_data_dir")),
            "browser.browser_user_data_dir",
            base_dir=base_dir,
        ),
    )


def _parse_runner(
    raw_runner: Any,
    raw_defaults: Any,
    base_dir: Path,
) -> RunnerSettings:
    if raw_runner is None:
        raw_runner = {}
    if not isinstance(raw_runner, Mapping):
        raise ConfigError("runner must be a table.")
    legacy_defaults = raw_defaults if isinstance(raw_defaults, Mapping) else {}
    return RunnerSettings(
        codex_exec_timeout_seconds=_optional_positive_float(
            raw_runner.get(
                "codex_exec_timeout_seconds",
                legacy_defaults.get("codex_exec_timeout_seconds"),
            ),
            "runner.codex_exec_timeout_seconds",
            default=600.0,
        ),
        bob_codex_home=_optional_path(
            raw_runner.get("bob_codex_home", legacy_defaults.get("bob_codex_home")),
            "runner.bob_codex_home",
            base_dir=base_dir,
        ),
    )


def _parse_lifecycle(
    raw_lifecycle: Any,
    raw_defaults: Any,
) -> LifecycleSettings:
    if raw_lifecycle is None:
        raw_lifecycle = {}
    if not isinstance(raw_lifecycle, Mapping):
        raise ConfigError("lifecycle must be a table.")
    legacy_defaults = raw_defaults if isinstance(raw_defaults, Mapping) else {}
    return LifecycleSettings(
        reminder_minutes=_int_list(
            raw_lifecycle.get("reminder_minutes", legacy_defaults.get("reminder_minutes")),
            "lifecycle.reminder_minutes",
        ),
        auto_close_minutes=_optional_int(
            raw_lifecycle.get("auto_close_minutes", legacy_defaults.get("auto_close_minutes")),
            "lifecycle.auto_close_minutes",
        ),
    )


def _parse_orchestrator(
    raw_orchestrator: Any,
    raw_defaults: Any,
) -> OrchestratorSettings:
    if raw_orchestrator is None:
        raw_orchestrator = {}
    if not isinstance(raw_orchestrator, Mapping):
        raise ConfigError("orchestrator must be a table.")
    legacy_defaults = raw_defaults if isinstance(raw_defaults, Mapping) else {}
    return OrchestratorSettings(
        max_concurrent_tasks=_positive_int(
            raw_orchestrator.get(
                "max_concurrent_tasks",
                legacy_defaults.get("max_concurrent_tasks"),
            ),
            "orchestrator.max_concurrent_tasks",
            default=1,
        ),
        max_concurrent_per_thread=_positive_int(
            raw_orchestrator.get(
                "max_concurrent_per_thread",
                legacy_defaults.get("max_concurrent_per_thread"),
            ),
            "orchestrator.max_concurrent_per_thread",
            default=1,
        ),
    )


def _parse_watcher(
    raw_watcher: Any,
    raw_defaults: Any,
) -> WatcherSettings:
    if raw_watcher is None:
        raw_watcher = {}
    if not isinstance(raw_watcher, Mapping):
        raise ConfigError("watcher must be a table.")
    legacy_defaults = raw_defaults if isinstance(raw_defaults, Mapping) else {}
    return WatcherSettings(
        root_batch_size=_positive_int(
            raw_watcher.get("root_batch_size", legacy_defaults.get("root_batch_size")),
            "watcher.root_batch_size",
            default=50,
        ),
        thread_batch_size=_positive_int(
            raw_watcher.get("thread_batch_size", legacy_defaults.get("thread_batch_size")),
            "watcher.thread_batch_size",
            default=200,
        ),
        thread_reply_rate_limit_backoff_seconds=_optional_positive_float(
            raw_watcher.get(
                "thread_reply_rate_limit_backoff_seconds",
                legacy_defaults.get("thread_reply_rate_limit_backoff_seconds"),
            ),
            "watcher.thread_reply_rate_limit_backoff_seconds",
            default=60.0,
        )
        or 60.0,
        recent_terminal_thread_reconcile_limit=_positive_int(
            raw_watcher.get(
                "recent_terminal_thread_reconcile_limit",
                legacy_defaults.get("recent_terminal_thread_reconcile_limit"),
            ),
            "watcher.recent_terminal_thread_reconcile_limit",
            default=6,
        ),
        periodic_terminal_thread_reconcile_batch_size=_positive_int(
            raw_watcher.get(
                "periodic_terminal_thread_reconcile_batch_size",
                legacy_defaults.get("periodic_terminal_thread_reconcile_batch_size"),
            ),
            "watcher.periodic_terminal_thread_reconcile_batch_size",
            default=1,
        ),
        historical_terminal_thread_reconcile_base_interval_seconds=_optional_positive_float(
            raw_watcher.get(
                "historical_terminal_thread_reconcile_base_interval_seconds",
                legacy_defaults.get("historical_terminal_thread_reconcile_base_interval_seconds"),
            ),
            "watcher.historical_terminal_thread_reconcile_base_interval_seconds",
            default=60.0,
        )
        or 60.0,
        historical_terminal_thread_reconcile_max_interval_seconds=_optional_positive_float(
            raw_watcher.get(
                "historical_terminal_thread_reconcile_max_interval_seconds",
                legacy_defaults.get("historical_terminal_thread_reconcile_max_interval_seconds"),
            ),
            "watcher.historical_terminal_thread_reconcile_max_interval_seconds",
            default=15 * 60.0,
        )
        or 15 * 60.0,
        bob_ultimate_mode=_optional_bool(
            raw_watcher.get("bob_ultimate_mode", legacy_defaults.get("bob_ultimate_mode")),
            "watcher.bob_ultimate_mode",
            default=False,
        )
        or False,
    )


def _parse_workspaces(raw_workspaces: Any, defaults: DefaultSettings, base_dir: Path) -> List[WorkspaceConfig]:
    if raw_workspaces is None:
        return []
    if not isinstance(raw_workspaces, list):
        raise ConfigError("workspaces must be an array of tables.")

    result = []
    seen_names = set()
    for index, raw_workspace in enumerate(raw_workspaces):
        if not isinstance(raw_workspace, Mapping):
            raise ConfigError("Each workspace must be a table.")
        name = raw_workspace.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError("workspace.name must be a non-empty string.")
        if name in seen_names:
            raise ConfigError(f"Duplicate workspace name: {name}")
        seen_names.add(name)

        if "allowed_actor_ids" in raw_workspace:
            raise ConfigError(
                "workspaces.allowed_actor_ids is no longer supported; use workspaces.channel_defaults.allowed_actor_ids or channel.allowed_actor_ids."
            )
        channel_defaults = _parse_workspace_channel_defaults(
            raw_workspace.get("channel_defaults"),
            defaults=defaults,
            base_dir=base_dir,
        )
        channels = _parse_channels(
            raw_workspace.get("channels"),
            defaults=defaults,
            channel_defaults=channel_defaults,
            workspace_index=index,
            base_dir=base_dir,
        )
        result.append(
            WorkspaceConfig(
                name=name,
                channels=channels,
                channel_defaults=channel_defaults,
                slack_url=_optional_https_url(
                    raw_workspace.get("slack_url"),
                    "workspaces.slack_url",
                ),
                slack_api_origin=_optional_https_url(
                    raw_workspace.get("slack_api_origin"),
                    "workspaces.slack_api_origin",
                ),
                slack_api_token=_optional_string(
                    raw_workspace.get("slack_api_token"),
                    "workspaces.slack_api_token",
                ),
            )
        )

    return result


def _parse_channels(
    raw_channels: Any,
    defaults: DefaultSettings,
    channel_defaults: WorkspaceChannelDefaults,
    workspace_index: int,
    base_dir: Path,
) -> List[ChannelConfig]:
    if raw_channels is None:
        return []
    if not isinstance(raw_channels, list):
        raise ConfigError("workspaces.channels must be an array of tables.")

    channels = []
    seen_names = set()
    for channel_index, raw_channel in enumerate(raw_channels):
        if not isinstance(raw_channel, Mapping):
            raise ConfigError("Each channel must be a table.")
        channel_name = raw_channel.get("name")
        if not isinstance(channel_name, str) or not channel_name:
            raise ConfigError(
                "Missing required workspaces[{0}].channels[{1}].name.".format(
                    workspace_index, channel_index
                )
            )
        if channel_name in seen_names:
            raise ConfigError(f"Duplicate channel name in workspace[{workspace_index}]: {channel_name}")
        seen_names.add(channel_name)

        channel = ChannelConfig(
            name=channel_name,
            allowed_actor_ids=(
                _string_list(raw_channel.get("allowed_actor_ids"), "channel.allowed_actor_ids")
                if "allowed_actor_ids" in raw_channel
                else None
            ),
            default_cwd=_optional_directory_path(
                raw_channel.get("default_cwd"),
                "channel.default_cwd",
                base_dir,
            ),
            additional_roots=_optional_directory_list(
                raw_channel.get("additional_roots"),
                "channel.additional_roots",
                base_dir,
            ),
            accept_root_bob_requests=_optional_bool(
                raw_channel.get("accept_root_bob_requests"),
                "channel.accept_root_bob_requests",
            ),
            post_terminal_threads_here=(
                _optional_bool(
                    raw_channel.get("post_terminal_threads_here"),
                    "channel.post_terminal_threads_here",
                )
                if "post_terminal_threads_here" in raw_channel
                else None
            ),
            codex_home_mode=_optional_codex_home_mode(
                raw_channel.get("codex_home_mode"),
                "channel.codex_home_mode",
            ),
            codex_sandbox_mode=_optional_codex_sandbox_mode(
                raw_channel.get("codex_sandbox_mode"),
                "channel.codex_sandbox_mode",
            ),
            codex_workspace_write_writable_roots=_optional_directory_list(
                raw_channel.get("codex_workspace_write_writable_roots"),
                "channel.codex_workspace_write_writable_roots",
                base_dir,
            ),
            persistent_memory_mode=(
                _persistent_memory_mode(
                    raw_channel.get("persistent_memory_mode"),
                    "channel.persistent_memory_mode",
                )
                if "persistent_memory_mode" in raw_channel
                else None
            ),
            persistent_memory_owner=(
                _optional_string(
                    raw_channel.get("persistent_memory_owner"),
                    "channel.persistent_memory_owner",
                )
                if "persistent_memory_owner" in raw_channel
                else None
            ),
            slack_channel_id=(
                _optional_string(
                    raw_channel.get("slack_channel_id"),
                    "channel.slack_channel_id",
                )
                if "slack_channel_id" in raw_channel
                else None
            ),
        )
        resolved = apply_channel_defaults(defaults, channel_defaults, channel)
        if not resolved.effective_default_cwd:
            raise ConfigError(
                "workspaces[{0}].channels[{1}] must define default_cwd directly or via workspaces.channel_defaults.".format(
                    workspace_index,
                    channel_index,
                )
            )
        channels.append(_validate_channel_memory_policy(resolved))
    return channels


def _parse_workspace_channel_defaults(
    raw_channel_defaults: Any,
    defaults: DefaultSettings,
    base_dir: Path,
) -> WorkspaceChannelDefaults:
    if raw_channel_defaults is None:
        raw_channel_defaults = {}
    if not isinstance(raw_channel_defaults, Mapping):
        raise ConfigError("workspaces.channel_defaults must be a table.")

    default_cwd = _optional_directory_path(
        raw_channel_defaults.get("default_cwd"),
        "workspaces.channel_defaults.default_cwd",
        base_dir,
    )
    return WorkspaceChannelDefaults(
        allowed_actor_ids=(
            _string_list(
                raw_channel_defaults.get("allowed_actor_ids"),
                "workspaces.channel_defaults.allowed_actor_ids",
            )
            if "allowed_actor_ids" in raw_channel_defaults
            else list(defaults.allowed_actor_ids)
        ),
        default_cwd=default_cwd if default_cwd is not None else defaults.default_cwd,
        additional_roots=(
            _directory_list(
                raw_channel_defaults.get("additional_roots"),
                "workspaces.channel_defaults.additional_roots",
                base_dir,
            )
            if "additional_roots" in raw_channel_defaults
            else list(defaults.additional_roots)
        ),
        accept_root_bob_requests=_optional_bool(
            raw_channel_defaults.get("accept_root_bob_requests"),
            "workspaces.channel_defaults.accept_root_bob_requests",
            default=defaults.accept_root_bob_requests,
        )
        if "accept_root_bob_requests" in raw_channel_defaults
        else defaults.accept_root_bob_requests,
        codex_home_mode=_codex_home_mode(
            raw_channel_defaults.get("codex_home_mode"),
            "workspaces.channel_defaults.codex_home_mode",
            default=defaults.codex_home_mode,
        ),
        codex_sandbox_mode=_optional_codex_sandbox_mode(
            raw_channel_defaults.get("codex_sandbox_mode"),
            "workspaces.channel_defaults.codex_sandbox_mode",
        )
        if "codex_sandbox_mode" in raw_channel_defaults
        else defaults.codex_sandbox_mode,
        codex_workspace_write_writable_roots=(
            _optional_directory_list(
                raw_channel_defaults.get("codex_workspace_write_writable_roots"),
                "workspaces.channel_defaults.codex_workspace_write_writable_roots",
                base_dir,
            )
            if "codex_workspace_write_writable_roots" in raw_channel_defaults
            else (
                list(defaults.codex_workspace_write_writable_roots)
                if defaults.codex_workspace_write_writable_roots is not None
                else None
            )
        ),
        post_terminal_threads_here=(
            _optional_bool(
                raw_channel_defaults.get("post_terminal_threads_here"),
                "workspaces.channel_defaults.post_terminal_threads_here",
            )
            if "post_terminal_threads_here" in raw_channel_defaults
            else None
        ),
        persistent_memory_mode=(
            _persistent_memory_mode(
                raw_channel_defaults.get("persistent_memory_mode"),
                "workspaces.channel_defaults.persistent_memory_mode",
            )
            if "persistent_memory_mode" in raw_channel_defaults
            else None
        ),
        persistent_memory_owner=(
            _optional_string(
                raw_channel_defaults.get("persistent_memory_owner"),
                "workspaces.channel_defaults.persistent_memory_owner",
            )
            if "persistent_memory_owner" in raw_channel_defaults
            else None
        ),
        slack_channel_id=(
            _optional_string(
                raw_channel_defaults.get("slack_channel_id"),
                "workspaces.channel_defaults.slack_channel_id",
            )
            if "slack_channel_id" in raw_channel_defaults
            else None
        ),
    )


def _has_workspace_channel_defaults(channel_defaults: WorkspaceChannelDefaults) -> bool:
    return any(
        (
            channel_defaults.allowed_actor_ids is not None,
            channel_defaults.default_cwd is not None,
            channel_defaults.additional_roots is not None,
            channel_defaults.accept_root_bob_requests is not None,
            channel_defaults.post_terminal_threads_here is not None,
            channel_defaults.codex_home_mode != CODEX_HOME_MODE_DEFAULT,
            channel_defaults.codex_sandbox_mode is not None,
            channel_defaults.codex_workspace_write_writable_roots is not None,
            channel_defaults.persistent_memory_mode is not None,
            channel_defaults.persistent_memory_owner is not None,
            channel_defaults.slack_channel_id is not None,
        )
    )


def _string_list(value: Any, field_name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ConfigError("{0} must be a list of strings.".format(field_name))
    return list(value)


def _int_list(value: Any, field_name: str) -> List[int]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(type(item) is int for item in value):
        raise ConfigError("{0} must be a list of integers.".format(field_name))
    return list(value)


def _optional_bool(value: Any, field_name: str, default: Optional[bool] = None) -> Optional[bool]:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError("{0} must be a boolean.".format(field_name))
    return value


def _optional_int(value: Any, field_name: str) -> Optional[int]:
    if value is None:
        return None
    if type(value) is not int:
        raise ConfigError("{0} must be an integer.".format(field_name))
    return value


def _positive_int(value: Any, field_name: str, default: int) -> int:
    if value is None:
        return default
    if type(value) is not int or value <= 0:
        raise ConfigError("{0} must be a positive integer.".format(field_name))
    return value


def _optional_positive_float(
    value: Any,
    field_name: str,
    default: Optional[float] = None,
) -> Optional[float]:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ConfigError("{0} must be a positive number.".format(field_name))
    return float(value)


def _optional_https_url(
    value: Any,
    field_name: str,
    default: Optional[str] = None,
) -> Optional[str]:
    if value is None:
        return default
    return _https_url(value, field_name)


def _optional_url(
    value: Any,
    field_name: str,
    default: Optional[str] = None,
) -> Optional[str]:
    if value is None:
        return default
    return _url(value, field_name)


def _url(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("{0} must be a non-empty string.".format(field_name))
    normalized = value.strip()
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        raise ConfigError("{0} must be an absolute URL.".format(field_name))
    return normalized


def _https_url(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("{0} must be a non-empty string.".format(field_name))
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigError("{0} must be an https URL.".format(field_name))
    return normalized


def _browser_mode(value: Any, field_name: str, default: str) -> str:
    if value is None:
        return default
    if value not in (SHARED_BROWSER_MODE, DEDICATED_BROWSER_MODE):
        raise ConfigError(
            "{0} must be one of: {1}, {2}.".format(
                field_name,
                SHARED_BROWSER_MODE,
                DEDICATED_BROWSER_MODE,
            )
        )
    return value


def _optional_directory_path(value: Any, field_name: str, base_dir: Path) -> Optional[str]:
    if value is None:
        return None
    return _directory_path(value, field_name, base_dir)


def _optional_string(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("{0} must be a non-empty string.".format(field_name))
    return value.strip()


def _persistent_memory_mode(value: Any, field_name: str) -> str:
    if value is None:
        raise ConfigError("{0} is required.".format(field_name))
    if value not in (PERSISTENT_MEMORY_MODE_OWNER_ONLY, PERSISTENT_MEMORY_MODE_DISABLED):
        raise ConfigError(
            "{0} must be one of: {1}, {2}.".format(
                field_name,
                PERSISTENT_MEMORY_MODE_OWNER_ONLY,
                PERSISTENT_MEMORY_MODE_DISABLED,
            )
        )
    return value


def _codex_home_mode(value: Any, field_name: str, default: str) -> str:
    if value is None:
        return default
    if value not in (CODEX_HOME_MODE_DEFAULT, CODEX_HOME_MODE_ISOLATED):
        raise ConfigError(
            "{0} must be one of: {1}, {2}.".format(
                field_name,
                CODEX_HOME_MODE_DEFAULT,
                CODEX_HOME_MODE_ISOLATED,
            )
        )
    return value


def _optional_codex_home_mode(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    return _codex_home_mode(value, field_name, default=CODEX_HOME_MODE_DEFAULT)


def _codex_sandbox_mode(value: Any, field_name: str) -> str:
    if value not in (
        CODEX_SANDBOX_MODE_READ_ONLY,
        CODEX_SANDBOX_MODE_WORKSPACE_WRITE,
        CODEX_SANDBOX_MODE_DANGER_FULL_ACCESS,
    ):
        raise ConfigError(
            "{0} must be one of: {1}, {2}, {3}.".format(
                field_name,
                CODEX_SANDBOX_MODE_READ_ONLY,
                CODEX_SANDBOX_MODE_WORKSPACE_WRITE,
                CODEX_SANDBOX_MODE_DANGER_FULL_ACCESS,
            )
        )
    return value


def _optional_codex_sandbox_mode(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    return _codex_sandbox_mode(value, field_name)


def _validate_channel_memory_policy(channel: ChannelConfig) -> ChannelConfig:
    effective_mode = channel.effective_persistent_memory_mode
    effective_owner = channel.effective_persistent_memory_owner
    if effective_mode == PERSISTENT_MEMORY_MODE_OWNER_ONLY:
        if effective_owner is None:
            raise ConfigError(
                "channel.persistent_memory_owner is required when "
                "channel.persistent_memory_mode is owner_only."
            )
        return channel

    if effective_owner is not None:
        raise ConfigError(
            "channel.persistent_memory_owner is only allowed when "
            "channel.persistent_memory_mode is owner_only."
        )
    return channel


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_number(value: Union[int, float]) -> str:
    if isinstance(value, bool):
        raise TypeError("Boolean values are not valid config numbers.")
    if isinstance(value, int):
        return str(value)
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def _directory_list(value: Any, field_name: str, base_dir: Path) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError("{0} must be a list of directory paths.".format(field_name))
    return [_directory_path(item, field_name, base_dir) for item in value]


def _optional_directory_list(value: Any, field_name: str, base_dir: Path) -> Optional[List[str]]:
    if value is None:
        return None
    return _directory_list(value, field_name, base_dir)


def _directory_path(value: Any, field_name: str, base_dir: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("{0} must be a non-empty string.".format(field_name))

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    else:
        path = path.resolve()

    if not path.exists() or not path.is_dir():
        raise ConfigError("{0} must point to an existing directory.".format(field_name))

    return str(path)


def _optional_path(value: Any, field_name: str, base_dir: Path) -> Optional[str]:
    if value is None:
        return None
    return _path(value, field_name, base_dir)


def _path(value: Any, field_name: str, base_dir: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("{0} must be a non-empty string.".format(field_name))
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    else:
        path = path.resolve()
    return str(path)
