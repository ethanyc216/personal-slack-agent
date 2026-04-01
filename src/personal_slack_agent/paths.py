from pathlib import Path


def default_state_dir() -> Path:
    return Path.home() / ".local" / "share" / "personal-slack-agent"


def default_config_file() -> Path:
    return Path.home() / ".config" / "personal-slack-agent" / "bob.toml"


def default_log_file() -> Path:
    return default_state_dir() / "logs" / "bob.log"
