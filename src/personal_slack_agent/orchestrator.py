import time
from typing import List, Optional, Protocol, Tuple

from .codex_runner import CodexRunResult
from .generated_files import GeneratedFile, extract_generated_files, normalize_slack_markdown
from .models import AppConfig, ChannelConfig, OutboundIntentRecord, SessionRecord, SessionStatus
from .slack import SlackBrowserAdapter
from .state import BobStateStore


class CodexRunner(Protocol):
    def run_new_session(self, prompt: str, cwd: str, additional_roots: List[str]) -> CodexRunResult:
        ...

    def resume_session(self, session_id: str, prompt: str, cwd: str) -> CodexRunResult:
        ...


class BobOrchestrator:
    _PURPOSE_ROOT_REQUEST = "root_request"
    _PURPOSE_THREAD_REPLY = "thread_reply"
    _LABEL_WORKING = "_*Bob is working on it :arrows_counterclockwise::*_"
    _LABEL_INPUT = "_*Bob needs input :exclamation::*_"
    _LABEL_APPROVAL = "_*Bob needs approval :exclamation::*_"
    _LABEL_DONE = "_*codex Bob :white_check_mark::*_"
    _LABEL_ERROR = "_*Bob hit an error :exclamation::*_"

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
        if not self._is_bob_root_message(text):
            return
        if not self._is_actor_allowed(workspace_name, author_actor_id):
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
                    "Bob already has a session in this thread: {0}".format(
                        existing.codex_session_id
                    )
                ),
            )
            return

        try:
            cwd = self._resolve_default_cwd(workspace_name, channel_name)
            prompt = self._build_codex_prompt(workspace_name, channel_name, text)
            run_result = self._runner_for_channel(workspace_name, channel_name).run_new_session(
                prompt=prompt,
                cwd=cwd,
                additional_roots=list(self.config.defaults.additional_roots),
            )
            session_id = run_result.session_id or "unknown-session"
            self.state_store.upsert_session(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=message_ts,
                root_ts=message_ts,
                codex_session_id=session_id,
                cwd=cwd,
                owner_actor_id=author_actor_id,
                status=SessionStatus.RUNNING,
            )
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=message_ts,
                intent_key="start-status",
                text="{0} {1}".format(self._LABEL_WORKING, session_id),
            )
            self._process_run_result(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=message_ts,
                session_id=session_id,
                run_result=run_result,
                result_key_suffix=message_ts,
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

    def process_scheduled_actions(self, now_epoch: Optional[int] = None) -> None:
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
                        self._LABEL_DONE
                    )
                ),
            )
            self.state_store.update_status(
                workspace_name=record.workspace_name,
                channel_name=record.channel_name,
                thread_ts=record.thread_ts,
                status=SessionStatus.CLOSED_TIMEOUT,
            )

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
        if not self._is_actor_allowed(workspace_name, author_actor_id):
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

        if self._is_manual_close_request(text):
            self._clear_waiting_message(record)
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.CLOSED_MANUAL,
            )
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key="manual-close-{0}".format(message_ts),
                text="{0} Session closed. Reply again in this thread to resume.".format(
                    self._LABEL_DONE
                ),
            )
            return

        if record.status is SessionStatus.WAITING_FOR_APPROVAL:
            self._handle_approval_reply(
                record=record,
                workspace_name=workspace_name,
                channel_name=channel_name,
                message_ts=message_ts,
                text=text,
                author_actor_id=author_actor_id,
            )
            return

        if record.status is SessionStatus.WAITING_FOR_INPUT:
            if not self._is_actor_allowed(workspace_name, author_actor_id):
                return
            self._clear_waiting_message(record)
            self._resume_record(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message_ts,
                session_id=record.codex_session_id,
                prompt=text,
            )
            return

        if record.status in (
            SessionStatus.CLOSED_IDLE,
            SessionStatus.CLOSED_TIMEOUT,
            SessionStatus.CLOSED_MANUAL,
            SessionStatus.FAILED,
        ):
            if not self._is_actor_allowed(workspace_name, author_actor_id):
                return
            self._resume_record(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message_ts,
                session_id=record.codex_session_id,
                prompt=text,
            )
            return

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
                text="_*Bob {0} command request :exclamation:*_ {1}.".format(action_text, approval_id),
            )
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=record.thread_ts,
                status=SessionStatus.CLOSED_MANUAL,
            )
            return

        try:
            run_result = self.codex_runner.resume_session(record.codex_session_id, text, record.cwd)
        except Exception:
            self.state_store.release_processed_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=record.thread_ts,
                message_ts=message_ts,
                purpose=self._PURPOSE_THREAD_REPLY,
            )
            raise
        self._clear_waiting_message(record)
        self.state_store.update_status(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=record.thread_ts,
            status=SessionStatus.RUNNING,
        )
        try:
            self._process_run_result(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=record.thread_ts,
                session_id=record.codex_session_id,
                run_result=run_result,
                result_key_suffix=message_ts,
            )
        except Exception:
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=record.thread_ts,
                status=SessionStatus.FAILED,
            )
            raise

    def _process_run_result(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        session_id: str,
        run_result: CodexRunResult,
        result_key_suffix: str,
    ) -> None:
        if run_result.wait_kind == "input":
            wait_message = run_result.wait_message or "Please reply in this thread."
            reminder_due_at, auto_close_due_at = self._waiting_deadlines()
            waiting_message_ts = self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key="wait-input-{0}".format(result_key_suffix),
                text="{0} {1}".format(self._LABEL_INPUT, wait_message),
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
                auto_result = self.codex_runner.resume_session(
                    session_id,
                    "approve {0}".format(approval_request_id),
                    self._cwd_for_thread(workspace_name, channel_name, thread_ts),
                )
                self._process_run_result(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    session_id=session_id,
                    run_result=auto_result,
                    result_key_suffix="auto-{0}".format(result_key_suffix),
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
                ).format(self._LABEL_APPROVAL, approval_summary, approval_request_id),
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
            )
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.CLOSED_IDLE,
            )
            return

        if run_result.failure_text:
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key="failure-{0}".format(session_id),
                text="{0} {1}".format(self._LABEL_ERROR, run_result.failure_text),
            )
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.FAILED,
            )

    def _deliver_final_output(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        session_id: str,
        result_key_suffix: str,
        final_output: str,
    ) -> None:
        summary, files = extract_generated_files(final_output)
        if not files:
            self._deliver_thread_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                intent_key="final-{0}-{1}".format(session_id, result_key_suffix),
                text="{0} {1}".format(self._LABEL_DONE, final_output),
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
                        text="{0} {1}".format(self._LABEL_DONE, final_output),
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
                self._LABEL_DONE,
                summary_text,
                file_list,
            ),
        )

    def _resume_record(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        message_ts: str,
        session_id: str,
        prompt: str,
    ) -> None:
        record = self.state_store.get_by_thread(workspace_name, channel_name, thread_ts)
        if record is None:
            return
        try:
            wrapped_prompt = self._build_codex_prompt(workspace_name, channel_name, prompt)
            run_result = self._runner_for_channel(workspace_name, channel_name).resume_session(
                session_id, wrapped_prompt, record.cwd
            )
        except Exception:
            self.state_store.release_processed_message(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                message_ts=message_ts,
                purpose=self._PURPOSE_THREAD_REPLY,
            )
            raise
        self.state_store.update_status(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            status=SessionStatus.RUNNING,
        )
        try:
            self._process_run_result(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                session_id=session_id,
                run_result=run_result,
                result_key_suffix=message_ts,
            )
        except Exception:
            self.state_store.update_status(
                workspace_name=workspace_name,
                channel_name=channel_name,
                thread_ts=thread_ts,
                status=SessionStatus.FAILED,
            )
            raise

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

    def _resolve_default_cwd(self, workspace_name: str, channel_name: str) -> str:
        workspace = self._find_workspace(workspace_name)
        channel = self._find_channel(workspace, channel_name) if workspace else None
        if channel is not None and channel.effective_default_cwd:
            return channel.effective_default_cwd
        return self.config.defaults.default_cwd

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
        return self.codex_runner

    def _build_codex_prompt(self, workspace_name: str, channel_name: str, user_text: str) -> str:
        workspace = self._find_workspace(workspace_name)
        channel = self._find_channel(workspace, channel_name)
        if channel is None:
            return user_text

        owner = channel.persistent_memory_owner or "none"
        if channel.persistent_memory_mode == "owner_only":
            memory_rule = (
                "This Slack channel is allowed to update durable personal preference notes "
                "for owner `{0}` when the conversation reveals a durable preference or workflow rule."
            ).format(owner)
        else:
            memory_rule = (
                "This Slack channel does not grant permission to update Yifan Chen / Ethan's "
                "personal durable preference files. Do not update personal session notes or "
                "similar durable preference files for Yifan from this conversation. Do not "
                "modify repo-local or global skill files such as `.codex/skills/**`, `SKILL.md`, "
                "or similar skill definitions unless the user explicitly asks you to create or edit them."
            )

        return (
            "Bob execution context:\n"
            "- workspace: {0}\n"
            "- channel: {1}\n"
            "- persistent_memory_mode: {2}\n"
            "- persistent_memory_owner: {3}\n\n"
            "Bob role:\n"
            "- Bob is Ethan's personal assistant.\n"
            "- Bob specializes in working on CTDM tickets.\n"
            "- Bob helps with research on internal topics.\n"
            "- Bob helps with checking work status.\n"
            "- Bob is only invoked from approved Slack channels.\n"
            "- In these Slack-started sessions, always use `Bob` as your name.\n"
            "- Do not tell the user to use `Codex` as the default name in approved Bob channels.\n\n"
            "Rules:\n"
            "- You may use all available tools, skills, MCP servers, and agents normally.\n"
            "- {4}\n\n"
            "User request from Slack:\n"
            "{5}"
        ).format(
            workspace_name,
            channel_name,
            channel.persistent_memory_mode,
            owner,
            memory_rule,
            user_text,
        )

    def _is_actor_allowed(self, workspace_name: str, actor_id: str) -> bool:
        workspace = self._find_workspace(workspace_name)
        if workspace is None:
            return False
        allowed = workspace.allowed_actor_ids
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
        return None

    def _parse_approval_reply(self, text: str) -> Tuple[str, str]:
        parts = text.strip().split()
        if len(parts) < 2:
            return "", ""
        return parts[0].lower(), parts[1]

    def _is_bob_root_message(self, text: str) -> bool:
        normalized = text.strip().lower()
        return normalized.startswith("bob")

    def _is_manual_close_request(self, text: str) -> bool:
        normalized = text.strip().lower()
        return normalized in {"bob close", "close bob"}

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
                self._LABEL_APPROVAL,
                summary,
                approval_id,
            )
        )

    def _reminder_text(self, record: SessionRecord) -> str:
        if record.status is SessionStatus.WAITING_FOR_APPROVAL:
            return "{0} Reminder: approval is still pending in this thread.".format(
                self._LABEL_APPROVAL
            )
        return "{0} Reminder: I am still waiting for your reply in this thread.".format(
            self._LABEL_INPUT
        )

    def _next_reminder_due_at(self, reminder_count: int, now_epoch: int) -> Optional[int]:
        next_index = reminder_count + 1
        if next_index >= len(self.config.defaults.reminder_minutes):
            return None
        return now_epoch + int(self.config.defaults.reminder_minutes[next_index]) * 60

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
        if self.config.defaults.reminder_minutes:
            reminder_due_at = now + int(self.config.defaults.reminder_minutes[0]) * 60

        auto_close_due_at = None
        if self.config.defaults.auto_close_minutes is not None:
            auto_close_due_at = now + int(self.config.defaults.auto_close_minutes) * 60

        return reminder_due_at, auto_close_due_at
