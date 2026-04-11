from dataclasses import dataclass
import time
from typing import Dict, List

import pytest

from personal_slack_agent.codex_runner import CodexRunResult
from personal_slack_agent.models import (
    AppConfig,
    ChannelConfig,
    DefaultSettings,
    SessionStatus,
    WorkspaceConfig,
)
from personal_slack_agent.orchestrator import BobOrchestrator
from personal_slack_agent.state import BobStateStore


class FakeSlackBrowser:
    def __init__(self) -> None:
        self.thread_posts: Dict[str, List[str]] = {}
        self.deleted_messages: List[str] = []
        self.uploaded_snippets: List[dict] = []
        self.post_error: Exception = None
        self.upload_error: Exception = None

    def post_thread_reply(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        text: str,
    ) -> str:
        if self.post_error is not None:
            raise self.post_error
        del workspace_name
        del channel_name
        posts = self.thread_posts.setdefault(thread_ts, [])
        posts.append(text)
        return "{0}.{1:06d}".format(thread_ts.split(".")[0], len(posts))

    def delete_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
    ) -> None:
        del workspace_name
        del channel_name
        self.deleted_messages.append(message_ts)

    def find_existing_bob_messages(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> List[str]:
        del workspace_name
        del channel_name
        return list(self.thread_posts.get(thread_ts, []))

    def upload_text_snippet(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        filename: str,
        content: str,
    ) -> str:
        if self.upload_error is not None:
            raise self.upload_error
        self.uploaded_snippets.append(
            {
                "workspace_name": workspace_name,
                "channel_name": channel_name,
                "thread_ts": thread_ts,
                "filename": filename,
                "content": content,
            }
        )
        return "F{0}".format(len(self.uploaded_snippets))


@dataclass
class FakeCodexRunner:
    next_result: CodexRunResult = CodexRunResult(
        session_id="session-123",
        final_output="Final answer",
    )

    def __post_init__(self) -> None:
        self.new_session_calls: List[dict] = []
        self.resume_calls: List[dict] = []
        self.new_session_error: Exception = None
        self.resume_error: Exception = None
        self.next_resume_result: CodexRunResult | None = None

    def run_new_session(self, prompt: str, cwd: str, additional_roots: List[str]) -> CodexRunResult:
        if self.new_session_error is not None:
            raise self.new_session_error
        self.new_session_calls.append(
            {"prompt": prompt, "cwd": cwd, "additional_roots": list(additional_roots)}
        )
        return self.next_result

    def resume_session(self, session_id: str, prompt: str, cwd: str) -> CodexRunResult:
        if self.resume_error is not None:
            raise self.resume_error
        self.resume_calls.append({"session_id": session_id, "prompt": prompt, "cwd": cwd})
        if self.next_resume_result is not None:
            return self.next_resume_result
        return self.next_result


@pytest.fixture
def fake_environment(tmp_path):
    db_path = tmp_path / "bob.sqlite3"
    store = BobStateStore(db_path)
    store.initialize()
    browser = FakeSlackBrowser()
    runner = FakeCodexRunner()
    config = AppConfig(
        defaults=DefaultSettings(
            default_cwd=str(tmp_path),
            additional_roots=[str(tmp_path / "roots")],
            allowed_actor_ids=["U123"],
        ),
        workspaces=[
            WorkspaceConfig(
                name="oracle",
                allowed_actor_ids=["U123"],
                channels=[
                    ChannelConfig(
                        name="yifanche-private",
                        persistent_memory_mode="owner_only",
                        persistent_memory_owner="yifanche",
                        effective_default_cwd=str(tmp_path),
                        effective_accept_root_bob_requests=True,
                    )
                ],
            )
        ],
    )
    orchestrator = BobOrchestrator(
        browser=browser,
        state_store=store,
        codex_runner=runner,
        config=config,
    )
    return orchestrator, browser, store, runner


def test_new_root_message_creates_session_and_posts_start_status(fake_environment):
    orchestrator, browser, store, runner = fake_environment

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    thread_posts = browser.thread_posts["1743461000.000001"]
    assert thread_posts[0].startswith("_*Bob is working on it :arrows_counterclockwise::*_ ")
    assert thread_posts[1] == "_*codex Bob :white_check_mark::*_ Final answer"
    assert len(runner.new_session_calls) == 1
    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_IDLE


def test_new_root_message_wraps_prompt_with_owner_only_memory_policy(fake_environment):
    orchestrator, _browser, _store, runner = fake_environment

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, remember that I prefer reviewer passes",
    )

    prompt = runner.new_session_calls[0]["prompt"]
    assert "channel: yifanche-private" in prompt
    assert "persistent_memory_mode: owner_only" in prompt
    assert "persistent_memory_owner: yifanche" in prompt
    assert "may use all available tools, skills, MCP servers, and agents" in prompt


