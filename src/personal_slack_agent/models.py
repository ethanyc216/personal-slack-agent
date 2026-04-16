from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

DEFAULT_SLACK_SIGNIN_URL = "https://slack.com/signin?entry_point=nav_menu#/signin"
DEFAULT_BROWSER_CDP_URL = "http://127.0.0.1:9222"
SHARED_BROWSER_MODE = "shared_browser"
DEDICATED_BROWSER_MODE = "dedicated_browser"
PERSISTENT_MEMORY_MODE_DISABLED = "disabled"
PERSISTENT_MEMORY_MODE_OWNER_ONLY = "owner_only"
CODEX_HOME_MODE_DEFAULT = "default"
CODEX_HOME_MODE_ISOLATED = "isolated"
CODEX_SANDBOX_MODE_READ_ONLY = "read-only"
CODEX_SANDBOX_MODE_WORKSPACE_WRITE = "workspace-write"
CODEX_SANDBOX_MODE_DANGER_FULL_ACCESS = "danger-full-access"


@dataclass
class DefaultSettings:
    default_cwd: Optional[str] = None
    additional_roots: List[str] = field(default_factory=list)
    accept_root_bob_requests: bool = True
    allowed_actor_ids: List[str] = field(default_factory=list)
    codex_home_mode: str = CODEX_HOME_MODE_DEFAULT
    codex_sandbox_mode: Optional[str] = None
    codex_workspace_write_writable_roots: Optional[List[str]] = None


@dataclass
class BrowserSettings:
    slack_signin_url: str = DEFAULT_SLACK_SIGNIN_URL
    browser_mode: str = DEDICATED_BROWSER_MODE
    browser_url: str = DEFAULT_BROWSER_CDP_URL
    cdp_url: str = DEFAULT_BROWSER_CDP_URL
    chrome_executable_path: Optional[str] = None
    browser_user_data_dir: Optional[str] = None


@dataclass
class RunnerSettings:
    codex_exec_timeout_seconds: Optional[float] = 600.0
    bob_codex_home: Optional[str] = None


@dataclass
class LifecycleSettings:
    reminder_minutes: List[int] = field(default_factory=list)
    auto_close_minutes: Optional[int] = None


@dataclass
class OrchestratorSettings:
    max_concurrent_tasks: int = 1
    max_concurrent_per_thread: int = 1


@dataclass
class WatcherSettings:
    root_batch_size: int = 50
    thread_batch_size: int = 200
    thread_reply_rate_limit_backoff_seconds: float = 60.0
    recent_terminal_thread_reconcile_limit: int = 6
    periodic_terminal_thread_reconcile_batch_size: int = 1
    historical_terminal_thread_reconcile_base_interval_seconds: float = 60.0
    historical_terminal_thread_reconcile_max_interval_seconds: float = 15 * 60.0


@dataclass
class WorkspaceChannelDefaults:
    allowed_actor_ids: Optional[List[str]] = None
    default_cwd: Optional[str] = None
    additional_roots: Optional[List[str]] = None
    accept_root_bob_requests: Optional[bool] = None
    post_terminal_threads_here: Optional[bool] = None
    codex_home_mode: str = CODEX_HOME_MODE_DEFAULT
    codex_sandbox_mode: Optional[str] = None
    codex_workspace_write_writable_roots: Optional[List[str]] = None
    persistent_memory_mode: Optional[str] = None
    persistent_memory_owner: Optional[str] = None
    slack_channel_id: Optional[str] = None


@dataclass
class ChannelConfig:
    name: str
    allowed_actor_ids: Optional[List[str]] = None
    default_cwd: Optional[str] = None
    additional_roots: Optional[List[str]] = None
    accept_root_bob_requests: Optional[bool] = None
    post_terminal_threads_here: Optional[bool] = None
    codex_home_mode: Optional[str] = None
    codex_sandbox_mode: Optional[str] = None
    codex_workspace_write_writable_roots: Optional[List[str]] = None
    persistent_memory_mode: Optional[str] = None
    persistent_memory_owner: Optional[str] = None
    slack_channel_id: Optional[str] = None
    effective_allowed_actor_ids: List[str] = field(default_factory=list)
    effective_default_cwd: str = ""
    effective_additional_roots: List[str] = field(default_factory=list)
    effective_accept_root_bob_requests: bool = True
    effective_post_terminal_threads_here: bool = False
    effective_codex_home_mode: str = CODEX_HOME_MODE_DEFAULT
    effective_codex_sandbox_mode: Optional[str] = None
    effective_codex_workspace_write_writable_roots: Optional[List[str]] = None
    effective_persistent_memory_mode: Optional[str] = None
    effective_persistent_memory_owner: Optional[str] = None
    effective_slack_channel_id: Optional[str] = None


@dataclass
class WorkspaceConfig:
    name: str
    channels: List[ChannelConfig] = field(default_factory=list)
    channel_defaults: WorkspaceChannelDefaults = field(default_factory=WorkspaceChannelDefaults)
    slack_url: Optional[str] = None
    slack_api_origin: Optional[str] = None
    slack_api_token: Optional[str] = None


@dataclass
class AppConfig:
    defaults: DefaultSettings
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    runner: RunnerSettings = field(default_factory=RunnerSettings)
    lifecycle: LifecycleSettings = field(default_factory=LifecycleSettings)
    orchestrator: OrchestratorSettings = field(default_factory=OrchestratorSettings)
    watcher: WatcherSettings = field(default_factory=WatcherSettings)
    workspaces: List[WorkspaceConfig] = field(default_factory=list)


class SessionStatus(str, Enum):
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    CLOSED_IDLE = "closed_idle"
    CLOSED_TIMEOUT = "closed_timeout"
    CLOSED_MANUAL = "closed_manual"
    FAILED = "failed"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


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
class TaskRecord:
    task_id: int
    workspace_name: str
    channel_name: str
    thread_ts: str
    message_ts: str
    author_actor_id: str
    task_kind: str
    prompt_text: str
    codex_session_id: Optional[str]
    status: TaskStatus
    error_text: Optional[str]
    created_at: int
    started_at: Optional[int]
    finished_at: Optional[int]
    updated_at: int


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
