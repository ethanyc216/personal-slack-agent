from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

DEFAULT_SLACK_SIGNIN_URL = "https://slack.com/signin?entry_point=nav_menu#/signin"
DEFAULT_BROWSER_CDP_URL = "http://127.0.0.1:9222"
SHARED_BROWSER_MODE = "shared_browser"
DEDICATED_BROWSER_MODE = "dedicated_browser"
PERSISTENT_MEMORY_MODE_DISABLED = "disabled"
PERSISTENT_MEMORY_MODE_OWNER_ONLY = "owner_only"


@dataclass
class DefaultSettings:
    default_cwd: str
    additional_roots: List[str] = field(default_factory=list)
    accept_root_bob_requests: bool = True
    allowed_actor_ids: List[str] = field(default_factory=list)
    slack_signin_url: str = DEFAULT_SLACK_SIGNIN_URL
    browser_mode: str = DEDICATED_BROWSER_MODE
    browser_url: str = DEFAULT_BROWSER_CDP_URL
    cdp_url: str = DEFAULT_BROWSER_CDP_URL
    chrome_executable_path: Optional[str] = None
    browser_user_data_dir: Optional[str] = None
    reminder_minutes: List[int] = field(default_factory=list)
    auto_close_minutes: Optional[int] = None


@dataclass
class ChannelConfig:
    name: str
    default_cwd: Optional[str] = None
    accept_root_bob_requests: Optional[bool] = None
    post_terminal_threads_here: bool = False
    persistent_memory_mode: str = ""
    persistent_memory_owner: Optional[str] = None
    effective_default_cwd: str = ""
    effective_accept_root_bob_requests: bool = True


@dataclass
class WorkspaceConfig:
    name: str
    channels: List[ChannelConfig] = field(default_factory=list)
    allowed_actor_ids: List[str] = field(default_factory=list)
    slack_url: Optional[str] = None
    slack_api_origin: Optional[str] = None
    slack_api_token: Optional[str] = None


@dataclass
class AppConfig:
    defaults: DefaultSettings
    workspaces: List[WorkspaceConfig] = field(default_factory=list)


class SessionStatus(str, Enum):
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    CLOSED_IDLE = "closed_idle"
    CLOSED_TIMEOUT = "closed_timeout"
    CLOSED_MANUAL = "closed_manual"
    FAILED = "failed"


@dataclass
class SessionRecord:
    workspace_name: str
    channel_name: str
    thread_ts: str
    root_ts: str
    codex_session_id: str
    cwd: str
    owner_actor_id: str
    status: SessionStatus
    waiting_message_ts: Optional[str] = None
    approval_request_id: Optional[str] = None
    approval_command_summary: Optional[str] = None
    reminder_count: int = 0
    reminder_due_at: Optional[int] = None
    auto_close_due_at: Optional[int] = None
    last_summary: Optional[str] = None
    last_error: Optional[str] = None
    created_at: int = 0
    updated_at: int = 0


@dataclass
class OutboundIntentRecord:
    workspace_name: str
    channel_name: str
    thread_ts: str
    intent_key: str
    action: str
    text: str
    delivery_state: str
    delivered: bool
    message_ts: Optional[str]
    created_at: int
    updated_at: int
