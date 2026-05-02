from concurrent.futures import Future, ThreadPoolExecutor
import time
from typing import Callable, Dict, List, Optional, Protocol, Tuple

from .callsign import (
    assistant_label_from_text,
    is_manual_close_request,
    match_assistant_invocation,
    strip_assistant_prefix,
)
from .config import build_runtime_channel, slack_channel_id_from_runtime_channel_name
from .codex_runner import CodexRunResult
from .generated_files import GeneratedFile, extract_generated_files, normalize_slack_markdown
from .models import (
    AppConfig,
    ChannelConfig,
    OutboundIntentRecord,
    SessionRecord,
    SessionStatus,
    TaskRecord,
    TaskStatus,
)
from .slack import SlackBrowserAdapter, SlackThreadMessage
from .state import BobStateStore


class CodexRunner(Protocol):
    def run_new_session(
        self,
        prompt: str,
        cwd: str,
        additional_roots: List[str],
        sandbox_mode: Optional[str] = None,
        workspace_write_writable_roots: Optional[List[str]] = None,
        on_session_started: Optional[Callable[[str], None]] = None,
    ) -> CodexRunResult:
        ...

    def resume_session(
        self,
        session_id: str,
        prompt: str,
        cwd: str,
        sandbox_mode: Optional[str] = None,
        workspace_write_writable_roots: Optional[List[str]] = None,
    ) -> CodexRunResult:
        ...