def test_new_root_message_wraps_prompt_with_disabled_memory_policy_for_shared_channel(
    fake_environment,
):
    orchestrator, _browser, _store, runner = fake_environment
    orchestrator.config.workspaces[0].channels.append(
        ChannelConfig(
            name="yifanche-bob",
            persistent_memory_mode="disabled",
            effective_default_cwd=orchestrator.config.defaults.default_cwd,
            effective_accept_root_bob_requests=True,
        )
    )

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-bob",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, help my coworker debug this test",
    )

    prompt = runner.new_session_calls[0]["prompt"]
    assert "channel: yifanche-bob" in prompt
    assert "persistent_memory_mode: disabled" in prompt
    assert "do not update personal session notes" in prompt.lower()


def test_final_output_with_generated_files_posts_summary_and_uploads_snippets(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    runner.next_result = CodexRunResult(
        session_id="session-123",
        final_output=(
            "Use this set as a repo-local starter package.\n\n"
            "**`scripts/shepherd/README.md`**\n"
            "```md\n"
            "# Shepherd\n"
            "Hello\n"
            "```\n"
        ),
    )

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    assert browser.uploaded_snippets == [
        {
            "workspace_name": "oracle",
            "channel_name": "yifanche-private",
            "thread_ts": "1743461000.000001",
            "filename": "scripts/shepherd/README.md",
            "content": "# Shepherd\nHello",
        }
    ]
    final_post = browser.thread_posts["1743461000.000001"][-1]
    assert "Use this set as a repo-local starter package." in final_post
    assert "scripts/shepherd/README.md" in final_post
    assert "# Shepherd" not in final_post
    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_IDLE


def test_waiting_for_input_posts_wait_message_and_saves_wait_state(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    runner.next_result = CodexRunResult(
        session_id="session-123",
        wait_kind="input",
        wait_message="Which option do you want?",
    )

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, choose an option",
    )

    assert browser.thread_posts["1743461000.000001"][-1] == "_*Bob needs input :exclamation::*_ Which option do you want?"
    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.WAITING_FOR_INPUT


def test_waiting_for_input_schedules_reminder_and_auto_close(fake_environment):
    orchestrator, _browser, store, runner = fake_environment
    orchestrator.config.defaults.reminder_minutes = [30]
    orchestrator.config.defaults.auto_close_minutes = 120
    runner.next_result = CodexRunResult(
        session_id="session-123",
        wait_kind="input",
        wait_message="Which option do you want?",
    )
    before = int(time.time())

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, choose an option",
    )

    after = int(time.time())
    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.reminder_due_at is not None
    assert record.auto_close_due_at is not None
    assert before + 30 * 60 <= record.reminder_due_at <= after + 30 * 60
    assert before + 120 * 60 <= record.auto_close_due_at <= after + 120 * 60


def test_unauthorized_actor_is_ignored(fake_environment):
    orchestrator, browser, store, runner = fake_environment

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U999",
        text="Bob, hi there",
    )

    assert browser.thread_posts == {}
    assert len(runner.new_session_calls) == 0
    assert store.get_by_thread("oracle", "yifanche-private", "1743461000.000001") is None


def test_empty_allowed_actor_ids_allows_any_actor(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.defaults.allowed_actor_ids = []
    orchestrator.config.workspaces[0].allowed_actor_ids = []

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U999",
        text="Bob, hi there",
    )

    assert len(runner.new_session_calls) == 1
    assert browser.thread_posts["1743461000.000001"][-1] == "_*codex Bob :white_check_mark::*_ Final answer"
    assert store.get_by_thread("oracle", "yifanche-private", "1743461000.000001") is not None


def test_duplicate_root_message_is_not_processed_twice(fake_environment):
    orchestrator, browser, store, runner = fake_environment

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )
    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    assert len(runner.new_session_calls) == 1
    assert browser.thread_posts["1743461000.000001"].count("_*codex Bob :white_check_mark::*_ Final answer") == 1


