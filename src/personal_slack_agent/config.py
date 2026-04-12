from pathlib import Path
from typing import Any, List, Mapping, Optional, Union
from urllib.parse import urlparse

from .models import (
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
    ChannelConfig,
    DefaultSettings,
    WorkspaceConfig,
)

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]


class ConfigError(ValueError):
    pass


def apply_channel_defaults(defaults: DefaultSettings, channel: ChannelConfig) -> ChannelConfig:
    channel.effective_default_cwd = channel.default_cwd or defaults.default_cwd
    channel.effective_additional_roots = (
        list(channel.additional_roots)
        if channel.additional_roots is not None
        else list(defaults.additional_roots)
    )
    channel.effective_accept_root_bob_requests = (
        defaults.accept_root_bob_requests
        if channel.accept_root_bob_requests is None
        else channel.accept_root_bob_requests
    )
    channel.effective_codex_home_mode = channel.codex_home_mode or defaults.codex_home_mode
    channel.effective_codex_sandbox_mode = (
        channel.codex_sandbox_mode
        if channel.codex_sandbox_mode is not None
        else defaults.codex_sandbox_mode
    )
    channel.effective_codex_workspace_write_writable_roots = (
        list(channel.codex_workspace_write_writable_roots)
        if channel.codex_workspace_write_writable_roots is not None
        else (
            list(defaults.codex_workspace_write_writable_roots)
            if defaults.codex_workspace_write_writable_roots is not None
            else None
        )
    )
    return channel


def load_config(config_path: Union[str, Path]) -> AppConfig:
    path = Path(config_path).expanduser().resolve()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    defaults = _parse_defaults(raw.get("defaults"), path.parent)
    workspaces = _parse_workspaces(raw.get("workspaces"), defaults, path.parent)
    return AppConfig(defaults=defaults, workspaces=workspaces)


