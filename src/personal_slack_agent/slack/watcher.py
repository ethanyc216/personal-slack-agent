from __future__ import annotations

from collections import deque
from logging import Logger
import time
from typing import Deque, Dict, Optional, Set, Tuple

from ..models import AppConfig, SessionStatus
from ..state import BobStateStore
from .browser import SlackBrowserAdapter, SlackRootMessage, SlackThreadReplyMessage
from .events import SlackRealtimeEvent
from .websocket_client import SlackWebsocketClient


class SlackWatcher:
    _ROOT_BATCH_SIZE = 50
    _THREAD_BATCH_SIZE = 200
    _THREAD_REPLY_RATE_LIMIT_BACKOFF_SECONDS = 60.0

    def __init__(
        self,
        browser: SlackBrowserAdapter,
        orchestrator,
        state_store: BobStateStore,
        config: AppConfig,
        logger: Optional[Logger] = None,
    ) -> None:
        self.browser = browser
        self.orchestrator = orchestrator
        self.state_store = state_store
        self.config = config
        self.logger = logger
        self._initialized = False
        self._event_queue: Deque[Tuple[str, SlackRealtimeEvent]] = deque()
        self._channel_name_by_id: Dict[Tuple[str, str], str] = {}
        self._workspace_clients: Dict[str, SlackWebsocketClient] = {}
        self._workspaces_pending_reconcile: Set[str] = set()
        self._threads_pending_reconcile: Set[Tuple[str, str, str]] = set()
        self._thread_reply_backoff_until: Dict[str, float] = {}

    def run_cycle(self) -> None:
        if not self._initialized:
            self._initialize()
        self._reconcile_pending_workspaces()
        self._process_event_queue()
        self._reconcile_all_workspaces()
        self._reconcile_pending_threads()

    def request_workspace_reconcile(self, workspace_name: str) -> None:
        self._workspaces_pending_reconcile.add(workspace_name)

    def _initialize(self) -> None:
        for workspace in self.config.workspaces:
            for channel in workspace.channels:
                channel_id = self.browser.get_channel_id(workspace.name, channel.name)
                self._channel_name_by_id[(workspace.name, channel_id)] = channel.name

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
        pending = list(self._workspaces_pending_reconcile)
        self._workspaces_pending_reconcile.clear()
        for workspace_name in pending:
            workspace = self._workspace_config(workspace_name)
            if workspace is None:
                continue
            for channel in workspace.channels:
                self.reconcile_channel_since_cursor(workspace_name, channel.name)
                for session in self.state_store.list_sessions(workspace_name, channel.name):
                    if session.status is SessionStatus.RUNNING:
                        continue
                    self.reconcile_thread_since_cursor(
                        workspace_name=workspace_name,
                        channel_name=channel.name,
                        thread_ts=session.thread_ts,
                    )

    def _process_event_queue(self) -> None:
        while self._event_queue:
            workspace_name, event = self._event_queue.popleft()
            self.handle_event(workspace_name, event)

    def _reconcile_pending_threads(self) -> None:
        pending = list(self._threads_pending_reconcile)
        for key in pending:
            record = self.state_store.get_by_thread(key[0], key[1], key[2])
            if record is None:
                self._threads_pending_reconcile.discard(key)
                continue
            if record.status is SessionStatus.RUNNING:
                continue
            self.reconcile_thread_since_cursor(
                workspace_name=key[0],
                channel_name=key[1],
                thread_ts=key[2],
            )
            self._threads_pending_reconcile.discard(key)

    def _reconcile_all_workspaces(self) -> None:
        for workspace in self.config.workspaces:
            for channel in workspace.channels:
                self.reconcile_channel_since_cursor(workspace.name, channel.name)
                for session in self.state_store.list_sessions(workspace.name, channel.name):
                    if session.status is SessionStatus.RUNNING:
                        continue
                    self.reconcile_thread_since_cursor(
                        workspace_name=workspace.name,
                        channel_name=channel.name,
                        thread_ts=session.thread_ts,
                    )

    def handle_event(self, workspace_name: str, event: SlackRealtimeEvent) -> None:
        channel_name = self._channel_name_by_id.get((workspace_name, event.channel_id))
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
            messages = self.browser.list_root_messages(
                workspace_name,
                channel_name,
                oldest=cursor,
                latest=latest_boundary,
                limit=self._ROOT_BATCH_SIZE,
            )
            if not messages:
                break
            batches.append(messages)
            oldest_message_ts = messages[0].message_ts
            if not _is_newer_timestamp(oldest_message_ts, cursor):
                break
            latest_boundary = oldest_message_ts
            if len(messages) < self._ROOT_BATCH_SIZE:
                break
        current_cursor = cursor
        for batch in reversed(batches):
            for message in batch:
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
    ) -> None:
        if self._thread_reply_backoff_active(workspace_name):
            return
        record = self.state_store.get_by_thread(workspace_name, channel_name, thread_ts)
        if record is None or record.status is SessionStatus.RUNNING:
            return
        cursor = self.state_store.get_thread_cursor(workspace_name, channel_name, thread_ts)
        delivered_timestamps = set(
            self.state_store.list_delivered_outbound_message_timestamps(
                workspace_name,
                channel_name,
                thread_ts,
            )
        )
        current_cursor = cursor
        while True:
            try:
                replies = self.browser.list_thread_replies(
                    workspace_name,
                    channel_name,
                    thread_ts,
                    oldest=current_cursor,
                    limit=self._THREAD_BATCH_SIZE,
                )
            except RuntimeError as exc:
                if _is_slack_ratelimited_error(exc):
                    self._record_thread_reply_backoff(workspace_name)
                    self._log_warning(
                        "slack replies reconcile rate-limited workspace=%s channel=%s thread=%s",
                        workspace_name,
                        channel_name,
                        thread_ts,
                    )
                    return
                raise
            if not replies:
                return
            for reply in replies:
                if not _is_newer_timestamp(reply.message_ts, current_cursor):
                    continue
                if not _should_route_reply(reply, record.created_at, delivered_timestamps):
                    continue
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
            if len(replies) < self._THREAD_BATCH_SIZE:
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
        if record is None:
            return
        if record.status is SessionStatus.RUNNING:
            self._threads_pending_reconcile.add((workspace_name, channel_name, thread_ts))
            return
        cursor = self.state_store.get_thread_cursor(workspace_name, channel_name, thread_ts)
        if not _is_newer_timestamp(message_ts, cursor):
            return
        reply = self._find_thread_reply(workspace_name, channel_name, thread_ts, message_ts)
        if reply is None:
            self._threads_pending_reconcile.add((workspace_name, channel_name, thread_ts))
            return
        delivered_timestamps = set(
            self.state_store.list_delivered_outbound_message_timestamps(
                workspace_name,
                channel_name,
                thread_ts,
            )
        )
        if not _should_route_reply(reply, record.created_at, delivered_timestamps):
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

    def _workspace_config(self, workspace_name: str):
        for workspace in self.config.workspaces:
            if workspace.name == workspace_name:
                return workspace
        return None

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
            time.monotonic() + self._THREAD_REPLY_RATE_LIMIT_BACKOFF_SECONDS
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
) -> bool:
    if not reply.text or not reply.text.strip():
        return False
    if reply.message_ts in delivered_timestamps:
        return False
    if _is_bob_generated_reply_text(reply.text):
        return False
    try:
        return float(reply.message_ts) > float(session_created_at)
    except (TypeError, ValueError):
        return False


def _is_bob_generated_reply_text(text: str) -> bool:
    normalized = text.strip()
    return normalized.startswith("_*codex Bob ") or normalized.startswith("_*Bob ")