def test_non_bob_root_message_is_ignored(fake_environment):
    orchestrator, browser, store, runner = fake_environment

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="hello there",
    )

    assert browser.thread_posts == {}
    assert runner.new_session_calls == []
    assert store.get_by_thread("oracle", "yifanche-private", "1743461000.000001") is None


def test_stale_approval_id_is_rejected(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_APPROVAL,
        approval_request_id="APR-001",
        approval_command_summary="git status -sb",
    )

    orchestrator.handle_thread_reply(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461010.000001",
        author_actor_id="U123",
        text="approve APR-999",
    )

    assert runner.resume_calls == []
    assert browser.thread_posts["1743461000.000001"][-1].startswith("_*Bob needs approval :exclamation::*_")


def test_new_root_message_failure_releases_processed_claim(fake_environment):
    orchestrator, _browser, store, runner = fake_environment
    runner.new_session_error = RuntimeError("codex unavailable")

    with pytest.raises(RuntimeError):
        orchestrator.handle_new_root_message(
            workspace_name="oracle",
            channel_name="yifanche-private",
            message_ts="1743461000.000001",
            author_actor_id="U123",
            text="Bob, hi there",
        )

    assert (
        store.has_processed_message(
            workspace_name="oracle",
            channel_name="yifanche-private",
            thread_ts="1743461000.000001",
            message_ts="1743461000.000001",
            purpose="root_request",
        )
        is False
    )
    assert store.get_by_thread("oracle", "yifanche-private", "1743461000.000001") is None


def test_root_message_claim_is_released_if_session_persistence_fails(fake_environment, monkeypatch):
    orchestrator, _browser, store, _runner = fake_environment

    def fail_upsert_session(**kwargs):
        del kwargs
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(store, "upsert_session", fail_upsert_session)

    with pytest.raises(RuntimeError):
        orchestrator.handle_new_root_message(
            workspace_name="oracle",
            channel_name="yifanche-private",
            message_ts="1743461000.000001",
            author_actor_id="U123",
            text="Bob, hi there",
        )

    assert (
        store.has_processed_message(
            workspace_name="oracle",
            channel_name="yifanche-private",
            thread_ts="1743461000.000001",
            message_ts="1743461000.000001",
            purpose="root_request",
        )
        is False
    )
    assert store.get_by_thread("oracle", "yifanche-private", "1743461000.000001") is None


def test_waiting_reply_resume_failure_keeps_waiting_state_and_releases_claim(fake_environment):
    orchestrator, _browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        waiting_message_ts="1743461001.000001",
    )
    runner.resume_error = RuntimeError("resume failed")

    with pytest.raises(RuntimeError):
        orchestrator.handle_thread_reply(
            workspace_name="oracle",
            channel_name="yifanche-private",
            thread_ts="1743461000.000001",
            message_ts="1743461010.000001",
            author_actor_id="U123",
            text="Option A",
        )

    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.WAITING_FOR_INPUT
    assert (
        store.has_processed_message(
            workspace_name="oracle",
            channel_name="yifanche-private",
            thread_ts="1743461000.000001",
            message_ts="1743461010.000001",
            purpose="thread_reply",
        )
        is False
    )


def test_bob_close_marks_session_closed_without_resuming(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        waiting_message_ts="1743461001.000001",
    )

    orchestrator.handle_thread_reply(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461010.000001",
        author_actor_id="U123",
        text="bob close",
    )

    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_MANUAL
    assert runner.resume_calls == []
    assert "closed" in browser.thread_posts["1743461000.000001"][-1].lower()