def dump_config(config: AppConfig) -> str:
    lines = [
        "[defaults]",
        'default_cwd = "{0}"'.format(_toml_escape(config.defaults.default_cwd)),
    ]
    if config.defaults.additional_roots:
        lines.append(
            "additional_roots = [{0}]".format(
                ", ".join('"{}"'.format(_toml_escape(item)) for item in config.defaults.additional_roots)
            )
        )
    lines.append(
        "allowed_actor_ids = [{0}]".format(
            ", ".join('"{}"'.format(_toml_escape(item)) for item in config.defaults.allowed_actor_ids)
        )
    )
    lines.append("max_concurrent_tasks = {0}".format(config.defaults.max_concurrent_tasks))
    lines.append(
        "max_concurrent_per_thread = {0}".format(
            config.defaults.max_concurrent_per_thread
        )
    )
    if config.defaults.bob_codex_home is not None:
        lines.append('bob_codex_home = "{0}"'.format(_toml_escape(config.defaults.bob_codex_home)))
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
    lines.append(
        "accept_root_bob_requests = {0}".format("true" if config.defaults.accept_root_bob_requests else "false")
    )
    lines.append('slack_signin_url = "{0}"'.format(_toml_escape(config.defaults.slack_signin_url)))
    lines.append('browser_mode = "{0}"'.format(_toml_escape(config.defaults.browser_mode)))
    lines.append('browser_url = "{0}"'.format(_toml_escape(config.defaults.browser_url)))
    lines.append('cdp_url = "{0}"'.format(_toml_escape(config.defaults.cdp_url)))
    if config.defaults.chrome_executable_path is not None:
        lines.append('chrome_executable_path = "{0}"'.format(_toml_escape(config.defaults.chrome_executable_path)))
    if config.defaults.browser_user_data_dir is not None:
        lines.append('browser_user_data_dir = "{0}"'.format(_toml_escape(config.defaults.browser_user_data_dir)))
    if config.defaults.reminder_minutes:
        lines.append(
            "reminder_minutes = [{0}]".format(", ".join(str(item) for item in config.defaults.reminder_minutes))
        )
    if config.defaults.auto_close_minutes is not None:
        lines.append("auto_close_minutes = {0}".format(config.defaults.auto_close_minutes))

    for workspace in config.workspaces:
        lines.extend(
            [
                "",
                "[[workspaces]]",
                'name = "{0}"'.format(_toml_escape(workspace.name)),
                "allowed_actor_ids = [{0}]".format(
                    ", ".join('"{}"'.format(_toml_escape(item)) for item in workspace.allowed_actor_ids)
                ),
            ]
        )
        if workspace.slack_url is not None:
            lines.append('slack_url = "{0}"'.format(_toml_escape(workspace.slack_url)))
        if workspace.slack_api_origin is not None:
            lines.append('slack_api_origin = "{0}"'.format(_toml_escape(workspace.slack_api_origin)))
        if workspace.slack_api_token is not None:
            lines.append('slack_api_token = "{0}"'.format(_toml_escape(workspace.slack_api_token)))
        for channel in workspace.channels:
            lines.extend(
                [
                    "",
                    "[[workspaces.channels]]",
                    'name = "{0}"'.format(_toml_escape(channel.name)),
                ]
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
            if channel.post_terminal_threads_here:
                lines.append("post_terminal_threads_here = true")
    lines.append("")
    return "\n".join(lines)


def _parse_defaults(raw_defaults: Any, base_dir: Path) -> DefaultSettings:
    if not isinstance(raw_defaults, Mapping):
        raise ConfigError("Missing required [defaults] table.")

    return DefaultSettings(
        default_cwd=_directory_path(raw_defaults.get("default_cwd"), "defaults.default_cwd", base_dir),
        additional_roots=_directory_list(
            raw_defaults.get("additional_roots"),
            "defaults.additional_roots",
            base_dir,
        ),
        accept_root_bob_requests=_optional_bool(raw_defaults.get("accept_root_bob_requests"), "defaults.accept_root_bob_requests", default=True),
        allowed_actor_ids=_string_list(raw_defaults.get("allowed_actor_ids"), "defaults.allowed_actor_ids"),
        max_concurrent_tasks=_positive_int(
            raw_defaults.get("max_concurrent_tasks"),
            "defaults.max_concurrent_tasks",
            default=1,
        ),
        max_concurrent_per_thread=_positive_int(
            raw_defaults.get("max_concurrent_per_thread"),
            "defaults.max_concurrent_per_thread",
            default=1,
        ),
        bob_codex_home=_optional_path(
            raw_defaults.get("bob_codex_home"),
            "defaults.bob_codex_home",
            base_dir=base_dir,
        ),
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
        slack_signin_url=_optional_https_url(
            raw_defaults.get("slack_signin_url"),
            "defaults.slack_signin_url",
            default=DEFAULT_SLACK_SIGNIN_URL,
        ),
        browser_mode=_browser_mode(
            raw_defaults.get("browser_mode"),
            "defaults.browser_mode",
            default=DEDICATED_BROWSER_MODE,
        ),
        browser_url=_optional_url(
            raw_defaults.get("browser_url"),
            "defaults.browser_url",
            default="http://127.0.0.1:9222",
        ),
        cdp_url=_optional_url(
            raw_defaults.get("cdp_url"),
            "defaults.cdp_url",
            default="http://127.0.0.1:9222",
        ),
        chrome_executable_path=_optional_path(
            raw_defaults.get("chrome_executable_path"),
            "defaults.chrome_executable_path",
            base_dir=base_dir,
        ),
        browser_user_data_dir=_optional_path(
            raw_defaults.get("browser_user_data_dir"),
            "defaults.browser_user_data_dir",
            base_dir=base_dir,
        ),
        reminder_minutes=_int_list(raw_defaults.get("reminder_minutes"), "defaults.reminder_minutes"),
        auto_close_minutes=_optional_int(raw_defaults.get("auto_close_minutes"), "defaults.auto_close_minutes"),
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

        channels = _parse_channels(
            raw_workspace.get("channels"),
            defaults=defaults,
            workspace_index=index,
            base_dir=base_dir,
        )
        raw_allowed_actor_ids = raw_workspace.get("allowed_actor_ids")
        if raw_allowed_actor_ids is None:
            allowed_actor_ids = list(defaults.allowed_actor_ids)
        else:
            allowed_actor_ids = _string_list(
                raw_allowed_actor_ids,
                "workspaces.allowed_actor_ids",
            )
        result.append(
            WorkspaceConfig(
                name=name,
                channels=channels,
                allowed_actor_ids=allowed_actor_ids,
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
            post_terminal_threads_here=_optional_bool(
                raw_channel.get("post_terminal_threads_here"),
                "channel.post_terminal_threads_here",
                default=False,
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
            persistent_memory_mode=_persistent_memory_mode(
                raw_channel.get("persistent_memory_mode"),
                "channel.persistent_memory_mode",
            ),
            persistent_memory_owner=_optional_string(
                raw_channel.get("persistent_memory_owner"),
                "channel.persistent_memory_owner",
            ),
            slack_channel_id=_optional_string(
                raw_channel.get("slack_channel_id"),
                "channel.slack_channel_id",
            ),
        )
        channels.append(_validate_channel_memory_policy(apply_channel_defaults(defaults, channel)))
    return channels


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
    if channel.persistent_memory_mode == PERSISTENT_MEMORY_MODE_OWNER_ONLY:
        if channel.persistent_memory_owner is None:
            raise ConfigError(
                "channel.persistent_memory_owner is required when "
                "channel.persistent_memory_mode is owner_only."
            )
        return channel

    if channel.persistent_memory_owner is not None:
        raise ConfigError(
            "channel.persistent_memory_owner is only allowed when "
            "channel.persistent_memory_mode is owner_only."
        )
    return channel


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


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