class BobOrchestrator:
    _PURPOSE_ROOT_REQUEST = "root_request"
    _PURPOSE_THREAD_REPLY = "thread_reply"
    _PURPOSE_ULTIMATE_INVOCATION = "ultimate_invocation"
    _TASK_KIND_NEW_ROOT = "new_root"
    _TASK_KIND_THREAD_REPLY = "thread_reply"
    _TASK_KIND_ULTIMATE_INVOCATION = "ultimate_invocation"
    _STARTUP_FAILED_SESSION_PREFIX = "startup-failed-"
    def __init__(
        self,
        browser: SlackBrowserAdapter,
        state_store: BobStateStore,
        codex_runner: CodexRunner,
        config: AppConfig,
        isolated_codex_runner: Optional[CodexRunner] = None,
    ) -> None:
        self.browser = browser
        self.state_store = state_store
        self.codex_runner = codex_runner
        self.config = config
        self.isolated_codex_runner = isolated_codex_runner
        self._max_concurrent_tasks = max(1, int(self.config.orchestrator.max_concurrent_tasks))
        self._max_concurrent_per_thread = max(
            1, int(self.config.orchestrator.max_concurrent_per_thread)
        )
        self._worker_pool = ThreadPoolExecutor(
            max_workers=self._max_concurrent_tasks,
            thread_name_prefix="bob-task",
        )
        self._active_tasks: Dict[int, Future[None]] = {}
        self._active_task_threads: Dict[int, Tuple[str, str, str]] = {}
        self._thread_active_counts: Dict[Tuple[str, str, str], int] = {}
        self.state_store.requeue_running_tasks()

    def close(self) -> None:
        self._worker_pool.shutdown(wait=True)

    def handle_new_root_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
        author_actor_id: str,
        text: str,
    ) -> None:
        workspace = self._find_workspace(workspace_name)
        channel = self._find_channel(workspace, channel_name) if workspace else None
        if workspace is None or channel is None:
            return
        if self._should_use_ultimate_mode_for_channel(channel_name):
            self.handle_ultimate_invocation(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=message_ts,
                message_ts=message_ts,
                author_actor_id=author_actor_id,
                text=text,
            )
            return
        invocation = self._match_assistant_invocation(text)
        if invocation is None:
            return
        if not self._is_actor_allowed(workspace_name, channel_name, author_actor_id):
            return

        claimed = self.state_store.claim_processed_message(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=message_ts,
            message_ts=message_ts,
            author_actor_id=author_actor_id,
            purpose=self._PURPOSE_ROOT_REQUEST,
        )
        if not claimed:
            return

        if not channel.effective_accept_root_bob_requests:
            return

        existing = self.state_store.get_by_thread(workspace_name, channel_name, message_ts)
        if existing is not None:
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=message_ts,
                intent_key="duplicate-session-warning",
                text=(
                    "{0} already has a session in this thread: {1}".format(
                        invocation.alias,
                        existing.codex_session_id
                    )
                ),
            )
            return

        self._try_ack_message(
            workspace_name=workspace_name,
            channel_name=channel_name,
            message_ts=message_ts,
        )
        self.state_store.enqueue_task(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=message_ts,
            message_ts=message_ts,
            author_actor_id=author_actor_id,
            task_kind=self._TASK_KIND_NEW_ROOT,
            prompt_text=text,
        )
        self._dispatch_queued_tasks()

    def handle_ultimate_invocation(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        author_actor_id: str,
        text: str,
    ) -> None:
        workspace = self._find_workspace(workspace_name)
        channel = self._find_channel(workspace, channel_name) if workspace else None
        if workspace is None or channel is None:
            return
        existing = self.state_store.get_by_thread(workspace_name, channel_name, thread_ts)
        if not self.config.watcher.bob_ultimate_mode:
            return
        if not self._should_use_ultimate_mode_for_invocation(
            workspace_name,
            channel_name,
            thread_ts,
        ):
            return
        invocation = self._match_assistant_invocation(text)
        if invocation is None:
            return
        if not self._is_actor_allowed(workspace_name, channel_name, author_actor_id):
            return
        if not channel.effective_accept_root_bob_requests:
            return

        claimed = self.state_store.claim_processed_message(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
                message_ts=message_ts,
                author_actor_id=author_actor_id,
                purpose=self._PURPOSE_ULTIMATE_INVOCATION,
        )
        if not claimed:
            return

        self._try_ack_message(
            workspace_name=workspace_name,
            channel_name=channel_name,
            message_ts=message_ts,
        )
        self.state_store.enqueue_task(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            message_ts=message_ts,
            author_actor_id=author_actor_id,
            task_kind=self._TASK_KIND_ULTIMATE_INVOCATION,
            prompt_text=text,
            codex_session_id=existing.codex_session_id if existing is not None else None,
        )
        if self.state_store.count_incomplete_tasks_for_thread(
            workspace_name,
            channel_name,
            thread_ts,
        ) > 1:
            assistant_name = self._assistant_name_from_text(
                text,
                existing.assistant_name if existing is not None else self._default_assistant_name(),
            )
            self._try_append_queued_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message_ts,
                original_text=text,
                assistant_name=assistant_name,
            )
        self._dispatch_queued_tasks()

    def process_scheduled_actions(self, now_epoch: Optional[int] = None) -> None:
        self._drain_completed_tasks()
        current_epoch = int(time.time()) if now_epoch is None else int(now_epoch)

        for record in self.state_store.claim_due_reminders(current_epoch):
            self._deliver_thread_message(
                workspace_name=record.workspace_name,
                channel_name=record.channel_name,
                thread_ts=record.thread_ts,
                intent_key="reminder-{0}-{1}".format(record.thread_ts, record.reminder_count),
                text=self._reminder_text(record),
            )
            next_due = self._next_reminder_due_at(record.reminder_count, current_epoch)
            self.state_store.record_waiting_reminder(
                workspace_name=record.workspace_name,
                channel_name=record.channel_name,
                thread_ts=record.thread_ts,
                reminder_count=record.reminder_count + 1,
                reminder_due_at=next_due,
            )

        for record in self.state_store.claim_due_auto_closes(current_epoch):
            self._clear_waiting_message(record)
            self._deliver_thread_message(
                workspace_name=record.workspace_name,
                channel_name=record.channel_name,
                thread_ts=record.thread_ts,
                intent_key="auto-close-{0}".format(record.thread_ts),
                text=(
                    "{0} Session timed out while waiting. Reply again in this thread to resume.".format(
                        self._label_done(record.assistant_name)
                    )
                ),
            )
            self.state_store.update_status(
                workspace_name=record.workspace_name,
                channel_name=record.channel_name,
                thread_ts=record.thread_ts,
                status=SessionStatus.CLOSED_TIMEOUT,
            )
        self._dispatch_queued_tasks()

    def handle_thread_reply(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        author_actor_id: str,
        text: str,
    ) -> None:
        record = self.state_store.get_by_thread(workspace_name, channel_name, thread_ts)
        if record is None:
            return
        if not self._is_actor_allowed(workspace_name, channel_name, author_actor_id):
            return

        claimed = self.state_store.claim_processed_message(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            message_ts=message_ts,
            author_actor_id=author_actor_id,
            purpose=self._PURPOSE_THREAD_REPLY,
        )
        if not claimed:
            return

        self._try_ack_message(
            workspace_name=workspace_name,
            channel_name=channel_name,
            message_ts=message_ts,
        )
        self.state_store.enqueue_task(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            message_ts=message_ts,
            author_actor_id=author_actor_id,
            task_kind=self._TASK_KIND_THREAD_REPLY,
            prompt_text=text,
            codex_session_id=record.codex_session_id,
        )
        if self.state_store.count_incomplete_tasks_for_thread(
            workspace_name,
            channel_name,
            thread_ts,
        ) > 1:
            assistant_name = self._assistant_name_from_text(text, record.assistant_name)
            self._try_deliver_queued_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message_ts,
                assistant_name=assistant_name,
            )
        self._dispatch_queued_tasks()

    def _handle_approval_reply(
        self,
        record: SessionRecord,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
        text: str,
        author_actor_id: str,
    ) -> None:
        if author_actor_id != record.owner_actor_id and not self._is_actor_allowed(
            workspace_name,
            channel_name,
            author_actor_id,
        ):
            return

        action, approval_id = self._parse_approval_reply(text)
        current_approval_id = record.approval_request_id or ""
        if approval_id != current_approval_id or action not in ("approve", "deny", "cancel"):
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=record.thread_ts,
                intent_key="approval-id-mismatch-{0}".format(message_ts),
                text=self._approval_needed_text(record),
            )
            return

        if action in ("deny", "cancel"):
            self._clear_waiting_message(record)
            action_text = "denied" if action == "deny" else "canceled"
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=record.thread_ts,
                intent_key="approval-{0}-{1}".format(action, approval_id),
                text="_*{0} {1} command request :exclamation:*_ {2}.".format(
                    record.assistant_name,
                    action_text,
                    approval_id,
                ),
            )
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=record.thread_ts,
                status=SessionStatus.CLOSED_MANUAL,
            )
            return

        self._resume_record(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=record.thread_ts,
            message_ts=message_ts,
            session_id=record.codex_session_id,
            prompt=text,
            wrap_prompt=False,
        )

    def _process_run_result(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        session_id: str,
        run_result: CodexRunResult,
        result_key_suffix: str,
        assistant_name: str,
    ) -> None:
        if run_result.wait_kind == "input":
            wait_message = run_result.wait_message or "Please reply in this thread."
            reminder_due_at, auto_close_due_at = self._waiting_deadlines()
            waiting_message_ts = self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key="wait-input-{0}".format(result_key_suffix),
                text="{0} {1}".format(self._label_input(assistant_name), wait_message),
            )
            self.state_store.set_waiting_state(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.WAITING_FOR_INPUT,
                waiting_message_ts=waiting_message_ts,
                approval_request_id=None,
                approval_command_summary=None,
                reminder_due_at=reminder_due_at,
                auto_close_due_at=auto_close_due_at,
            )
            return

        if run_result.wait_kind == "approval":
            approval_request_id = self._extract_approval_request_id(run_result.wait_message)
            if approval_request_id is None:
                approval_request_id = "APR-{0}".format(thread_ts.replace(".", "")[-6:])
            approval_summary = run_result.wait_message or "Command requires approval"
            if self._should_auto_approve(approval_summary, approval_request_id):
                auto_result = self._runner_for_ultimate_invocation(
                    workspace_name,
                    channel_name,
                ).resume_session(
                    session_id,
                    "approve {0}".format(approval_request_id),
                    self._cwd_for_thread(workspace_name, channel_name, thread_ts),
                    sandbox_mode=self._sandbox_mode_for_channel(workspace_name, channel_name),
                    workspace_write_writable_roots=self._workspace_write_writable_roots_for_channel(
                        workspace_name,
                        channel_name,
                    ),
                )
                self._process_run_result(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    session_id=session_id,
                    run_result=auto_result,
                    result_key_suffix="auto-{0}".format(result_key_suffix),
                    assistant_name=assistant_name,
                )
                return
            reminder_due_at, auto_close_due_at = self._waiting_deadlines()
            waiting_message_ts = self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key="wait-approval-{0}-{1}".format(
                    approval_request_id,
                    result_key_suffix,
                ),
                text=(
                    "{0} {1} "
                    "(reply with `approve {2}`, `deny {2}`, or `cancel {2}`)"
                ).format(self._label_approval(assistant_name), approval_summary, approval_request_id),
            )
            self.state_store.set_waiting_state(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.WAITING_FOR_APPROVAL,
                waiting_message_ts=waiting_message_ts,
                approval_request_id=approval_request_id,
                approval_command_summary=approval_summary,
                reminder_due_at=reminder_due_at,
                auto_close_due_at=auto_close_due_at,
            )
            return

        if run_result.final_output:
            self._deliver_final_output(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                session_id=session_id,
                result_key_suffix=result_key_suffix,
                final_output=run_result.final_output,
                assistant_name=assistant_name,
            )
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.CLOSED_IDLE,
            )
            return

        if run_result.failure_text:
            if self._is_exec_timeout_failure(run_result.failure_text):
                self._deliver_thread_message(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    intent_key="timeout-{0}".format(session_id),
                    text=(
                        "{0} {1} Reply again in this thread to resume.".format(
                            self._label_timed_out(assistant_name),
                            run_result.failure_text,
                        )
                    ),
                )
                self.state_store.update_status(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    status=SessionStatus.CLOSED_TIMEOUT,
                    last_error=run_result.failure_text,
                )
            else:
                self._deliver_thread_message(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    intent_key="failure-{0}".format(session_id),
                    text=self._failure_text(assistant_name),
                )
                self.state_store.update_status(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    status=SessionStatus.FAILED,
                    last_error=run_result.failure_text,
                )

    def _deliver_final_output(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        session_id: str,
        result_key_suffix: str,
        final_output: str,
        assistant_name: str,
    ) -> None:
        summary, files = extract_generated_files(final_output)
        if not files:
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key="final-{0}-{1}".format(session_id, result_key_suffix),
                text="{0} {1}".format(self._label_done(assistant_name), final_output),
            )
            return

        uploaded_any = False
        for generated_file in files:
            try:
                self.browser.upload_text_snippet(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    filename=generated_file.path,
                    content=generated_file.content,
                )
                uploaded_any = True
            except Exception:
                if not uploaded_any:
                    self._deliver_thread_message(
                        workspace_name=workspace_name,
                        channel_name=channel_name,
                        thread_ts=thread_ts,
                        intent_key="final-{0}-{1}".format(session_id, result_key_suffix),
                        text="{0} {1}".format(self._label_done(assistant_name), final_output),
                    )
                    return
                raise

        summary_text = summary or "Uploaded generated file snippets."
        file_list = ", ".join("`{0}`".format(item.path) for item in files)
        self._deliver_thread_message(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            intent_key="final-{0}-{1}".format(session_id, result_key_suffix),
            text="{0} {1}\n\nUploaded snippets: {2}".format(
                self._label_done(assistant_name),
                summary_text,
                file_list,
            ),
        )

    def _process_ultimate_run_result(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        original_text: str,
        session_id: str,
        run_result: CodexRunResult,
        result_key_suffix: str,
        assistant_name: str,
    ) -> None:
        if run_result.wait_kind == "input":
            wait_message = (
                run_result.wait_message
                or "Please reply with another `{0} ...` message.".format(assistant_name)
            )
            self._append_message_line_or_fallback(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message_ts,
                original_text=original_text,
                intent_key="ultimate-wait-input-{0}".format(result_key_suffix),
                line="{0} {1}".format(self._label_input(assistant_name), wait_message),
            )
            self.state_store.set_waiting_state(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.WAITING_FOR_INPUT,
                waiting_message_ts=None,
                approval_request_id=None,
                approval_command_summary=None,
                reminder_due_at=None,
                auto_close_due_at=None,
            )
            return

        if run_result.wait_kind == "approval":
            approval_request_id = self._extract_approval_request_id(run_result.wait_message)
            if approval_request_id is None:
                approval_request_id = "APR-{0}".format(thread_ts.replace(".", "")[-6:])
            approval_summary = run_result.wait_message or "Command requires approval"
            if self._should_auto_approve(approval_summary, approval_request_id):
                auto_result = self._runner_for_ultimate_invocation(
                    workspace_name,
                    channel_name,
                ).resume_session(
                    session_id,
                    "approve {0}".format(approval_request_id),
                    self._cwd_for_thread(workspace_name, channel_name, thread_ts),
                    sandbox_mode=self._sandbox_mode_for_channel(workspace_name, channel_name),
                    workspace_write_writable_roots=self._workspace_write_writable_roots_for_channel(
                        workspace_name,
                        channel_name,
                    ),
                )
                self._process_ultimate_run_result(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    message_ts=message_ts,
                    original_text=original_text,
                    session_id=session_id,
                    run_result=auto_result,
                    result_key_suffix="auto-{0}".format(result_key_suffix),
                    assistant_name=assistant_name,
                )
                return
            self._append_message_line_or_fallback(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message_ts,
                original_text=original_text,
                intent_key="ultimate-wait-approval-{0}-{1}".format(
                    approval_request_id,
                    result_key_suffix,
                ),
                line=(
                    "{label} {summary} "
                    "(send `{alias} approve {approval_id}`, "
                    "`{alias} deny {approval_id}`, or `{alias} cancel {approval_id}`)"
                ).format(
                    label=self._label_approval(assistant_name),
                    summary=approval_summary,
                    alias=assistant_name,
                    approval_id=approval_request_id,
                ),
            )
            self.state_store.set_waiting_state(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.WAITING_FOR_APPROVAL,
                waiting_message_ts=None,
                approval_request_id=approval_request_id,
                approval_command_summary=approval_summary,
                reminder_due_at=None,
                auto_close_due_at=None,
            )
            return

        if run_result.final_output:
            self._deliver_ultimate_final_output(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message_ts,
                original_text=original_text,
                session_id=session_id,
                result_key_suffix=result_key_suffix,
                final_output=run_result.final_output,
                assistant_name=assistant_name,
            )
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.CLOSED_IDLE,
            )
            return

        if run_result.failure_text:
            if self._is_exec_timeout_failure(run_result.failure_text):
                self._append_message_line_or_fallback(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    message_ts=message_ts,
                    original_text=original_text,
                    intent_key="ultimate-timeout-{0}".format(session_id),
                    line="{0} {1}".format(
                        self._label_timed_out(assistant_name),
                        run_result.failure_text,
                    ),
                )
                self.state_store.update_status(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    status=SessionStatus.CLOSED_TIMEOUT,
                    last_error=run_result.failure_text,
                )
            else:
                self._append_message_line_or_fallback(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    message_ts=message_ts,
                    original_text=original_text,
                    intent_key="ultimate-failure-{0}".format(session_id),
                    line=self._failure_text(assistant_name),
                )
                self.state_store.update_status(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    status=SessionStatus.FAILED,
                    last_error=run_result.failure_text,
                )

    def _deliver_ultimate_final_output(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        original_text: str,
        session_id: str,
        result_key_suffix: str,
        final_output: str,
        assistant_name: str,
    ) -> None:
        summary, files = extract_generated_files(final_output)
        if not files:
            self._append_message_line_or_fallback(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message_ts,
                original_text=original_text,
                intent_key="ultimate-final-{0}-{1}".format(session_id, result_key_suffix),
                line="{0} {1}".format(self._label_done(assistant_name), final_output),
            )
            return

        uploaded_any = False
        for generated_file in files:
            try:
                self.browser.upload_text_snippet(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    filename=generated_file.path,
                    content=generated_file.content,
                )
                uploaded_any = True
            except Exception:
                if not uploaded_any:
                    self._append_message_line_or_fallback(
                        workspace_name=workspace_name,
                        channel_name=channel_name,
                        thread_ts=thread_ts,
                        message_ts=message_ts,
                        original_text=original_text,
                        intent_key="ultimate-final-{0}-{1}".format(session_id, result_key_suffix),
                        line="{0} {1}".format(self._label_done(assistant_name), final_output),
                    )
                    return
                raise

        summary_text = summary or "Uploaded generated file snippets."
        file_list = ", ".join("`{0}`".format(item.path) for item in files)
        self._append_message_line_or_fallback(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            message_ts=message_ts,
            original_text=original_text,
            intent_key="ultimate-final-{0}-{1}".format(session_id, result_key_suffix),
            line="{0} {1}\n\nUploaded snippets: {2}".format(
                self._label_done(assistant_name),
                summary_text,
                file_list,
            ),
        )

    def _dispatch_queued_tasks(self) -> None:
        self._drain_completed_tasks()
        available_slots = self._max_concurrent_tasks - len(self._active_tasks)
        if available_slots <= 0:
            return

        for task in self.state_store.list_tasks(status=TaskStatus.QUEUED):
            if available_slots <= 0:
                break
            thread_key = (task.workspace_name, task.channel_name, task.thread_ts)
            if self._thread_active_counts.get(thread_key, 0) >= self._max_concurrent_per_thread:
                continue
            claimed_task = self.state_store.claim_task(task.task_id)
            if claimed_task is None:
                continue
            if self._max_concurrent_tasks == 1:
                self._run_task_inline(claimed_task)
                available_slots -= 1
                continue
            future = self._worker_pool.submit(self._execute_claimed_task, claimed_task.task_id)
            self._active_tasks[claimed_task.task_id] = future
            self._active_task_threads[claimed_task.task_id] = thread_key
            self._thread_active_counts[thread_key] = self._thread_active_counts.get(thread_key, 0) + 1
            available_slots -= 1

    def _drain_completed_tasks(self) -> None:
        completed_task_ids = [
            task_id for task_id, future in self._active_tasks.items() if future.done()
        ]
        for task_id in completed_task_ids:
            future = self._active_tasks.pop(task_id)
            thread_key = self._active_task_threads.pop(task_id, None)
            if thread_key is not None:
                remaining = self._thread_active_counts.get(thread_key, 0) - 1
                if remaining > 0:
                    self._thread_active_counts[thread_key] = remaining
                else:
                    self._thread_active_counts.pop(thread_key, None)
            try:
                future.result()
            except Exception:
                continue

    def _execute_claimed_task(self, task_id: int) -> None:
        task = self.state_store.get_task(task_id)
        if task is None:
            return
        try:
            if task.task_kind == self._TASK_KIND_NEW_ROOT:
                self._execute_new_root_task(task)
            elif task.task_kind == self._TASK_KIND_THREAD_REPLY:
                self._execute_thread_reply_task(task)
            elif task.task_kind == self._TASK_KIND_ULTIMATE_INVOCATION:
                self._execute_ultimate_invocation_task(task)
            else:
                raise ValueError("Unknown task kind: {0}".format(task.task_kind))
        except Exception as exc:
            self.state_store.mark_task_failed(
                task_id,
                str(exc).strip() or exc.__class__.__name__,
            )
            raise
        self.state_store.mark_task_completed(task_id)

    def _run_task_inline(self, task: TaskRecord) -> None:
        thread_key = (task.workspace_name, task.channel_name, task.thread_ts)
        self._thread_active_counts[thread_key] = self._thread_active_counts.get(thread_key, 0) + 1
        try:
            self._execute_claimed_task(task.task_id)
        finally:
            remaining = self._thread_active_counts.get(thread_key, 0) - 1
            if remaining > 0:
                self._thread_active_counts[thread_key] = remaining
            else:
                self._thread_active_counts.pop(thread_key, None)

    def _execute_new_root_task(self, task: TaskRecord) -> None:
        workspace_name = task.workspace_name
        channel_name = task.channel_name
        message_ts = task.message_ts
        author_actor_id = task.author_actor_id
        assistant_name = self._assistant_name_from_text(
            task.prompt_text,
            self._default_assistant_name(),
        )
        existing = self.state_store.get_by_thread(workspace_name, channel_name, message_ts)
        if existing is not None:
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=message_ts,
                intent_key="duplicate-session-warning",
                text="{0} already has a session in this thread: {1}".format(
                    assistant_name,
                    existing.codex_session_id
                ),
            )
            return

        try:
            self._start_root_session(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=message_ts,
                root_ts=message_ts,
                message_ts=message_ts,
                prompt_text=task.prompt_text,
                owner_actor_id=author_actor_id,
                cwd=self._resolve_default_cwd(workspace_name, channel_name),
                assistant_name=assistant_name,
            )
        except Exception:
            record = self.state_store.get_by_thread(workspace_name, channel_name, message_ts)
            if record is None:
                self.state_store.release_processed_message(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=message_ts,
                    message_ts=message_ts,
                    purpose=self._PURPOSE_ROOT_REQUEST,
                )
            else:
                self.state_store.update_status(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=message_ts,
                    status=SessionStatus.FAILED,
                )
            raise

    def _start_root_session(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        root_ts: str,
        message_ts: str,
        prompt_text: str,
        owner_actor_id: str,
        cwd: str,
        assistant_name: str,
    ) -> None:
        channel = self._find_channel(self._find_workspace(workspace_name), channel_name)
        assert channel is not None
        started_session_id: Optional[str] = None

        def _on_session_started(session_id: str) -> None:
            nonlocal started_session_id
            started_session_id = session_id
            self.state_store.upsert_session(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                root_ts=root_ts,
                codex_session_id=session_id,
                cwd=cwd,
                owner_actor_id=owner_actor_id,
                status=SessionStatus.RUNNING,
                assistant_name=assistant_name,
            )
            self._try_deliver_working_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key="start-status-{0}".format(session_id),
                session_id=session_id,
                assistant_name=assistant_name,
            )

        run_result = self._runner_for_channel(workspace_name, channel_name).run_new_session(
            prompt=self._build_codex_prompt(
                workspace_name,
                channel_name,
                prompt_text,
                assistant_name=assistant_name,
            ),
            cwd=cwd,
            additional_roots=list(channel.effective_additional_roots),
            sandbox_mode=self._sandbox_mode_for_channel(workspace_name, channel_name),
            workspace_write_writable_roots=self._workspace_write_writable_roots_for_channel(
                workspace_name,
                channel_name,
            ),
            on_session_started=_on_session_started,
        )
        session_id = run_result.session_id or started_session_id
        if session_id is None:
            session_id = self._startup_failed_session_id(message_ts)
            self.state_store.upsert_session(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                root_ts=root_ts,
                codex_session_id=session_id,
                cwd=cwd,
                owner_actor_id=owner_actor_id,
                status=SessionStatus.RUNNING,
                assistant_name=assistant_name,
            )
            self._process_run_result(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                session_id=session_id,
                run_result=run_result,
                result_key_suffix=message_ts,
                assistant_name=assistant_name,
            )
            return
        self.state_store.upsert_session(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            root_ts=root_ts,
            codex_session_id=session_id,
            cwd=cwd,
            owner_actor_id=owner_actor_id,
            status=SessionStatus.RUNNING,
            assistant_name=assistant_name,
        )
        if started_session_id is None:
            self._try_deliver_working_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key="start-status-{0}".format(session_id),
                session_id=session_id,
                assistant_name=assistant_name,
            )
        self._process_run_result(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            session_id=session_id,
            run_result=run_result,
            result_key_suffix=message_ts,
            assistant_name=assistant_name,
        )

    def _execute_thread_reply_task(self, task: TaskRecord) -> None:
        record = self.state_store.get_by_thread(
            task.workspace_name,
            task.channel_name,
            task.thread_ts,
        )
        if record is None:
            return

        assistant_name = self._assistant_name_from_text(
            task.prompt_text,
            record.assistant_name,
        )
        if self._is_manual_close_request(task.prompt_text):
            self._clear_waiting_message(record)
            self.state_store.update_assistant_name(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                assistant_name=assistant_name,
            )
            self.state_store.update_status(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                status=SessionStatus.CLOSED_MANUAL,
            )
            self._deliver_thread_message(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                intent_key="manual-close-{0}".format(task.message_ts),
                text="{0} Session closed. Reply again in this thread to resume.".format(
                    self._label_done(assistant_name)
                ),
            )
            return

        if record.status is SessionStatus.WAITING_FOR_APPROVAL:
            self._handle_approval_reply(
                record=record,
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                message_ts=task.message_ts,
                text=task.prompt_text,
                author_actor_id=task.author_actor_id,
            )
            return

        if self._is_startup_failed_session_id(record.codex_session_id):
            self._restart_startup_failed_root(task, record)
            return

        if record.status is SessionStatus.WAITING_FOR_INPUT:
            self._resume_record(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                message_ts=task.message_ts,
                session_id=record.codex_session_id,
                prompt=task.prompt_text,
            )
            return

        if record.status in (
            SessionStatus.CLOSED_IDLE,
            SessionStatus.CLOSED_TIMEOUT,
            SessionStatus.CLOSED_MANUAL,
            SessionStatus.FAILED,
        ):
            self._resume_record(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                message_ts=task.message_ts,
                session_id=record.codex_session_id,
                prompt=task.prompt_text,
            )

    def _execute_ultimate_invocation_task(self, task: TaskRecord) -> None:
        record = self.state_store.get_by_thread(
            task.workspace_name,
            task.channel_name,
            task.thread_ts,
        )
        assistant_name = self._assistant_name_from_text(
            task.prompt_text,
            record.assistant_name if record is not None else self._default_assistant_name(),
        )
        if record is not None and record.status is SessionStatus.WAITING_FOR_APPROVAL:
            self._handle_ultimate_approval_invocation(task, record)
            return
        thread_messages = self.browser.list_thread_messages(
            task.workspace_name,
            task.channel_name,
            task.thread_ts,
        )
        if not thread_messages:
            thread_messages = [
                SlackThreadMessage(
                    workspace_name=task.workspace_name,
                    channel_name=task.channel_name,
                    thread_ts=task.thread_ts,
                    message_ts=task.message_ts,
                    author_actor_id=task.author_actor_id,
                    text=task.prompt_text,
                )
            ]
        prompt = self._build_ultimate_prompt(
            workspace_name=task.workspace_name,
            channel_name=task.channel_name,
            user_text=task.prompt_text,
            invocation_message_ts=task.message_ts,
            thread_messages=thread_messages,
            assistant_name=assistant_name,
        )
        if record is None:
            self._execute_new_ultimate_session(task, prompt, assistant_name)
            return
        self._resume_ultimate_session(task, record, prompt, assistant_name)

    def _handle_ultimate_approval_invocation(
        self,
        task: TaskRecord,
        record: SessionRecord,
    ) -> None:
        assistant_name = self._assistant_name_from_text(task.prompt_text, record.assistant_name)
        self.state_store.update_assistant_name(
            workspace_name=task.workspace_name,
            channel_name=task.channel_name,
            thread_ts=task.thread_ts,
            assistant_name=assistant_name,
        )
        approval_text = self._strip_bob_prefix(task.prompt_text)
        action, approval_id = self._parse_approval_reply(approval_text)
        current_approval_id = record.approval_request_id or ""
        if approval_id != current_approval_id or action not in ("approve", "deny", "cancel"):
            self._append_message_line_or_fallback(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                message_ts=task.message_ts,
                original_text=task.prompt_text,
                intent_key="ultimate-approval-id-mismatch-{0}".format(task.message_ts),
                line=self._approval_needed_text(record),
            )
            return

        if action in ("deny", "cancel"):
            action_text = "denied" if action == "deny" else "canceled"
            self.state_store.update_status(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                status=SessionStatus.CLOSED_MANUAL,
            )
            self._append_message_line_or_fallback(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                message_ts=task.message_ts,
                original_text=task.prompt_text,
                intent_key="ultimate-approval-{0}-{1}".format(action, approval_id),
                line="_*{0} {1} command request :exclamation:*_ {2}.".format(
                    assistant_name,
                    action_text,
                    approval_id,
                ),
            )
            return

        previous_status = record.status
        self.state_store.update_status(
            workspace_name=task.workspace_name,
            channel_name=task.channel_name,
            thread_ts=task.thread_ts,
            status=SessionStatus.RUNNING,
            clear_waiting_fields=False,
        )
        self._try_append_working_message(
            workspace_name=task.workspace_name,
            channel_name=task.channel_name,
            thread_ts=task.thread_ts,
            message_ts=task.message_ts,
            original_text=task.prompt_text,
            intent_key="ultimate-start-status-{0}".format(task.message_ts),
            session_id=record.codex_session_id,
            assistant_name=assistant_name,
        )
        try:
            run_result = self._runner_for_ultimate_invocation(
                task.workspace_name,
                task.channel_name,
            ).resume_session(
                record.codex_session_id,
                approval_text,
                record.cwd,
                sandbox_mode=self._sandbox_mode_for_channel(task.workspace_name, task.channel_name),
                workspace_write_writable_roots=self._workspace_write_writable_roots_for_channel(
                    task.workspace_name,
                    task.channel_name,
                ),
            )
        except Exception:
            self.state_store.release_processed_message(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                message_ts=task.message_ts,
                purpose=self._PURPOSE_ULTIMATE_INVOCATION,
            )
            self.state_store.update_status(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                status=previous_status,
                clear_waiting_fields=False,
            )
            raise
        self._process_ultimate_run_result(
            workspace_name=task.workspace_name,
            channel_name=task.channel_name,
            thread_ts=task.thread_ts,
            message_ts=task.message_ts,
            original_text=task.prompt_text,
            session_id=record.codex_session_id,
            run_result=run_result,
            result_key_suffix=task.message_ts,
            assistant_name=assistant_name,
        )

    def _execute_new_ultimate_session(
        self,
        task: TaskRecord,
        prompt: str,
        assistant_name: str,
    ) -> None:
        cwd = self._resolve_default_cwd(task.workspace_name, task.channel_name)
        started_session_id: Optional[str] = None

        def _on_session_started(session_id: str) -> None:
            nonlocal started_session_id
            started_session_id = session_id
            self.state_store.upsert_session(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                root_ts=task.thread_ts,
                codex_session_id=session_id,
                cwd=cwd,
                owner_actor_id=task.author_actor_id,
                status=SessionStatus.RUNNING,
                assistant_name=assistant_name,
            )
            self._try_append_working_message(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                message_ts=task.message_ts,
                original_text=task.prompt_text,
                intent_key="ultimate-start-status-{0}".format(task.message_ts),
                session_id=session_id,
                assistant_name=assistant_name,
                redeliver_existing=True,
            )

        try:
            run_result = self._runner_for_ultimate_invocation(
                task.workspace_name,
                task.channel_name,
            ).run_new_session(
                prompt=prompt,
                cwd=cwd,
                additional_roots=list(
                    self._find_channel(
                        self._find_workspace(task.workspace_name),
                        task.channel_name,
                    ).effective_additional_roots
                ),
                sandbox_mode=self._sandbox_mode_for_channel(task.workspace_name, task.channel_name),
                workspace_write_writable_roots=self._workspace_write_writable_roots_for_channel(
                    task.workspace_name,
                    task.channel_name,
                ),
                on_session_started=_on_session_started,
            )
            session_id = run_result.session_id or started_session_id
            if session_id is None:
                session_id = self._startup_failed_session_id(task.message_ts)
                self.state_store.upsert_session(
                    workspace_name=task.workspace_name,
                    channel_name=task.channel_name,
                    thread_ts=task.thread_ts,
                    root_ts=task.thread_ts,
                    codex_session_id=session_id,
                    cwd=cwd,
                    owner_actor_id=task.author_actor_id,
                    status=SessionStatus.RUNNING,
                    assistant_name=assistant_name,
                )
                self._process_ultimate_run_result(
                    workspace_name=task.workspace_name,
                    channel_name=task.channel_name,
                    thread_ts=task.thread_ts,
                    message_ts=task.message_ts,
                    original_text=task.prompt_text,
                    session_id=session_id,
                    run_result=run_result,
                    result_key_suffix=task.message_ts,
                    assistant_name=assistant_name,
                )
                return
            self.state_store.upsert_session(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                root_ts=task.thread_ts,
                codex_session_id=session_id,
                cwd=cwd,
                owner_actor_id=task.author_actor_id,
                status=SessionStatus.RUNNING,
                assistant_name=assistant_name,
            )
            if started_session_id is None:
                self._try_append_working_message(
                    workspace_name=task.workspace_name,
                    channel_name=task.channel_name,
                    thread_ts=task.thread_ts,
                    message_ts=task.message_ts,
                    original_text=task.prompt_text,
                    intent_key="ultimate-start-status-{0}".format(task.message_ts),
                    session_id=session_id,
                    assistant_name=assistant_name,
                    redeliver_existing=True,
                )
            self._process_ultimate_run_result(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                message_ts=task.message_ts,
                original_text=task.prompt_text,
                session_id=session_id,
                run_result=run_result,
                result_key_suffix=task.message_ts,
                assistant_name=assistant_name,
            )
        except Exception:
            record = self.state_store.get_by_thread(task.workspace_name, task.channel_name, task.thread_ts)
            if record is None:
                self.state_store.release_processed_message(
                    workspace_name=task.workspace_name,
                    channel_name=task.channel_name,
                    thread_ts=task.thread_ts,
                    message_ts=task.message_ts,
                    purpose=self._PURPOSE_ULTIMATE_INVOCATION,
                )
            else:
                self.state_store.update_status(
                    workspace_name=task.workspace_name,
                    channel_name=task.channel_name,
                    thread_ts=task.thread_ts,
                    status=SessionStatus.FAILED,
                )
            raise

    def _resume_ultimate_session(
        self,
        task: TaskRecord,
        record: SessionRecord,
        prompt: str,
        assistant_name: str,
    ) -> None:
        self.state_store.update_assistant_name(
            workspace_name=task.workspace_name,
            channel_name=task.channel_name,
            thread_ts=task.thread_ts,
            assistant_name=assistant_name,
        )
        previous_status = record.status
        self.state_store.update_status(
            workspace_name=task.workspace_name,
            channel_name=task.channel_name,
            thread_ts=task.thread_ts,
            status=SessionStatus.RUNNING,
            clear_waiting_fields=False,
        )
        self._try_append_working_message(
            workspace_name=task.workspace_name,
            channel_name=task.channel_name,
            thread_ts=task.thread_ts,
            message_ts=task.message_ts,
            original_text=task.prompt_text,
            intent_key="ultimate-start-status-{0}".format(task.message_ts),
            session_id=record.codex_session_id,
            assistant_name=assistant_name,
            redeliver_existing=True,
        )
        try:
            run_result = self._runner_for_ultimate_invocation(
                task.workspace_name,
                task.channel_name,
            ).resume_session(
                record.codex_session_id,
                prompt,
                record.cwd,
                sandbox_mode=self._sandbox_mode_for_channel(task.workspace_name, task.channel_name),
                workspace_write_writable_roots=self._workspace_write_writable_roots_for_channel(
                    task.workspace_name,
                    task.channel_name,
                ),
            )
        except Exception:
            self.state_store.release_processed_message(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                message_ts=task.message_ts,
                purpose=self._PURPOSE_ULTIMATE_INVOCATION,
            )
            self.state_store.update_status(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
                status=previous_status,
                clear_waiting_fields=False,
            )
            raise
        if (
            previous_status in (
                SessionStatus.CLOSED_IDLE,
                SessionStatus.CLOSED_TIMEOUT,
                SessionStatus.CLOSED_MANUAL,
                SessionStatus.FAILED,
            )
            and run_result.failure_text
            and self._is_missing_rollout_failure(run_result.failure_text)
        ):
            self.state_store.delete_session(
                workspace_name=task.workspace_name,
                channel_name=task.channel_name,
                thread_ts=task.thread_ts,
            )
            self._execute_new_ultimate_session(task, prompt, assistant_name)
            return
        if run_result.session_id and run_result.session_id != record.codex_session_id:
            self._rebind_session_id(record, run_result.session_id)
            record = self.state_store.get_by_thread(
                task.workspace_name,
                task.channel_name,
                task.thread_ts,
            ) or record
        self._process_ultimate_run_result(
            workspace_name=task.workspace_name,
            channel_name=task.channel_name,
            thread_ts=task.thread_ts,
            message_ts=task.message_ts,
            original_text=task.prompt_text,
            session_id=record.codex_session_id,
            run_result=run_result,
            result_key_suffix=task.message_ts,
            assistant_name=assistant_name,
        )

    def _resume_record(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        session_id: str,
        prompt: str,
        wrap_prompt: bool = True,
    ) -> None:
        record = self.state_store.get_by_thread(workspace_name, channel_name, thread_ts)
        if record is None:
            return
        assistant_name = self._assistant_name_from_text(prompt, record.assistant_name)
        previous_status = record.status
        self.state_store.update_assistant_name(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            assistant_name=assistant_name,
        )
        self.state_store.update_status(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            status=SessionStatus.RUNNING,
            clear_waiting_fields=False,
        )
        try:
            resume_prompt = (
                self._build_codex_prompt(
                    workspace_name,
                    channel_name,
                    prompt,
                    assistant_name=assistant_name,
                )
                if wrap_prompt
                else prompt
            )
            run_result = self._runner_for_channel(workspace_name, channel_name).resume_session(
                session_id,
                resume_prompt,
                record.cwd,
                sandbox_mode=self._sandbox_mode_for_channel(workspace_name, channel_name),
                workspace_write_writable_roots=self._workspace_write_writable_roots_for_channel(
                    workspace_name,
                    channel_name,
                ),
            )
        except Exception:
            self.state_store.release_processed_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message_ts,
                purpose=self._PURPOSE_THREAD_REPLY,
            )
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=previous_status,
                clear_waiting_fields=False,
            )
            raise
        if previous_status in (
            SessionStatus.WAITING_FOR_INPUT,
            SessionStatus.WAITING_FOR_APPROVAL,
        ):
            self._clear_waiting_message(record)
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.RUNNING,
            )
        if run_result.session_id and run_result.session_id != session_id:
            self._rebind_session_id(record, run_result.session_id)
            session_id = run_result.session_id
        try:
            self._process_run_result(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                session_id=session_id,
                run_result=run_result,
                result_key_suffix=message_ts,
                assistant_name=assistant_name,
            )
        except Exception:
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.FAILED,
            )
            raise

    def _restart_startup_failed_root(
        self,
        task: TaskRecord,
        record: SessionRecord,
    ) -> None:
        root_task = self.state_store.get_latest_task_for_thread(
            task.workspace_name,
            task.channel_name,
            task.thread_ts,
            self._TASK_KIND_NEW_ROOT,
        )
        prompt_text = root_task.prompt_text if root_task is not None else task.prompt_text
        assistant_name = self._assistant_name_from_text(prompt_text, record.assistant_name)
        self.state_store.delete_session(task.workspace_name, task.channel_name, task.thread_ts)
        self._start_root_session(
            workspace_name=task.workspace_name,
            channel_name=task.channel_name,
            thread_ts=task.thread_ts,
            root_ts=record.root_ts,
            message_ts=task.message_ts,
            prompt_text=prompt_text,
            owner_actor_id=record.owner_actor_id,
            cwd=record.cwd,
            assistant_name=assistant_name,
        )

    def _rebind_session_id(self, record: SessionRecord, session_id: str) -> None:
        if session_id == record.codex_session_id:
            return
        self.state_store.delete_session(
            record.workspace_name,
            record.channel_name,
            record.thread_ts,
        )
        self.state_store.upsert_session(
            workspace_name=record.workspace_name,
            channel_name=record.channel_name,
            thread_ts=record.thread_ts,
            root_ts=record.root_ts,
            codex_session_id=session_id,
            cwd=record.cwd,
            owner_actor_id=record.owner_actor_id,
            status=SessionStatus.RUNNING,
            assistant_name=record.assistant_name,
        )

    def _startup_failed_session_id(self, message_ts: str) -> str:
        return "{0}{1}".format(
            self._STARTUP_FAILED_SESSION_PREFIX,
            message_ts.replace(".", "-"),
        )

    def _is_startup_failed_session_id(self, session_id: str) -> bool:
        return session_id == "unknown-session" or session_id.startswith(
            self._STARTUP_FAILED_SESSION_PREFIX
        )

    def _deliver_thread_message(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        intent_key: str,
        text: str,
    ) -> Optional[str]:
        normalized_text = normalize_slack_markdown(text)
        self.state_store.upsert_outbound_intent(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            intent_key=intent_key,
            action="post_thread_reply",
            text=normalized_text,
            delivered=False,
            message_ts=None,
        )
        pending_intent = self._find_pending_intent(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            intent_key=intent_key,
        )
        if pending_intent is None:
            return None

        if pending_intent.delivery_state == "attempted":
            existing_messages = self.browser.find_existing_bob_messages(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
            )
            if normalized_text in existing_messages:
                reconciled_ts = "{0}.reconciled".format(thread_ts.split(".")[0])
                self.state_store.mark_outbound_intent_delivered(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    intent_key=intent_key,
                    message_ts=reconciled_ts,
                )
                return reconciled_ts

        try:
            reply_ts = self.browser.post_thread_reply(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                text=normalized_text,
            )
        except Exception:
            self.state_store.mark_outbound_intent_attempted(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key=intent_key,
            )
            raise

        self.state_store.mark_outbound_intent_delivered(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            intent_key=intent_key,
            message_ts=reply_ts,
        )
        return reply_ts

    def _append_message_line(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        original_text: str,
        intent_key: str,
        line: str,
        redeliver_existing: bool = False,
    ) -> None:
        normalized_line = normalize_slack_markdown(line)
        self.state_store.upsert_outbound_intent(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            intent_key=intent_key,
            action="append_message_line",
            text=normalized_line,
            delivered=False,
            message_ts=None,
        )
        existing_intent = self._find_thread_intent(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            intent_key=intent_key,
        )
        pending_intent = self._find_pending_intent(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            intent_key=intent_key,
        )
        if pending_intent is None and (
            not redeliver_existing
            or existing_intent is None
            or existing_intent.action != "append_message_line"
            or existing_intent.message_ts != message_ts
        ):
            return

        next_text = self._compose_message_with_appended_lines(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            message_ts=message_ts,
            original_text=original_text,
            pending_line=normalized_line if pending_intent is not None else None,
        )
        try:
            self.browser.update_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                message_ts=message_ts,
                text=next_text,
            )
        except Exception:
            if pending_intent is not None:
                self.state_store.mark_outbound_intent_attempted(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    intent_key=intent_key,
                )
            raise

        self.state_store.mark_outbound_intent_delivered(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            intent_key=intent_key,
            message_ts=message_ts,
        )

    def _append_message_line_or_fallback(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        original_text: str,
        intent_key: str,
        line: str,
        redeliver_existing: bool = False,
    ) -> None:
        try:
            self._append_message_line(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message_ts,
                original_text=original_text,
                intent_key=intent_key,
                line=line,
                redeliver_existing=redeliver_existing,
            )
        except Exception:
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key="fallback-{0}".format(intent_key),
                text=line,
            )

    def _compose_message_with_appended_lines(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        original_text: str,
        pending_line: Optional[str] = None,
    ) -> str:
        lines = [normalize_slack_markdown(original_text).rstrip()]
        for intent in self.state_store.list_outbound_intents_for_thread(
            workspace_name,
            channel_name,
            thread_ts,
        ):
            if (
                intent.action == "append_message_line"
                and intent.delivery_state == "delivered"
                and intent.message_ts == message_ts
            ):
                lines.append(intent.text)
        if pending_line is not None:
            lines.append(pending_line)
        return "\n".join(item for item in lines if item)

    def _find_pending_intent(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        intent_key: str,
    ) -> Optional[OutboundIntentRecord]:
        pending = self.state_store.list_pending_outbound_intents()
        for intent in pending:
            if (
                intent.workspace_name == workspace_name
                and intent.channel_name == channel_name
                and intent.thread_ts == thread_ts
                and intent.intent_key == intent_key
            ):
                return intent
        return None

    def _find_thread_intent(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        intent_key: str,
    ) -> Optional[OutboundIntentRecord]:
        for intent in self.state_store.list_outbound_intents_for_thread(
            workspace_name,
            channel_name,
            thread_ts,
        ):
            if intent.intent_key == intent_key:
                return intent
        return None

    def _resolve_default_cwd(self, workspace_name: str, channel_name: str) -> str:
        workspace = self._find_workspace(workspace_name)
        channel = self._find_channel(workspace, channel_name) if workspace else None
        if channel is not None and channel.effective_default_cwd:
            return channel.effective_default_cwd
        if workspace is not None and workspace.channel_defaults.default_cwd:
            return workspace.channel_defaults.default_cwd
        return self.config.defaults.default_cwd or ""

    def _cwd_for_thread(self, workspace_name: str, channel_name: str, thread_ts: str) -> str:
        record = self.state_store.get_by_thread(workspace_name, channel_name, thread_ts)
        if record is not None:
            return record.cwd
        return self._resolve_default_cwd(workspace_name, channel_name)

    def _runner_for_channel(self, workspace_name: str, channel_name: str) -> CodexRunner:
        workspace = self._find_workspace(workspace_name)
        channel = self._find_channel(workspace, channel_name)
        if (
            channel is not None
            and channel.effective_codex_home_mode == "isolated"
            and self.isolated_codex_runner is not None
        ):
            return self.isolated_codex_runner
        if (
            channel is None
            and workspace is not None
            and workspace.channel_defaults.codex_home_mode == "isolated"
            and self.isolated_codex_runner is not None
        ):
            return self.isolated_codex_runner
        return self.codex_runner

    def _runner_for_ultimate_invocation(
        self,
        workspace_name: str,
        channel_name: str,
    ) -> CodexRunner:
        mode = self.config.watcher.bob_ultimate_mode_codex_home_mode
        if mode == "isolated" and self.isolated_codex_runner is not None:
            return self.isolated_codex_runner
        if mode == "default":
            return self.codex_runner
        return self._runner_for_channel(workspace_name, channel_name)

    def _sandbox_mode_for_channel(self, workspace_name: str, channel_name: str) -> Optional[str]:
        workspace = self._find_workspace(workspace_name)
        channel = self._find_channel(workspace, channel_name)
        if channel is None:
            if workspace is not None:
                return workspace.channel_defaults.codex_sandbox_mode
            return self.config.defaults.codex_sandbox_mode
        return channel.effective_codex_sandbox_mode

    def _workspace_write_writable_roots_for_channel(
        self,
        workspace_name: str,
        channel_name: str,
    ) -> Optional[List[str]]:
        workspace = self._find_workspace(workspace_name)
        channel = self._find_channel(workspace, channel_name)
        if channel is None:
            if workspace is not None:
                return workspace.channel_defaults.codex_workspace_write_writable_roots
            return self.config.defaults.codex_workspace_write_writable_roots
        return channel.effective_codex_workspace_write_writable_roots

    def _build_codex_prompt(
        self,
        workspace_name: str,
        channel_name: str,
        user_text: str,
        assistant_name: Optional[str] = None,
    ) -> str:
        workspace = self._find_workspace(workspace_name)
        channel = self._find_channel(workspace, channel_name)
        if channel is None:
            return user_text

        effective_assistant_name = assistant_name or self._assistant_name_from_text(
            user_text,
            self._default_assistant_name(),
        )
        accepted_callsigns = ", ".join(self._assistant_names())
        owner_name = self.config.defaults.owner_name
        owner_preferred_name = self.config.defaults.owner_preferred_name
        effective_mode = channel.effective_persistent_memory_mode or channel.persistent_memory_mode
        owner = channel.effective_persistent_memory_owner or channel.persistent_memory_owner or "none"
        if effective_mode == "owner_only":
            memory_rule = (
                "This Slack channel is allowed to update durable personal preference notes "
                "for owner `{0}` when the conversation reveals a durable preference or workflow rule."
            ).format(owner)
        else:
            memory_rule = (
                "This Slack channel does not grant permission to update {0} / {1}'s "
                "personal durable preference files. Do not update personal session notes or "
                "similar durable preference files for {0} from this conversation. Do not "
                "modify repo-local or global skill files such as `.codex/skills/**`, `SKILL.md`, "
                "or similar skill definitions unless the user explicitly asks you to create or edit them."
            ).format(owner_name, owner_preferred_name)

        return (
            "{7} execution context:\n"
            "- workspace: {0}\n"
            "- channel: {1}\n"
            "- persistent_memory_mode: {2}\n"
            "- persistent_memory_owner: {3}\n\n"
            "{7} role:\n"
            "- {7} is {4}'s personal assistant.\n"
            "- {7} specializes in working on CTDM tickets.\n"
            "- {7} helps with research on internal topics.\n"
            "- {7} helps with checking work status.\n"
            "- {7} is only invoked from approved Slack channels.\n"
            "- Accepted Slack call signs: {8}\n"
            "- In these Slack-started sessions, always use `{7}` as your name.\n"
            "- Do not tell the user to use `Codex` as the default name in approved {7} channels.\n\n"
            "Rules:\n"
            "- You may use all available tools, skills, MCP servers, and agents normally.\n"
            "- When passing `sh -lc` through another shell layer, keep the inner script in single quotes or escape `$` as `\\$` so loop variables and shell parameters are not expanded by the outer shell.\n"
            "- {5}\n\n"
            "User request from Slack:\n"
            "{6}"
        ).format(
            workspace_name,
            channel_name,
            effective_mode,
            owner,
            owner_preferred_name,
            memory_rule,
            user_text,
            effective_assistant_name,
            accepted_callsigns,
        )

    def _build_ultimate_prompt(
        self,
        workspace_name: str,
        channel_name: str,
        user_text: str,
        invocation_message_ts: str,
        thread_messages: List[SlackThreadMessage],
        assistant_name: str,
    ) -> str:
        transcript_lines = []
        for message in thread_messages:
            transcript_lines.append(
                "[{0}] {1}: {2}".format(
                    message.message_ts,
                    message.author_actor_id or "unknown",
                    normalize_slack_markdown(message.text),
                )
            )
        transcript_text = "\n".join(transcript_lines) if transcript_lines else "(empty thread)"
        return (
            "{0}\n\n"
            "Ultimate mode:\n"
            "- invocation_mode: one-shot inline message append\n"
            "- invocation_message_ts: {1}\n\n"
            "Slack thread transcript:\n"
            "{2}"
        ).format(
            self._build_codex_prompt(
                workspace_name,
                channel_name,
                user_text,
                assistant_name=assistant_name,
            ),
            invocation_message_ts,
            transcript_text,
        )

    def _is_actor_allowed(self, workspace_name: str, channel_name: str, actor_id: str) -> bool:
        workspace = self._find_workspace(workspace_name)
        if workspace is None:
            return False
        channel = self._find_channel(workspace, channel_name)
        if channel is not None and channel.allowed_actor_ids is not None:
            allowed = channel.allowed_actor_ids
        elif workspace.channel_defaults.allowed_actor_ids is not None:
            allowed = workspace.channel_defaults.allowed_actor_ids
        else:
            allowed = self.config.defaults.allowed_actor_ids
        if not allowed:
            return True
        return actor_id in allowed

    def _find_workspace(self, workspace_name: str):
        for workspace in self.config.workspaces:
            if workspace.name == workspace_name:
                return workspace
        return None

    def _find_channel(self, workspace, channel_name: str) -> Optional[ChannelConfig]:
        if workspace is None:
            return None
        for channel in workspace.channels:
            if channel.name == channel_name:
                return channel
        if self.config.watcher.bob_ultimate_mode:
            runtime_channel = build_runtime_channel(self.config.defaults, workspace, channel_name)
            if runtime_channel is not None:
                return runtime_channel
        return None

    def _should_use_ultimate_mode_for_channel(self, channel_name: str) -> bool:
        return (
            self.config.watcher.bob_ultimate_mode
            and slack_channel_id_from_runtime_channel_name(channel_name) is not None
        )

    def _should_use_ultimate_mode_for_invocation(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> bool:
        if self._should_use_ultimate_mode_for_channel(channel_name):
            return True
        return not self.state_store.thread_has_processed_purpose(
            workspace_name,
            channel_name,
            thread_ts,
            self._PURPOSE_ROOT_REQUEST,
        )

    def _parse_approval_reply(self, text: str) -> Tuple[str, str]:
        parts = text.strip().split()
        if len(parts) < 2:
            return "", ""
        return parts[0].lower(), parts[1]

    def _strip_bob_prefix(self, text: str) -> str:
        return strip_assistant_prefix(text, self._assistant_names())

    def _is_bob_root_message(self, text: str) -> bool:
        return self._match_assistant_invocation(text) is not None

    def _is_manual_close_request(self, text: str) -> bool:
        return is_manual_close_request(text, self._assistant_names())

    def _match_assistant_invocation(self, text: str):
        return match_assistant_invocation(text, self._assistant_names())

    def _assistant_name_from_text(self, text: str, fallback: str) -> str:
        return assistant_label_from_text(text, self._assistant_names(), fallback)

    def _assistant_names(self) -> List[str]:
        return list(self.config.defaults.assistant_names)

    def _default_assistant_name(self) -> str:
        return self.config.defaults.assistant_names[0]

    def _extract_approval_request_id(self, wait_message: Optional[str]) -> Optional[str]:
        if not wait_message:
            return None
        for token in wait_message.split():
            if token.startswith("APR-"):
                return token
        return None

    def _should_auto_approve(self, approval_summary: str, approval_request_id: str) -> bool:
        if not approval_request_id:
            return False
        normalized = approval_summary.lower().replace(approval_request_id.lower(), "").strip()
        if any(token in normalized for token in ("&&", "||", ";", "|", ">", "<", "$(", "`")):
            return False
        risky_prefixes = (
            "rm ",
            "mv ",
            "cp ",
            "mkdir ",
            "rmdir ",
            "chmod ",
            "chown ",
            "touch ",
            "curl ",
            "wget ",
            "git commit",
            "git push",
            "git pull",
            "python ",
            "python3 ",
            "pip ",
            "pip3 ",
            "brew ",
            "npm ",
            "yarn ",
            "pnpm ",
            "docker ",
            "kubectl ",
        )
        if normalized.startswith(risky_prefixes):
            return False
        safe_prefixes = (
            "pwd",
            "ls",
            "cat ",
            "sed ",
            "rg ",
            "find ",
            "git status",
            "git diff",
            "head ",
            "tail ",
            "wc ",
        )
        return normalized.startswith(safe_prefixes)

    def _approval_needed_text(self, record: SessionRecord) -> str:
        approval_id = record.approval_request_id or "APR-unknown"
        summary = record.approval_command_summary or "pending command"
        return (
            "{0} {1} (reply with `approve {2}`, `deny {2}`, or `cancel {2}`)".format(
                self._label_approval(record.assistant_name),
                summary,
                approval_id,
            )
        )

    def _working_text(self, session_id: str, thread_ts: str, assistant_name: str) -> str:
        return "{0} session=`{1}` thread=`{2}`".format(
            self._label_working(assistant_name),
            session_id,
            thread_ts,
        )

    def _is_exec_timeout_failure(self, failure_text: str) -> bool:
        return failure_text.startswith("codex exec timed out")

    def _is_missing_rollout_failure(self, failure_text: str) -> bool:
        return "no rollout found" in failure_text.lower()

    def _queued_text(self, assistant_name: str) -> str:
        return "{0} I will run this after the active task in this thread.".format(
            self._label_queued(assistant_name)
        )

    def _failure_text(self, assistant_name: str) -> str:
        return "{0} Reply again in this thread to retry.".format(
            self._label_error(assistant_name)
        )

    def _label_working(self, assistant_name: str) -> str:
        return "_*{0} is working on it :arrows_counterclockwise::*_".format(assistant_name)

    def _label_queued(self, assistant_name: str) -> str:
        return "_*{0} queued it :hourglass_flowing_sand::*_".format(assistant_name)

    def _label_input(self, assistant_name: str) -> str:
        return "_*{0} needs input :exclamation::*_".format(assistant_name)

    def _label_approval(self, assistant_name: str) -> str:
        return "_*{0} needs approval :exclamation::*_".format(assistant_name)

    def _label_timed_out(self, assistant_name: str) -> str:
        return "_*{0} timed out :hourglass_flowing_sand::*_".format(assistant_name)

    def _label_done(self, assistant_name: str) -> str:
        return "_*{0} :white_check_mark::*_".format(assistant_name)

    def _label_error(self, assistant_name: str) -> str:
        return "_*{0} hit an error :exclamation::*_".format(assistant_name)

    def _try_ack_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
    ) -> None:
        try:
            self.browser.add_reaction(
                workspace_name=workspace_name,
                channel_name=channel_name,
                message_ts=message_ts,
                emoji_name="ok_hand",
            )
        except Exception:
            return

    def _try_deliver_working_message(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        intent_key: str,
        session_id: str,
        assistant_name: str,
    ) -> None:
        try:
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key=intent_key,
                text=self._working_text(
                    session_id=session_id,
                    thread_ts=thread_ts,
                    assistant_name=assistant_name,
                ),
            )
        except Exception:
            return

    def _try_append_working_message(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        original_text: str,
        intent_key: str,
        session_id: str,
        assistant_name: str,
        redeliver_existing: bool = False,
    ) -> None:
        self._append_message_line_or_fallback(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            message_ts=message_ts,
            original_text=original_text,
            intent_key=intent_key,
            line=self._working_text(
                session_id=session_id,
                thread_ts=thread_ts,
                assistant_name=assistant_name,
            ),
            redeliver_existing=redeliver_existing,
        )

    def _try_deliver_queued_message(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        assistant_name: str,
    ) -> None:
        try:
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key="queued-{0}".format(message_ts),
                text=self._queued_text(assistant_name),
            )
        except Exception:
            return

    def _try_append_queued_message(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        original_text: str,
        assistant_name: str,
    ) -> None:
        self._append_message_line_or_fallback(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            message_ts=message_ts,
            original_text=original_text,
            intent_key="queued-{0}".format(message_ts),
            line=self._queued_text(assistant_name),
        )

    def _reminder_text(self, record: SessionRecord) -> str:
        if record.status is SessionStatus.WAITING_FOR_APPROVAL:
            return "{0} Reminder: approval is still pending in this thread.".format(
                self._label_approval(record.assistant_name)
            )
        return "{0} Reminder: I am still waiting for your reply in this thread.".format(
            self._label_input(record.assistant_name)
        )

    def _next_reminder_due_at(self, reminder_count: int, now_epoch: int) -> Optional[int]:
        next_index = reminder_count + 1
        if next_index >= len(self.config.lifecycle.reminder_minutes):
            return None
        return now_epoch + int(self.config.lifecycle.reminder_minutes[next_index]) * 60

    def _clear_waiting_message(self, record: SessionRecord) -> None:
        if not record.waiting_message_ts:
            return
        try:
            self.browser.delete_message(
                workspace_name=record.workspace_name,
                channel_name=record.channel_name,
                message_ts=record.waiting_message_ts,
            )
        except Exception:
            return

    def _waiting_deadlines(self) -> Tuple[Optional[int], Optional[int]]:
        now = int(time.time())
        reminder_due_at = None
        if self.config.lifecycle.reminder_minutes:
            reminder_due_at = now + int(self.config.lifecycle.reminder_minutes[0]) * 60

        auto_close_due_at = None
        if self.config.lifecycle.auto_close_minutes is not None:
            auto_close_due_at = now + int(self.config.lifecycle.auto_close_minutes) * 60

        return reminder_due_at, auto_close_due_at