def test_post_failure_marks_session_failed_instead_of_leaving_it_running(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    browser.post_error = RuntimeError("slack unavailable")

    with pytest.raises(RuntimeError):
        orchestrator.handle_new_root_message(
            workspace_name="oracle",
            channel_name="yifanche-private",
            message_ts="1743461000.000001",
            author_actor_id="U123",
            text="Bob, hi there",
        )

    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.FAILED


def test_closed_idle_reply_resumes_same_session(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    runner.next_result = CodexRunResult(
        session_id="session-123",
        final_output="Follow-up answer",
    )

    orchestrator.handle_thread_reply(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461020.000001",
        author_actor_id="U123",
        text="What about a follow-up?",
    )

    assert len(runner.resume_calls) == 1
    assert runner.resume_calls[0]["cwd"] == "/tmp/project"
    assert browser.thread_posts["1743461000.000001"][-1] == "_*codex Bob :white_check_mark::*_ Follow-up answer"


def test_closed_idle_reply_resume_reasserts_disabled_memory_policy(fake_environment):
    orchestrator, _browser, store, runner = fake_environment
    orchestrator.config.workspaces[0].channels.append(
        ChannelConfig(
            name="yifanche-bob-test",
            persistent_memory_mode="disabled",
            effective_default_cwd=orchestrator.config.defaults.default_cwd,
            effective_accept_root_bob_requests=True,
        )
    )
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-bob-test",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )

    orchestrator.handle_thread_reply(
        workspace_name="oracle",
        channel_name="yifanche-bob-test",
        thread_ts="1743461000.000001",
        message_ts="1743461010.000001",
        author_actor_id="U123",
        text="Keep investigating",
    )

    prompt = runner.resume_calls[0]["prompt"]
    assert "channel: yifanche-bob-test" in prompt
    assert "persistent_memory_mode: disabled" in prompt
    assert "do not update personal session notes" in prompt.lower()


def test_closed_idle_reply_from_non_owner_resumes_when_workspace_is_unrestricted(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.defaults.allowed_actor_ids = []
    orchestrator.config.workspaces[0].allowed_actor_ids = []
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    runner.next_result = CodexRunResult(
        session_id="session-123",
        final_output="Follow-up answer",
    )

    orchestrator.handle_thread_reply(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461020.000001",
        author_actor_id="U999",
        text="What about a follow-up?",
    )

    assert len(runner.resume_calls) == 1
    assert browser.thread_posts["1743461000.000001"][-1] == "_*codex Bob :white_check_mark::*_ Follow-up answer"


def test_waiting_reply_deletes_previous_wait_prompt_before_resuming(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        waiting_message_ts="1743461001.000001",
    )
    runner.next_result = CodexRunResult(
        session_id="session-123",
        final_output="Thanks for the answer",
    )

    orchestrator.handle_thread_reply(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461020.000001",
        author_actor_id="U123",
        text="Option A",
    )

    assert browser.deleted_messages == ["1743461001.000001"]
    assert runner.resume_calls[0]["prompt"].endswith("User request from Slack:\nOption A")


def test_waiting_reply_from_non_owner_resumes_when_workspace_is_unrestricted(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.defaults.allowed_actor_ids = []
    orchestrator.config.workspaces[0].allowed_actor_ids = []
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        waiting_message_ts="1743461001.000001",
    )
    runner.next_result = CodexRunResult(
        session_id="session-123",
        final_output="Thanks for the answer",
    )

    orchestrator.handle_thread_reply(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461020.000001",
        author_actor_id="U999",
        text="Option A",
    )

    assert browser.deleted_messages == ["1743461001.000001"]
    assert runner.resume_calls[0]["prompt"].endswith("User request from Slack:\nOption A")


def test_process_due_reminders_posts_reminder_and_schedules_next_one(fake_environment):
    orchestrator, browser, store, _runner = fake_environment
    orchestrator.config.defaults.reminder_minutes = [30, 60]
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        waiting_message_ts="1743461001.000001",
        reminder_due_at=1,
        auto_close_due_at=999999,
    )

    orchestrator.process_scheduled_actions(now_epoch=5)

    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.WAITING_FOR_INPUT
    assert record.reminder_count == 1
    assert record.reminder_due_at == 5 + 60 * 60
    assert "reminder" in browser.thread_posts["1743461000.000001"][-1].lower()


def test_process_due_auto_closes_closes_waiting_session_and_deletes_prompt(fake_environment):
    orchestrator, browser, store, _runner = fake_environment
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        waiting_message_ts="1743461001.000001",
        auto_close_due_at=1,
    )

    orchestrator.process_scheduled_actions(now_epoch=5)

    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_TIMEOUT
    assert browser.deleted_messages == ["1743461001.000001"]
    assert "timed out" in browser.thread_posts["1743461000.000001"][-1].lower()


def test_failed_reply_resumes_same_session(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.FAILED,
    )
    runner.next_result = CodexRunResult(
        session_id="session-123",
        final_output="Recovered answer",
    )

    orchestrator.handle_thread_reply(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461021.000001",
        author_actor_id="U123",
        text="Try again",
    )

    assert len(runner.resume_calls) == 1
    assert runner.resume_calls[0]["cwd"] == "/tmp/project"
    assert browser.thread_posts["1743461000.000001"][-1] == "_*codex Bob :white_check_mark::*_ Recovered answer"


def test_second_waiting_input_prompt_is_posted(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    runner.next_result = CodexRunResult(
        session_id="session-123",
        wait_kind="input",
        wait_message="First question?",
    )
    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob start",
    )
    runner.next_result = CodexRunResult(
        session_id="session-123",
        wait_kind="input",
        wait_message="Second question?",
    )
    orchestrator.handle_thread_reply(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461030.000001",
        author_actor_id="U123",
        text="Answer one",
    )

    posts = browser.thread_posts["1743461000.000001"]
    assert "_*Bob needs input :exclamation::*_ First question?" in posts
    assert "_*Bob needs input :exclamation::*_ Second question?" in posts
    assert posts.count("_*Bob needs input :exclamation::*_ Second question?") == 1
    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.waiting_message_ts is not None


def test_generated_approval_id_is_included_in_prompt(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    runner.next_result = CodexRunResult(
        session_id="session-123",
        wait_kind="approval",
        wait_message="Run git status -sb?",
    )

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob run command",
    )

    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.approval_request_id is not None
    last_post = browser.thread_posts["1743461000.000001"][-1]
    assert "_*Bob needs approval :exclamation::*_" in last_post
    assert record.approval_request_id in last_post
    assert "approve {0}".format(record.approval_request_id) in last_post


def test_low_risk_approval_is_auto_approved_without_slack_prompt(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    runner.next_result = CodexRunResult(
        session_id="session-123",
        wait_kind="approval",
        wait_message="git status -sb APR-001",
    )
    runner.next_resume_result = CodexRunResult(
        session_id="session-123",
        final_output="Auto-approved answer",
    )

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob run safe command",
    )

    assert runner.resume_calls == [
        {
            "session_id": "session-123",
            "prompt": "approve APR-001",
            "cwd": str(store.get_by_thread("oracle", "yifanche-private", "1743461000.000001").cwd),
        }
    ]
    posts = browser.thread_posts["1743461000.000001"]
    assert all("_*Bob needs approval :exclamation::*_" not in post for post in posts)
    assert posts[-1] == "_*codex Bob :white_check_mark::*_ Auto-approved answer"


def test_high_risk_approval_still_requires_slack_prompt(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    runner.next_result = CodexRunResult(
        session_id="session-123",
        wait_kind="approval",
        wait_message="rm -rf /tmp/demo APR-001",
    )

    orchestrator.handle_new_root_message(
        workspace_name="oracle",
        channel_name="yifanche-private",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob run risky command",
    )

    assert runner.resume_calls == []
    record = store.get_by_thread("oracle", "yifanche-private", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.WAITING_FOR_APPROVAL
    assert browser.thread_posts["1743461000.000001"][-1].startswith(
        "_*Bob needs approval :exclamation::*_"
    )


def test_approval_accept_resumes_same_session_with_cwd(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_APPROVAL,
        approval_request_id="APR-001",
        approval_command_summary="git status -sb",
    )
    runner.next_result = CodexRunResult(
        session_id="session-123",
        final_output="Approved answer",
    )

    orchestrator.handle_thread_reply(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461050.000001",
        author_actor_id="U123",
        text="approve APR-001",
    )

    assert runner.resume_calls == [
        {
            "session_id": "session-123",
            "prompt": "approve APR-001",
            "cwd": "/tmp/project",
        }
    ]
    assert browser.thread_posts["1743461000.000001"][-1] == "_*codex Bob :white_check_mark::*_ Approved answer"


def test_deny_and_cancel_have_distinct_audit_messages(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_APPROVAL,
        approval_request_id="APR-001",
        approval_command_summary="git status -sb",
    )

    orchestrator.handle_thread_reply(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461040.000001",
        author_actor_id="U123",
        text="deny APR-001",
    )

    assert "denied" in browser.thread_posts["1743461000.000001"][-1].lower()
    assert runner.resume_calls == []
