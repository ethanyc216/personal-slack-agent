from __future__ import annotations

from collections import deque
from logging import Logger
import re
import time
from typing import Callable, Deque, Dict, Optional, Set, Tuple

from ..callsign import match_assistant_invocation
from ..config import runtime_channel_name, slack_channel_id_from_runtime_channel_name
from ..generated_files import normalize_slack_markdown
from ..models import AppConfig, SessionStatus
from ..state import BobStateStore
from .browser import SlackBrowserAdapter, SlackRootMessage, SlackThreadReplyMessage
from .events import SlackRealtimeEvent
from .websocket_client import SlackWebsocketClient


class SlackWatcher:
    _TERMINAL_SESSION_STATUSES = frozenset(
        (
            SessionStatus.CLOSED_IDLE,
            SessionStatus.CLOSED_TIMEOUT,
            SessionStatus.CLOSED_MANUAL,
            SessionStatus.FAILED,
        )
    )

    def __init__(
        self,
        browser: SlackBrowserAdapter,
        orchestrator,
        state_store: BobStateStore,
        config: AppConfig,
        should_stop: Optional[Callable[[], bool]] = None,
        logger: Optional[Logger] = None,
    ) -> None:
        self.browser = browser
        self.orchestrator = orchestrator
        self.state_store = state_store
        self.config = config
        self._should_stop = should_stop
        self.logger = logger
        self._initialized = False
        self._event_queue: Deque[Tuple[str, SlackRealtimeEvent]] = deque()
        self._channel_name_by_id: Dict[Tuple[str, str], str] = {}
        self._workspace_clients: Dict[str, SlackWebsocketClient] = {}
        self._workspaces_pending_reconcile: Set[str] = set()
        self._threads_pending_reconcile: Set[Tuple[str, str, str]] = set()
        self._thread_reply_backoff_until: Dict[str, float] = {}
        self._terminal_reconcile_cursor: Dict[Tuple[str, str], int] = {}
        self._historical_reconcile_cursor: Dict[str, int] = {}
        self._historical_reconcile_due_at: Dict[str, float] = {}
        self._historical_reconcile_interval_seconds: Dict[str, float] = {}
        self._ultimate_search_cursor: Dict[str, float] = {}

    def run_cycle(self) -> None:
        if self._stop_requested():
            return
        if not self._initialized:
            self._initialize()
        if self._stop_requested():
            return
        reconciled_workspaces = self._reconcile_pending_workspaces()
        if self._stop_requested():
            return
        self._process_event_queue()
        if self._stop_requested():
            return
        self._reconcile_recent_ultimate_invocations()
        if self._stop_requested():
            return
        self._reconcile_all_workspaces(skip_workspaces=reconciled_workspaces)
        if self._stop_requested():
            return
        self._reconcile_recent_ultimate_invocations()
        if self._stop_requested():
            return
        self._reconcile_pending_threads()

    def request_workspace_reconcile(self, workspace_name: str) -> None:
        self._workspaces_pending_reconcile.add(workspace_name)

    def _initialize(self) -> None:
        for workspace in self.config.workspaces:
            if self._stop_requested():
                return
            for channel in workspace.channels:
                channel_id = self.browser.get_channel_id(workspace.name, channel.name)
                self._channel_name_by_id[(workspace.name, channel_id)] = channel.name
            self._register_runtime_channels(workspace.name)

            client = SlackWebsocketClient(
                on_event=lambda event, workspace_name=workspace.name: self._event_queue.append(
                    (workspace_name, event)
                ),
                on_invalid_frame=lambda raw_frame, workspace_name=workspace.name: self._log_debug(
                    "ignored invalid slack frame workspace=%s frame=%s",
                    workspace_name,
                    raw_frame,
                ),
                on_reconnect=lambda attempt, backoff, workspace_name=workspace.name: self._log_debug(
                    "slack websocket disconnected workspace=%s attempt=%s backoff=%.3f",
                    workspace_name,
                    attempt,
                    backoff,
                ),
            )
            self._workspace_clients[workspace.name] = client
            self.browser.subscribe_to_realtime_frames(
                workspace_name=workspace.name,
                on_frame=self._build_frame_handler(workspace.name, client),
                on_disconnect=self._build_disconnect_handler(workspace.name, client),
            )
            self._workspaces_pending_reconcile.add(workspace.name)
        self._initialized = True

    def _build_frame_handler(
        self,
        workspace_name: str,
        client: SlackWebsocketClient,
    ):
        del workspace_name

        def _handle_frame(raw_frame: str) -> None:
            client.reset_reconnect_attempts()
            client.handle_raw_frame(raw_frame)

        return _handle_frame

    def _build_disconnect_handler(
        self,
        workspace_name: str,
        client: SlackWebsocketClient,
    ):
        def _handle_disconnect() -> None:
            client.handle_disconnect()
            self._workspaces_pending_reconcile.add(workspace_name)

        return _handle_disconnect

    def _reconcile_pending_workspaces(self) -> None:
        reconciled_workspaces = set()
        pending = list(self._workspaces_pending_reconcile)
        self._workspaces_pending_reconcile.clear()
        for workspace_name in pending:
            if self._stop_requested():
                return reconciled_workspaces
            workspace = self._workspace_config(workspace_name)
            if workspace is None:
                continue
            reconciled_workspaces.add(workspace_name)
            for channel_name in self._channel_names_for_workspace(workspace_name):
                if self._stop_requested():
                    return reconciled_workspaces
                self.reconcile_channel_since_cursor(workspace_name, channel_name)
                for session in self._sessions_for_periodic_thread_reconcile(
                    workspace_name, channel_name
                ):
                    if self._stop_requested():
                        return reconciled_workspaces
                    self.reconcile_thread_since_cursor(
                        workspace_name=workspace_name,
                        channel_name=channel_name,
                        thread_ts=session.thread_ts,
                    )
            historical_session = self._historical_session_for_workspace_periodic_reconcile(
                workspace_name
            )
            if historical_session is not None:
                self.reconcile_thread_since_cursor(
                    workspace_name=historical_session.workspace_name,
                    channel_name=historical_session.channel_name,
                    thread_ts=historical_session.thread_ts,
                    historical=True,
                )
        return reconciled_workspaces

    def _process_event_queue(self) -> None:
        while self._event_queue:
            if self._stop_requested():
                return
            workspace_name, event = self._event_queue.popleft()
            self.handle_event(workspace_name, event)

    def _reconcile_pending_threads(self) -> None:
        pending = list(self._threads_pending_reconcile)
        for key in pending:
            if self._stop_requested():
                return
            record = self.state_store.get_by_thread(key[0], key[1], key[2])
            if record is None:
                self._threads_pending_reconcile.discard(key)
                continue
            self.reconcile_thread_since_cursor(
                workspace_name=key[0],
                channel_name=key[1],
                thread_ts=key[2],
            )
            self._threads_pending_reconcile.discard(key)

    def _reconcile_all_workspaces(self, skip_workspaces=None) -> None:
        skipped = skip_workspaces or set()
        for workspace in self.config.workspaces:
            if self._stop_requested():
                return
            if workspace.name in skipped:
                continue
            for channel_name in self._channel_names_for_workspace(workspace.name):
                if self._stop_requested():
                    return
                self.reconcile_channel_since_cursor(workspace.name, channel_name)
                for session in self._sessions_for_periodic_thread_reconcile(
                    workspace.name, channel_name
                ):
                    if self._stop_requested():
                        return
                    self.reconcile_thread_since_cursor(
                        workspace_name=workspace.name,
                        channel_name=channel_name,
                        thread_ts=session.thread_ts,
                    )
            historical_session = self._historical_session_for_workspace_periodic_reconcile(
                workspace.name
            )
            if historical_session is not None:
                self.reconcile_thread_since_cursor(
                    workspace_name=historical_session.workspace_name,
                    channel_name=historical_session.channel_name,
                    thread_ts=historical_session.thread_ts,
                    historical=True,
                )

    def handle_event(self, workspace_name: str, event: SlackRealtimeEvent) -> None:
        channel_name = self._channel_name_by_id.get((workspace_name, event.channel_id))
        if channel_name is None and self.config.watcher.bob_ultimate_mode:
            channel_name = runtime_channel_name(event.channel_id)
            self._channel_name_by_id[(workspace_name, event.channel_id)] = channel_name
        if channel_name is None or event.message_ts is None:
            return

        if event.kind == "root_message_seen":
            self._handle_root_event(workspace_name, channel_name, event.message_ts)
            return

        if event.kind == "thread_reply_seen" and event.thread_ts is not None:
            self._handle_thread_reply_event(
                workspace_name,
                channel_name,
                event.thread_ts,
                event.message_ts,
            )

    def reconcile_channel_since_cursor(self, workspace_name: str, channel_name: str) -> None:
        cursor = self.state_store.get_channel_cursor(workspace_name, channel_name)
        latest_boundary = None
        batches = []
        while True:
            if self._stop_requested():
                return
            messages = self.browser.list_root_messages(
                workspace_name,
                channel_name,
                oldest=cursor,
                latest=latest_boundary,
                limit=self.config.watcher.root_batch_size,
            )
            if not messages:
                break
            batches.append(messages)
            oldest_message_ts = messages[0].message_ts
            if not _is_newer_timestamp(oldest_message_ts, cursor):
                break
            latest_boundary = oldest_message_ts
            if len(messages) < self.config.watcher.root_batch_size:
                break
        current_cursor = cursor
        for batch in reversed(batches):
            if self._stop_requested():
                return
            for message in batch:
                if self._stop_requested():
                    return
                if not _is_newer_timestamp(message.message_ts, current_cursor):
                    continue
                self.orchestrator.handle_new_root_message(
                    workspace_name=message.workspace_name,
                    channel_name=message.channel_name,
                    message_ts=message.message_ts,
                    author_actor_id=message.author_actor_id,
                    text=message.text,
                )
                current_cursor = message.message_ts
        if current_cursor is not None and _is_newer_timestamp(current_cursor, cursor):
            self.state_store.upsert_channel_cursor(
                workspace_name,
                channel_name,
                current_cursor,
            )

    def reconcile_thread_since_cursor(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        historical: bool = False,
    ) -> None:
        if self._stop_requested():
            return
        if self._thread_reply_backoff_active(workspace_name):
            return
        record = self.state_store.get_by_thread(workspace_name, channel_name, thread_ts)
        if record is None:
            return
        cursor = self.state_store.get_thread_cursor(workspace_name, channel_name, thread_ts)
        delivered_timestamps = set(
            self.state_store.list_delivered_outbound_message_timestamps(
                workspace_name,
                channel_name,
                thread_ts,
            )
        )
        pending_outbound_texts = self._pending_outbound_texts_for_thread(
            workspace_name,
            channel_name,
            thread_ts,
        )
        current_cursor = cursor
        legacy_thread = self._is_legacy_configured_bob_thread(
            workspace_name,
            channel_name,
            thread_ts,
        )
        while True:
            if self._stop_requested():
                return
            try:
                replies = self.browser.list_thread_replies(
                    workspace_name,
                    channel_name,
                    thread_ts,
                    oldest=current_cursor,
                    limit=self.config.watcher.thread_batch_size,
                )
            except RuntimeError as exc:
                if _is_slack_ratelimited_error(exc):
                    self._record_thread_reply_backoff(workspace_name)
                    if historical:
                        self._record_historical_sweep_rate_limit(workspace_name)
                    self._log_warning(
                        "slack replies reconcile rate-limited workspace=%s channel=%s thread=%s",
                        workspace_name,
                        channel_name,
                        thread_ts,
                    )
                    return
                raise
            if not replies:
                if historical:
                    self._record_historical_sweep_success(workspace_name)
                return
            for reply in replies:
                if self._stop_requested():
                    return
                if not _is_newer_timestamp(reply.message_ts, current_cursor):
                    continue
                if not _should_route_reply(
                    reply,
                    record.created_at,
                    delivered_timestamps,
                    pending_outbound_texts,
                    self._assistant_names_for_record(record),
                ):
                    continue
                if self._should_route_ultimate_invocation(reply.text) and (
                    self._is_runtime_channel(channel_name) or not legacy_thread
                ):
                    self.orchestrator.handle_ultimate_invocation(
                        workspace_name=reply.workspace_name,
                        channel_name=reply.channel_name,
                        thread_ts=reply.thread_ts,
                        message_ts=reply.message_ts,
                        author_actor_id=reply.author_actor_id,
                        text=reply.text,
                    )
                elif not self._is_runtime_channel(channel_name):
                    self.orchestrator.handle_thread_reply(
                        workspace_name=reply.workspace_name,
                        channel_name=reply.channel_name,
                        thread_ts=reply.thread_ts,
                        message_ts=reply.message_ts,
                        author_actor_id=reply.author_actor_id,
                        text=reply.text,
                    )
            latest_reply_ts = replies[-1].message_ts
            if not _is_newer_timestamp(latest_reply_ts, current_cursor):
                return
            self.state_store.upsert_thread_cursor(
                workspace_name,
                channel_name,
                thread_ts,
                latest_reply_ts,
            )
            current_cursor = latest_reply_ts
            if len(replies) < self.config.watcher.thread_batch_size:
                if historical:
                    self._record_historical_sweep_success(workspace_name)
                return

    def _handle_root_event(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
    ) -> None:
        cursor = self.state_store.get_channel_cursor(workspace_name, channel_name)
        if not _is_newer_timestamp(message_ts, cursor):
            return
        message = self._find_root_message(workspace_name, channel_name, message_ts)
        if message is None:
            self._workspaces_pending_reconcile.add(workspace_name)
            return
        self.orchestrator.handle_new_root_message(
            workspace_name=message.workspace_name,
            channel_name=message.channel_name,
            message_ts=message.message_ts,
            author_actor_id=message.author_actor_id,
            text=message.text,
        )
        self.state_store.upsert_channel_cursor(
            workspace_name,
            channel_name,
            message.message_ts,
        )

    def _handle_thread_reply_event(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
    ) -> None:
        record = self.state_store.get_by_thread(workspace_name, channel_name, thread_ts)
        cursor = self.state_store.get_thread_cursor(workspace_name, channel_name, thread_ts)
        if not _is_newer_timestamp(message_ts, cursor):
            return
        reply = self._find_thread_reply(workspace_name, channel_name, thread_ts, message_ts)
        if reply is None:
            self._threads_pending_reconcile.add((workspace_name, channel_name, thread_ts))
            return
        legacy_thread = self._is_legacy_configured_bob_thread(
            workspace_name,
            channel_name,
            thread_ts,
        )
        if self._is_runtime_channel(channel_name) or not legacy_thread:
            if self._should_route_ultimate_invocation(reply.text):
                self.orchestrator.handle_ultimate_invocation(
                    workspace_name=reply.workspace_name,
                    channel_name=reply.channel_name,
                    thread_ts=reply.thread_ts,
                    message_ts=reply.message_ts,
                    author_actor_id=reply.author_actor_id,
                    text=reply.text,
                )
            self.state_store.upsert_thread_cursor(
                workspace_name,
                channel_name,
                thread_ts,
                reply.message_ts,
            )
            return
        if record is None:
            self.state_store.upsert_thread_cursor(
                workspace_name,
                channel_name,
                thread_ts,
                reply.message_ts,
            )
            return
        delivered_timestamps = set(
            self.state_store.list_delivered_outbound_message_timestamps(
                workspace_name,
                channel_name,
                thread_ts,
            )
        )
        pending_outbound_texts = self._pending_outbound_texts_for_thread(
            workspace_name,
            channel_name,
            thread_ts,
        )
        if not _should_route_reply(
            reply,
            record.created_at,
            delivered_timestamps,
            pending_outbound_texts,
            self._assistant_names_for_record(record),
        ):
            self.state_store.upsert_thread_cursor(
                workspace_name,
                channel_name,
                thread_ts,
                reply.message_ts,
            )
            return
        self.orchestrator.handle_thread_reply(
            workspace_name=reply.workspace_name,
            channel_name=reply.channel_name,
            thread_ts=reply.thread_ts,
            message_ts=reply.message_ts,
            author_actor_id=reply.author_actor_id,
            text=reply.text,
        )
        self.state_store.upsert_thread_cursor(
            workspace_name,
            channel_name,
            thread_ts,
            reply.message_ts,
        )

    def _find_root_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
    ) -> Optional[SlackRootMessage]:
        for message in self.browser.list_root_messages(workspace_name, channel_name):
            if message.message_ts == message_ts:
                return message
        return None

    def _find_thread_reply(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
    ) -> Optional[SlackThreadReplyMessage]:
        if self._thread_reply_backoff_active(workspace_name):
            return None
        try:
            replies = self.browser.list_thread_replies(workspace_name, channel_name, thread_ts)
        except RuntimeError as exc:
            if _is_slack_ratelimited_error(exc):
                self._record_thread_reply_backoff(workspace_name)
                self._log_warning(
                    "slack reply hydration rate-limited workspace=%s channel=%s thread=%s",
                    workspace_name,
                    channel_name,
                    thread_ts,
                )
                return None
            raise
        for reply in replies:
            if reply.message_ts == message_ts:
                return reply
        return None

    def _pending_outbound_texts_for_thread(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> Set[str]:
        texts = set()
        for intent in self.state_store.list_pending_outbound_intents():
            if (
                intent.workspace_name != workspace_name
                or intent.channel_name != channel_name
                or intent.thread_ts != thread_ts
                or intent.action != "post_thread_reply"
            ):
                continue
            normalized = _normalize_reply_text(intent.text)
            if normalized:
                texts.add(normalized)
        return texts

    def _workspace_config(self, workspace_name: str):
        for workspace in self.config.workspaces:
            if workspace.name == workspace_name:
                return workspace
        return None

    def _register_runtime_channels(self, workspace_name: str) -> None:
        if not self.config.watcher.bob_ultimate_mode:
            return
        if self._stop_requested():
            return
        try:
            conversation_ids = self.browser.list_accessible_conversation_ids(workspace_name)
        except Exception as exc:
            self._log_warning(
                "runtime channel pre-registration failed workspace=%s error=%s",
                workspace_name,
                exc,
            )
            return
        for conversation_id in conversation_ids:
            if self._stop_requested():
                return
            key = (workspace_name, conversation_id)
            if key in self._channel_name_by_id:
                continue
            self._channel_name_by_id[key] = runtime_channel_name(conversation_id)

    def _reconcile_recent_ultimate_invocations(self) -> None:
        if not self.config.watcher.bob_ultimate_mode:
            return
        for workspace in self.config.workspaces:
            if self._stop_requested():
                return
            self._reconcile_recent_ultimate_invocations_for_workspace(workspace.name)

    def _reconcile_recent_ultimate_invocations_for_workspace(self, workspace_name: str) -> None:
        floor = self._ultimate_search_cursor.get(workspace_name)
        if floor is None:
            floor = time.time()
            self._ultimate_search_cursor[workspace_name] = floor
        if self._stop_requested():
            return
        messages_by_key = {}
        for assistant_name in self.config.defaults.assistant_names:
            if self._stop_requested():
                return
            try:
                messages = self.browser.search_messages(
                    workspace_name=workspace_name,
                    query=assistant_name,
                    count=50,
                    page=1,
                    sort="timestamp",
                    sort_dir="desc",
                )
            except Exception as exc:
                self._log_warning(
                    "ultimate search fallback failed workspace=%s query=%s error=%s",
                    workspace_name,
                    assistant_name,
                    exc,
                )
                continue
            for message in messages:
                messages_by_key[(message.channel_id, message.message_ts)] = message
        for message in sorted(messages_by_key.values(), key=lambda item: item.message_ts):
            if self._stop_requested():
                return
            try:
                message_ts_float = float(message.message_ts)
            except (TypeError, ValueError):
                continue
            if message_ts_float <= floor:
                continue
            if not self._should_route_ultimate_invocation(message.text):
                continue
            if _is_escaped_thread_reply(message.text):
                continue
            channel_name = self._channel_name_by_id.get((workspace_name, message.channel_id))
            if channel_name is None:
                channel_name = runtime_channel_name(message.channel_id)
                self._channel_name_by_id[(workspace_name, message.channel_id)] = channel_name
            thread_ts = message.thread_ts or message.message_ts
            if (
                not self._is_runtime_channel(channel_name)
                and (
                    message.thread_ts is None
                    or message.thread_ts == message.message_ts
                    or self._is_legacy_configured_bob_thread(
                        workspace_name,
                        channel_name,
                        thread_ts,
                    )
                )
            ):
                continue
            self.orchestrator.handle_ultimate_invocation(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message.message_ts,
                author_actor_id=message.author_actor_id,
                text=message.text,
            )

    def _stop_requested(self) -> bool:
        if self._should_stop is None:
            return False
        try:
            return bool(self._should_stop())
        except Exception:
            return False

    def _channel_names_for_workspace(self, workspace_name: str):
        workspace = self._workspace_config(workspace_name)
        if workspace is None:
            return []
        self._register_runtime_channels(workspace_name)
        channel_names = [channel.name for channel in workspace.channels]
        seen = set(channel_names)
        if not self.config.watcher.bob_ultimate_mode:
            return channel_names
        for (item_workspace, _channel_id), channel_name in self._channel_name_by_id.items():
            if item_workspace != workspace_name or channel_name in seen:
                continue
            channel_names.append(channel_name)
            seen.add(channel_name)
        return channel_names

    def _is_runtime_channel(self, channel_name: str) -> bool:
        return slack_channel_id_from_runtime_channel_name(channel_name) is not None

    def _is_legacy_configured_bob_thread(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> bool:
        return (
            not self._is_runtime_channel(channel_name)
            and self.state_store.thread_has_processed_purpose(
                workspace_name,
                channel_name,
                thread_ts,
                "root_request",
            )
        )

    def _should_route_ultimate_invocation(self, text: str) -> bool:
        return (
            self.config.watcher.bob_ultimate_mode
            and match_assistant_invocation(text, self.config.defaults.assistant_names) is not None
        )

    def _assistant_names_for_record(self, record) -> List[str]:
        names = list(self.config.defaults.assistant_names)
        if record.assistant_name.casefold() not in {name.casefold() for name in names}:
            names.append(record.assistant_name)
        return names

    def _sessions_for_periodic_thread_reconcile(
        self,
        workspace_name: str,
        channel_name: str,
    ):
        sessions = self.state_store.list_sessions(workspace_name, channel_name)
        if not sessions:
            return []

        active_sessions = []
        recent_terminal_sessions, _historical_terminal_sessions = self._partition_terminal_sessions(
            sessions
        )
        for session in sessions:
            if session.status not in self._TERMINAL_SESSION_STATUSES:
                active_sessions.append(session)

        return active_sessions + self._rotate_terminal_sessions(
            workspace_name,
            channel_name,
            recent_terminal_sessions,
        )

    def _historical_session_for_workspace_periodic_reconcile(self, workspace_name: str):
        if not self._historical_sweep_due(workspace_name):
            return None

        candidates = []
        workspace = self._workspace_config(workspace_name)
        if workspace is None:
            return None
        for channel_name in self._channel_names_for_workspace(workspace_name):
            sessions = self.state_store.list_sessions(workspace_name, channel_name)
            _recent_sessions, historical_sessions = self._partition_terminal_sessions(sessions)
            candidates.extend(historical_sessions)

        if not candidates:
            self._historical_reconcile_cursor.pop(workspace_name, None)
            return None

        candidates.sort(key=lambda item: item.updated_at, reverse=True)
        start = self._historical_reconcile_cursor.get(workspace_name, 0) % len(candidates)
        selected = candidates[start]
        self._historical_reconcile_cursor[workspace_name] = (start + 1) % len(candidates)
        self._schedule_next_historical_sweep(workspace_name)
        return selected

    def _partition_terminal_sessions(self, sessions):
        terminal_sessions = [
            session
            for session in sessions
            if session.status in self._TERMINAL_SESSION_STATUSES
        ]
        terminal_sessions.sort(key=lambda item: item.updated_at, reverse=True)
        recent_terminal_sessions = terminal_sessions[
            : self.config.watcher.recent_terminal_thread_reconcile_limit
        ]
        recent_keys = {
            (session.workspace_name, session.channel_name, session.thread_ts)
            for session in recent_terminal_sessions
        }
        historical_terminal_sessions = [
            session
            for session in terminal_sessions
            if (session.workspace_name, session.channel_name, session.thread_ts)
            not in recent_keys
        ]
        return recent_terminal_sessions, historical_terminal_sessions

    def _rotate_terminal_sessions(
        self,
        workspace_name: str,
        channel_name: str,
        sessions,
    ):
        if not sessions:
            self._terminal_reconcile_cursor.pop((workspace_name, channel_name), None)
            return []

        key = (workspace_name, channel_name)
        if len(sessions) <= self.config.watcher.periodic_terminal_thread_reconcile_batch_size:
            self._terminal_reconcile_cursor[key] = 0
            return sessions

        start = self._terminal_reconcile_cursor.get(key, 0) % len(sessions)
        selected = [sessions[start]]
        self._terminal_reconcile_cursor[key] = (
            start + self.config.watcher.periodic_terminal_thread_reconcile_batch_size
        ) % len(sessions)
        return selected

    def _historical_sweep_due(self, workspace_name: str) -> bool:
        due_at = self._historical_reconcile_due_at.get(workspace_name)
        if due_at is None:
            return True
        return time.monotonic() >= due_at

    def _schedule_next_historical_sweep(self, workspace_name: str) -> None:
        interval = self._historical_reconcile_interval_seconds.get(
            workspace_name,
            self.config.watcher.historical_terminal_thread_reconcile_base_interval_seconds,
        )
        self._historical_reconcile_due_at[workspace_name] = time.monotonic() + interval

    def _record_historical_sweep_success(self, workspace_name: str) -> None:
        self._historical_reconcile_interval_seconds[workspace_name] = (
            self.config.watcher.historical_terminal_thread_reconcile_base_interval_seconds
        )
        self._schedule_next_historical_sweep(workspace_name)

    def _record_historical_sweep_rate_limit(self, workspace_name: str) -> None:
        current = self._historical_reconcile_interval_seconds.get(
            workspace_name,
            self.config.watcher.historical_terminal_thread_reconcile_base_interval_seconds,
        )
        next_interval = min(
            current * 2.0,
            self.config.watcher.historical_terminal_thread_reconcile_max_interval_seconds,
        )
        self._historical_reconcile_interval_seconds[workspace_name] = next_interval
        self._historical_reconcile_due_at[workspace_name] = time.monotonic() + next_interval

    def _log_debug(self, message: str, *args) -> None:
        if self.logger is None:
            return
        self.logger.debug(message, *args)

    def _log_warning(self, message: str, *args) -> None:
        if self.logger is None:
            return
        self.logger.warning(message, *args)

    def _thread_reply_backoff_active(self, workspace_name: str) -> bool:
        until = self._thread_reply_backoff_until.get(workspace_name)
        if until is None:
            return False
        if time.monotonic() >= until:
            self._thread_reply_backoff_until.pop(workspace_name, None)
            return False
        return True

    def _record_thread_reply_backoff(self, workspace_name: str) -> None:
        self._thread_reply_backoff_until[workspace_name] = (
            time.monotonic() + self.config.watcher.thread_reply_rate_limit_backoff_seconds
        )


def _is_newer_timestamp(message_ts: str, cursor: Optional[str]) -> bool:
    if cursor is None:
        return True
    try:
        return float(message_ts) > float(cursor)
    except (TypeError, ValueError):
        return message_ts != cursor


def _is_slack_ratelimited_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "ratelimited" in text and "conversations.replies" in text


def _should_route_reply(
    reply: SlackThreadReplyMessage,
    session_created_at: int,
    delivered_timestamps: Set[str],
    pending_outbound_texts: Set[str],
    assistant_names: List[str],
) -> bool:
    normalized_text = _normalize_reply_text(reply.text)
    if not normalized_text:
        return False
    if reply.message_ts in delivered_timestamps:
        return False
    if normalized_text in pending_outbound_texts:
        return False
    if _is_assistant_generated_reply_text(normalized_text, assistant_names):
        return False
    if _is_escaped_thread_reply(reply.text):
        return False
    try:
        return float(reply.message_ts) > float(session_created_at)
    except (TypeError, ValueError):
        return False

def _normalize_reply_text(text: str) -> str:
    normalized = normalize_slack_markdown(text or "").strip()
    normalized = normalized.lstrip("_*`~> ")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _is_assistant_generated_reply_text(
    normalized_text: str,
    assistant_names: List[str],
) -> bool:
    match = match_assistant_invocation(normalized_text, assistant_names)
    if match is None:
        return False
    lowered = match.remainder.casefold()
    generated_phrases = (
        "white_check_mark:",
        "is working on it",
        "queued it",
        "needs input",
        "needs approval",
        "timed out",
        "hit an error",
        "denied command request",
        "canceled command request",
    )
    return lowered.startswith(generated_phrases)


def _is_escaped_thread_reply(text: str) -> bool:
    return text.lstrip().startswith("##")
