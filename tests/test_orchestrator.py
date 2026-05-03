from concurrent.futures import Future
from dataclasses import dataclass, field
import time
import threading
from typing import Callable, Dict, List, Optional

import pytest

from personal_slack_agent.codex_runner import CodexRunResult
from personal_slack_agent.models import (
    AppConfig,
    ChannelConfig,
    DefaultSettings,
    OrchestratorSettings,
    SessionStatus,
    TaskStatus,
    WorkspaceConfig,
)
from personal_slack_agent.orchestrator import BobOrchestrator
from personal_slack_agent.slack import SlackThreadMessage
from personal_slack_agent.state import BobStateStore


class FakeSlackBrowser:
    def __init__(self) -> None:
        self.thread_posts: Dict[str, List[str]] = {}
        self.updated_messages: Dict[str, List[str]] = {}
        self.thread_messages: Dict[tuple[str, str, str], list] = {}
        self.deleted_messages: List[str] = []
        self.uploaded_snippets: List[dict] = []
        self.reactions: List[dict] = []
        self.post_error: Exception = None
        self.update_error: Exception = None
        self.upload_error: Exception = None
        self.reaction_error: Exception = None

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

    def update_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
        text: str,
    ) -> None:
        if self.update_error is not None:
            raise self.update_error
        del workspace_name
        del channel_name
        updates = self.updated_messages.setdefault(message_ts, [])
        updates.append(text)

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

    def add_reaction(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
        emoji_name: str,
    ) -> None:
        if self.reaction_error is not None:
            raise self.reaction_error
        self.reactions.append(
            {
                "workspace_name": workspace_name,
                "channel_name": channel_name,
                "message_ts": message_ts,
                "emoji_name": emoji_name,
            }
        )

    def list_thread_messages(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ):
        return list(self.thread_messages.get((workspace_name, channel_name, thread_ts), []))


@dataclass
class FakeCodexRunner:
    next_result: CodexRunResult = field(
        default_factory=lambda: CodexRunResult(
            session_id="session-123",
            final_output="Final answer",
        )
    )

    def __post_init__(self) -> None:
        self.new_session_calls: List[dict] = []
        self.resume_calls: List[dict] = []
        self.new_session_error: Exception = None
        self.resume_error: Exception = None
        self.next_resume_result: CodexRunResult | None = None
        self.on_resume: Optional[Callable[[dict], None]] = None

    def run_new_session(
        self,
        prompt: str,
        cwd: str,
        additional_roots: List[str],
        sandbox_mode: Optional[str] = None,
        workspace_write_writable_roots: Optional[List[str]] = None,
        on_session_started: Optional[Callable[[str], None]] = None,
    ) -> CodexRunResult:
        if self.new_session_error is not None:
            raise self.new_session_error
        self.new_session_calls.append(
            {
                "prompt": prompt,
                "cwd": cwd,
                "additional_roots": list(additional_roots),
                "sandbox_mode": sandbox_mode,
                "workspace_write_writable_roots": (
                    list(workspace_write_writable_roots)
                    if workspace_write_writable_roots is not None
                    else None
                ),
            }
        )
        if on_session_started is not None and self.next_result.session_id is not None:
            on_session_started(self.next_result.session_id)
        return self.next_result

    def resume_session(
        self,
        session_id: str,
        prompt: str,
        cwd: str,
        sandbox_mode: Optional[str] = None,
        workspace_write_writable_roots: Optional[List[str]] = None,
    ) -> CodexRunResult:
        if self.resume_error is not None:
            raise self.resume_error
        self.resume_calls.append(
            {
                "session_id": session_id,
                "prompt": prompt,
                "cwd": cwd,
                "sandbox_mode": sandbox_mode,
                "workspace_write_writable_roots": (
                    list(workspace_write_writable_roots)
                    if workspace_write_writable_roots is not None
                    else None
                ),
            }
        )
        if self.on_resume is not None:
            self.on_resume(self.resume_calls[-1])
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
            owner_name="Bob Owner",
            owner_preferred_name="Owner",
        ),
        workspaces=[
            WorkspaceConfig(
                name="bob_company",
                channels=[
                    ChannelConfig(
                        name="bob_private_channel",
                        persistent_memory_mode="owner_only",
                        persistent_memory_owner="bob_owner_handle",
                        effective_default_cwd=str(tmp_path),
                        effective_additional_roots=[str(tmp_path / "roots")],
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    thread_posts = browser.thread_posts["1743461000.000001"]
    assert browser.reactions == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "message_ts": "1743461000.000001",
            "emoji_name": "ok_hand",
        }
    ]
    assert (
        thread_posts[0]
        == "_*Bob is working on it :arrows_counterclockwise::*_ session=`session-123` thread=`1743461000.000001`"
    )
    assert thread_posts[1] == "_*Bob :white_check_mark::*_ Final answer"
    assert len(runner.new_session_calls) == 1
    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_IDLE
    assert runner.new_session_calls[0]["sandbox_mode"] is None
    assert runner.new_session_calls[0]["workspace_write_writable_roots"] is None


def test_new_root_message_uses_configured_alias_casing_in_replies(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.defaults.assistant_names = ["Bob", "Bobby"]

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="bObBy, hi there",
    )

    thread_posts = browser.thread_posts["1743461000.000001"]
    assert thread_posts[0].startswith("_*Bobby is working on it")
    assert thread_posts[1] == "_*Bobby :white_check_mark::*_ Final answer"
    assert len(runner.new_session_calls) == 1
    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.assistant_name == "Bobby"


def test_new_root_message_does_not_trigger_on_partial_alias_boundary(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.defaults.assistant_names = ["Bob"]

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="bobcat run tests",
    )

    assert browser.thread_posts == {}
    assert store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001") is None
    assert runner.new_session_calls == []


def test_new_root_message_wraps_prompt_with_owner_only_memory_policy(fake_environment):
    orchestrator, _browser, _store, runner = fake_environment

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, remember that I prefer reviewer passes",
    )

    prompt = runner.new_session_calls[0]["prompt"]
    assert "Bob is Owner's personal assistant" in prompt
    assert "CTDM tickets" in prompt
    assert "internal topics" in prompt
    assert "checking work status" in prompt
    assert "approved Slack channels" in prompt
    assert "always use `Bob`" in prompt
    assert "Accepted Slack call signs: Bob" in prompt
    assert "Do not tell the user to use `Codex` as the default name" in prompt
    assert "channel: bob_private_channel" in prompt
    assert "persistent_memory_mode: owner_only" in prompt
    assert "persistent_memory_owner: bob_owner_handle" in prompt
    assert "may use all available tools, skills, MCP servers, and agents" in prompt
    assert "When passing `sh -lc` through another shell layer" in prompt
    assert "escape `$` as `\\$`" in prompt


def test_new_root_message_passes_channel_sandbox_mode_to_runner(fake_environment):
    orchestrator, _browser, _store, runner = fake_environment
    channel = orchestrator.config.workspaces[0].channels[0]
    channel.effective_codex_sandbox_mode = "danger-full-access"

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    assert runner.new_session_calls[0]["sandbox_mode"] == "danger-full-access"


def test_new_root_message_passes_channel_workspace_write_writable_roots_to_runner(fake_environment):
    orchestrator, _browser, _store, runner = fake_environment
    channel = orchestrator.config.workspaces[0].channels[0]
    channel.effective_codex_sandbox_mode = "workspace-write"
    channel.effective_codex_workspace_write_writable_roots = [
        "/Users/bob_owner_handle/workspace",
        "/Users/bob_owner_handle/scratch",
        "/tmp",
    ]

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    assert runner.new_session_calls[0]["workspace_write_writable_roots"] == [
        "/Users/bob_owner_handle/workspace",
        "/Users/bob_owner_handle/scratch",
        "/tmp",
    ]


def test_new_root_message_wraps_prompt_with_disabled_memory_policy_for_shared_channel(
    fake_environment,
):
    orchestrator, _browser, _store, runner = fake_environment
    orchestrator.config.workspaces[0].channels.append(
        ChannelConfig(
            name="bob_channel",
            persistent_memory_mode="disabled",
            effective_default_cwd=orchestrator.config.defaults.default_cwd,
            effective_accept_root_bob_requests=True,
        )
    )

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, help my coworker debug this test",
    )

    prompt = runner.new_session_calls[0]["prompt"]
    assert "channel: bob_channel" in prompt
    assert "persistent_memory_mode: disabled" in prompt
    assert "Bob Owner / Owner's personal durable preference files" in prompt
    assert "similar durable preference files for Bob Owner" in prompt
    assert "do not update personal session notes" in prompt.lower()
    assert "do not modify" in prompt.lower()
    assert ".codex/skills" in prompt
    assert "When passing `sh -lc` through another shell layer" in prompt


def test_new_root_message_uses_isolated_runner_for_isolated_channel(fake_environment):
    orchestrator, _browser, _store, runner = fake_environment
    isolated_runner = FakeCodexRunner(
        next_result=CodexRunResult(session_id="isolated-session", final_output="Isolated answer")
    )
    orchestrator.isolated_codex_runner = isolated_runner
    orchestrator.config.workspaces[0].channels.append(
        ChannelConfig(
            name="bob_channel",
            codex_home_mode="isolated",
            persistent_memory_mode="disabled",
            effective_default_cwd=orchestrator.config.defaults.default_cwd,
            effective_accept_root_bob_requests=True,
            effective_codex_home_mode="isolated",
        )
    )

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, use isolated runner",
    )

    assert runner.new_session_calls == []
    assert isolated_runner.new_session_calls[0]["prompt"].endswith("Bob, use isolated runner")


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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    assert browser.uploaded_snippets == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "thread_ts": "1743461000.000001",
            "filename": "scripts/shepherd/README.md",
            "content": "# Shepherd\nHello",
        }
    ]
    final_post = browser.thread_posts["1743461000.000001"][-1]
    assert "Use this set as a repo-local starter package." in final_post
    assert "scripts/shepherd/README.md" in final_post
    assert "# Shepherd" not in final_post
    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_IDLE


def test_final_output_with_bulleted_generated_files_uploads_snippets(fake_environment):
    orchestrator, browser, _store, runner = fake_environment
    runner.next_result = CodexRunResult(
        session_id="session-123",
        final_output=(
            "Here is a shepherd skill set.\n\n"
            "- **`skills/shepherd/SKILL.md`**:\n"
            "```md\n"
            "# Shepherd Deploy\n"
            "Use this skill.\n"
            "```\n"
        ),
    )

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, build a shepherd skill set",
    )

    assert browser.uploaded_snippets == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "thread_ts": "1743461000.000001",
            "filename": "skills/shepherd/SKILL.md",
            "content": "# Shepherd Deploy\nUse this skill.",
        }
    ]
    final_post = browser.thread_posts["1743461000.000001"][-1]
    assert "Uploaded snippets: `skills/shepherd/SKILL.md`" in final_post


def test_final_output_normalizes_code_fence_language_for_slack(fake_environment):
    orchestrator, browser, _store, runner = fake_environment
    runner.next_result = CodexRunResult(
        session_id="session-123",
        final_output="Use this:\n```md\n# Title\nBody\n```",
    )

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, show markdown",
    )

    final_post = browser.thread_posts["1743461000.000001"][-1]
    assert "```md" not in final_post
    assert "```\n# Title\nBody\n```" in final_post


def test_waiting_for_input_posts_wait_message_and_saves_wait_state(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    runner.next_result = CodexRunResult(
        session_id="session-123",
        wait_kind="input",
        wait_message="Which option do you want?",
    )

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, choose an option",
    )

    assert browser.thread_posts["1743461000.000001"][-1] == "_*Bob needs input :exclamation::*_ Which option do you want?"
    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.WAITING_FOR_INPUT


def test_waiting_for_input_schedules_reminder_and_auto_close(fake_environment):
    orchestrator, _browser, store, runner = fake_environment
    orchestrator.config.lifecycle.reminder_minutes = [30]
    orchestrator.config.lifecycle.auto_close_minutes = 120
    runner.next_result = CodexRunResult(
        session_id="session-123",
        wait_kind="input",
        wait_message="Which option do you want?",
    )
    before = int(time.time())

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, choose an option",
    )

    after = int(time.time())
    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.reminder_due_at is not None
    assert record.auto_close_due_at is not None
    assert before + 30 * 60 <= record.reminder_due_at <= after + 30 * 60
    assert before + 120 * 60 <= record.auto_close_due_at <= after + 120 * 60


def test_unauthorized_actor_is_ignored(fake_environment):
    orchestrator, browser, store, runner = fake_environment

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U999",
        text="Bob, hi there",
    )

    assert browser.thread_posts == {}
    assert len(runner.new_session_calls) == 0
    assert store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001") is None


def test_empty_allowed_actor_ids_allows_any_actor(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.defaults.allowed_actor_ids = []
    orchestrator.config.workspaces[0].channel_defaults.allowed_actor_ids = []

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U999",
        text="Bob, hi there",
    )

    assert len(runner.new_session_calls) == 1
    assert browser.thread_posts["1743461000.000001"][-1] == "_*Bob :white_check_mark::*_ Final answer"
    assert store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001") is not None


def test_channel_allowed_actor_ids_override_workspace_channel_default_allowed_actor_ids(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.workspaces[0].channel_defaults.allowed_actor_ids = ["U123"]
    orchestrator.config.workspaces[0].channels[0].allowed_actor_ids = ["U999"]

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )
    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461001.000001",
        author_actor_id="U999",
        text="Bob, hi there",
    )

    assert store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001") is None
    assert store.get_by_thread("bob_company", "bob_private_channel", "1743461001.000001") is not None
    assert len(runner.new_session_calls) == 1
    assert browser.thread_posts["1743461001.000001"][-1] == "_*Bob :white_check_mark::*_ Final answer"


def test_workspace_channel_default_allowed_actor_ids_are_used_when_channel_override_missing(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.workspaces[0].channel_defaults.allowed_actor_ids = ["U123"]

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U999",
        text="Bob, hi there",
    )
    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461001.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    assert store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001") is None
    assert store.get_by_thread("bob_company", "bob_private_channel", "1743461001.000001") is not None
    assert len(runner.new_session_calls) == 1
    assert browser.thread_posts["1743461001.000001"][-1] == "_*Bob :white_check_mark::*_ Final answer"


def test_duplicate_root_message_is_not_processed_twice(fake_environment):
    orchestrator, browser, store, runner = fake_environment

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )
    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    assert len(runner.new_session_calls) == 1
    assert browser.thread_posts["1743461000.000001"].count("_*Bob :white_check_mark::*_ Final answer") == 1


def test_non_bob_root_message_is_ignored(fake_environment):
    orchestrator, browser, store, runner = fake_environment

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="hello there",
    )

    assert browser.thread_posts == {}
    assert runner.new_session_calls == []
    assert store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001") is None


def test_stale_approval_id_is_rejected(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            message_ts="1743461000.000001",
            author_actor_id="U123",
            text="Bob, hi there",
        )

    assert (
        store.has_processed_message(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1743461000.000001",
            message_ts="1743461000.000001",
            purpose="root_request",
        )
        is False
    )
    assert store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001") is None


def test_new_root_startup_failure_without_session_id_does_not_post_unknown_session(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    runner.next_result = CodexRunResult(failure_text="codex exec failed")

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    thread_posts = browser.thread_posts["1743461000.000001"]
    assert thread_posts == ["_*Bob hit an error :exclamation::*_ Reply again in this thread to retry."]
    assert "unknown-session" not in "\n".join(thread_posts)
    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.codex_session_id.startswith("startup-failed-")
    assert record.status is SessionStatus.FAILED
    assert record.last_error == "codex exec failed"


def test_thread_retry_after_startup_failure_restarts_original_root_prompt(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    runner.next_result = CodexRunResult(failure_text="codex exec failed")
    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, research Hermes",
    )
    runner.next_result = CodexRunResult(session_id="session-456", final_output="recovered")

    orchestrator.handle_thread_reply(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461010.000001",
        author_actor_id="U123",
        text="retry",
    )

    assert len(runner.new_session_calls) == 2
    assert "Bob, research Hermes" in runner.new_session_calls[1]["prompt"]
    assert "User request from Slack:\nretry" not in runner.new_session_calls[1]["prompt"]
    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.codex_session_id == "session-456"
    assert record.status is SessionStatus.CLOSED_IDLE
    assert browser.thread_posts["1743461000.000001"][-2:] == [
        "_*Bob is working on it :arrows_counterclockwise::*_ session=`session-456` thread=`1743461000.000001`",
        "_*Bob :white_check_mark::*_ recovered",
    ]


def test_root_message_claim_is_released_if_session_persistence_fails(fake_environment, monkeypatch):
    orchestrator, _browser, store, _runner = fake_environment

    def fail_upsert_session(**kwargs):
        del kwargs
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(store, "upsert_session", fail_upsert_session)

    with pytest.raises(RuntimeError):
        orchestrator.handle_new_root_message(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            message_ts="1743461000.000001",
            author_actor_id="U123",
            text="Bob, hi there",
        )

    assert (
        store.has_processed_message(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1743461000.000001",
            message_ts="1743461000.000001",
            purpose="root_request",
        )
        is False
    )
    assert store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001") is None


def test_waiting_reply_resume_failure_keeps_waiting_state_and_releases_claim(fake_environment):
    orchestrator, _browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1743461000.000001",
            message_ts="1743461010.000001",
            author_actor_id="U123",
            text="Option A",
        )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.WAITING_FOR_INPUT
    assert (
        store.has_processed_message(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1743461000.000001",
            message_ts="1743461010.000001",
            purpose="thread_reply",
        )
        is False
    )


def test_bob_close_marks_session_closed_without_resuming(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        waiting_message_ts="1743461001.000001",
    )

    orchestrator.handle_thread_reply(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461010.000001",
        author_actor_id="U123",
        text="bob close",
    )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_MANUAL
    assert runner.resume_calls == []
    assert "closed" in browser.thread_posts["1743461000.000001"][-1].lower()


def test_alias_close_marks_session_closed_and_uses_configured_alias_casing(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.defaults.assistant_names = ["Bob", "Bobby"]
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_INPUT,
        assistant_name="Bob",
        waiting_message_ts="1743461001.000001",
    )

    orchestrator.handle_thread_reply(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461010.000001",
        author_actor_id="U123",
        text="close bObBy",
    )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_MANUAL
    assert runner.resume_calls == []
    assert browser.thread_posts["1743461000.000001"][-1].startswith("_*Bobby :white_check_mark::*_")


def test_post_failure_marks_session_failed_instead_of_leaving_it_running(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    browser.post_error = RuntimeError("slack unavailable")

    with pytest.raises(RuntimeError):
        orchestrator.handle_new_root_message(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            message_ts="1743461000.000001",
            author_actor_id="U123",
            text="Bob, hi there",
        )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.FAILED


def test_new_root_message_dispatches_immediately_with_worker_pool(fake_environment):
    orchestrator, _browser, store, _runner = fake_environment
    orchestrator._max_concurrent_tasks = 5  # type: ignore[attr-defined]

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    deadline = time.time() + 2
    while time.time() < deadline:
        if not store.list_tasks(status=TaskStatus.QUEUED) and not store.list_tasks(
            status=TaskStatus.RUNNING
        ):
            break
        time.sleep(0.01)

    assert store.list_tasks(status=TaskStatus.QUEUED) == []
    completed = store.list_tasks(status=TaskStatus.COMPLETED)
    assert len(completed) == 1
    assert completed[0].task_kind == "new_root"
    assert completed[0].prompt_text == "Bob, hi there"


def test_ultimate_invocation_dispatches_immediately_with_worker_pool(fake_environment):
    orchestrator, browser, store, _runner = fake_environment
    orchestrator._max_concurrent_tasks = 5  # type: ignore[attr-defined]
    orchestrator.config.watcher.bob_ultimate_mode = True
    browser.thread_messages[("bob_company", "slack:C999", "1776915000.000001")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776915000.000001",
            message_ts="1776915000.000001",
            author_actor_id="U123",
            text="bob dispatch this now",
        )
    ]

    orchestrator.handle_ultimate_invocation(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776915000.000001",
        message_ts="1776915000.000001",
        author_actor_id="U123",
        text="bob dispatch this now",
    )

    deadline = time.time() + 2
    while time.time() < deadline:
        if not store.list_tasks(status=TaskStatus.QUEUED) and not store.list_tasks(
            status=TaskStatus.RUNNING
        ):
            break
        time.sleep(0.01)

    assert store.list_tasks(status=TaskStatus.QUEUED) == []
    completed = store.list_tasks(status=TaskStatus.COMPLETED)
    assert any(item.task_kind == "ultimate_invocation" for item in completed)


def test_ultimate_root_message_updates_same_message_and_includes_thread_context(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.watcher.bob_ultimate_mode = True
    browser.thread_messages[("bob_company", "slack:C999", "1776911047.025189")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776911047.025189",
            message_ts="1776911047.025189",
            author_actor_id="U123",
            text="bob review this",
        )
    ]

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="slack:C999",
        message_ts="1776911047.025189",
        author_actor_id="U123",
        text="bob review this",
    )

    assert browser.reactions[-1]["message_ts"] == "1776911047.025189"
    assert browser.updated_messages["1776911047.025189"][0].startswith("bob review this\n_*Bob is working on it")
    assert browser.updated_messages["1776911047.025189"][-1].endswith("_*Bob :white_check_mark::*_ Final answer")
    assert "Slack thread transcript:" in runner.new_session_calls[0]["prompt"]
    assert "bob review this" in runner.new_session_calls[0]["prompt"]
    record = store.get_by_thread("bob_company", "slack:C999", "1776911047.025189")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_IDLE


def test_ultimate_reply_invocation_reuses_session_and_updates_same_message(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.watcher.bob_ultimate_mode = True
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776911047.025189",
        root_ts="1776911047.025189",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser.thread_messages[("bob_company", "slack:C999", "1776911047.025189")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776911047.025189",
            message_ts="1776911047.025189",
            author_actor_id="U999",
            text="can you say no?",
        ),
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776911047.025189",
            message_ts="1776911050.000200",
            author_actor_id="U123",
            text="bob can you do it?",
        ),
    ]

    orchestrator.handle_ultimate_invocation(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776911047.025189",
        message_ts="1776911050.000200",
        author_actor_id="U123",
        text="bob can you do it?",
    )

    assert browser.reactions[-1]["message_ts"] == "1776911050.000200"
    assert browser.updated_messages["1776911050.000200"][0].startswith("bob can you do it?\n_*Bob is working on it")
    assert browser.updated_messages["1776911050.000200"][-1].endswith("_*Bob :white_check_mark::*_ Final answer")
    assert len(runner.resume_calls) == 1
    assert "Slack thread transcript:" in runner.resume_calls[0]["prompt"]
    assert "can you say no?" in runner.resume_calls[0]["prompt"]


def test_ultimate_reply_invocation_marks_working_before_resume_returns(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.watcher.bob_ultimate_mode = True
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776911047.025189",
        root_ts="1776911047.025189",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    runner.next_resume_result = CodexRunResult(
        session_id="session-123",
        final_output="Final answer",
    )
    browser.thread_messages[("bob_company", "slack:C999", "1776911047.025189")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776911047.025189",
            message_ts="1776911047.025189",
            author_actor_id="U999",
            text="can you say no?",
        ),
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776911047.025189",
            message_ts="1776911050.000200",
            author_actor_id="U123",
            text="bob can you do it?",
        ),
    ]
    observed = {}

    def _record_working_state(_call):
        record = store.get_by_thread("bob_company", "slack:C999", "1776911047.025189")
        observed["status"] = record.status if record is not None else None
        observed["updates"] = list(browser.updated_messages.get("1776911050.000200", []))

    runner.on_resume = _record_working_state

    orchestrator.handle_ultimate_invocation(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776911047.025189",
        message_ts="1776911050.000200",
        author_actor_id="U123",
        text="bob can you do it?",
    )

    assert observed["status"] is SessionStatus.RUNNING
    assert observed["updates"][0].startswith("bob can you do it?\n_*Bob is working on it")


def test_ultimate_invocation_dispatch_drains_completed_same_thread_worker(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.watcher.bob_ultimate_mode = True
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776915000.000001",
        root_ts="1776915000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    browser.thread_messages[("bob_company", "slack:C999", "1776915000.000001")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776915000.000001",
            message_ts="1776915000.000001",
            author_actor_id="U999",
            text="old context",
        ),
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776915000.000001",
            message_ts="1776915001.000001",
            author_actor_id="U123",
            text="bob can you do it now?",
        ),
    ]
    completed_future = Future()
    completed_future.set_result(None)
    thread_key = ("bob_company", "slack:C999", "1776915000.000001")
    orchestrator._active_tasks[999] = completed_future
    orchestrator._active_task_threads[999] = thread_key
    orchestrator._thread_active_counts[thread_key] = 1

    orchestrator.handle_ultimate_invocation(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776915000.000001",
        message_ts="1776915001.000001",
        author_actor_id="U123",
        text="bob can you do it now?",
    )

    assert len(runner.resume_calls) == 1
    assert browser.updated_messages["1776915001.000001"][-1].endswith(
        "_*Bob :white_check_mark::*_ Final answer"
    )


def test_ultimate_invocation_falls_back_to_thread_reply_if_message_update_fails(fake_environment):
    orchestrator, browser, _store, _runner = fake_environment
    orchestrator.config.watcher.bob_ultimate_mode = True
    browser.update_error = RuntimeError("chat.update failed")
    browser.thread_messages[("bob_company", "slack:C999", "1776911047.025189")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776911047.025189",
            message_ts="1776911047.025189",
            author_actor_id="U123",
            text="bob review this",
        )
    ]

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="slack:C999",
        message_ts="1776911047.025189",
        author_actor_id="U123",
        text="bob review this",
    )

    assert browser.thread_posts["1776911047.025189"][0].startswith("_*Bob is working on it :arrows_counterclockwise::*_")
    assert browser.thread_posts["1776911047.025189"][-1] == "_*Bob :white_check_mark::*_ Final answer"


def test_configured_channel_root_messages_keep_legacy_thread_reply_behavior(fake_environment):
    orchestrator, browser, _store, runner = fake_environment
    orchestrator.config.watcher.bob_ultimate_mode = True
    browser.thread_messages[("bob_company", "bob_private_channel", "1776912000.000001")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1776912000.000001",
            message_ts="1776912000.000001",
            author_actor_id="U123",
            text="bob review this configured channel",
        )
    ]

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1776912000.000001",
        author_actor_id="U123",
        text="bob review this configured channel",
    )

    assert browser.updated_messages == {}
    assert browser.thread_posts["1776912000.000001"][0].startswith(
        "_*Bob is working on it :arrows_counterclockwise::*_"
    )
    assert browser.thread_posts["1776912000.000001"][-1] == "_*Bob :white_check_mark::*_ Final answer"
    assert len(runner.new_session_calls) == 1


def test_configured_channel_thread_reply_without_existing_session_can_use_ultimate_mode(
    fake_environment,
):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.watcher.bob_ultimate_mode = True
    browser.thread_messages[("bob_company", "bob_private_channel", "1776912100.000001")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1776912100.000001",
            message_ts="1776912100.000001",
            author_actor_id="U999",
            text="hello there",
        ),
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1776912100.000001",
            message_ts="1776912105.000001",
            author_actor_id="U123",
            text="bob can you do it here?",
        ),
    ]

    orchestrator.handle_ultimate_invocation(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1776912100.000001",
        message_ts="1776912105.000001",
        author_actor_id="U123",
        text="bob can you do it here?",
    )

    assert browser.updated_messages["1776912105.000001"][0].startswith(
        "bob can you do it here?\n_*Bob is working on it"
    )
    assert browser.updated_messages["1776912105.000001"][-1].endswith(
        "_*Bob :white_check_mark::*_ Final answer"
    )
    record = store.get_by_thread("bob_company", "bob_private_channel", "1776912100.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_IDLE
    assert len(runner.new_session_calls) == 1


def test_ultimate_invocation_uses_default_runner_when_ultimate_codex_home_mode_is_default(
    fake_environment,
):
    orchestrator, browser, store, runner = fake_environment
    isolated_runner = FakeCodexRunner(
        next_result=CodexRunResult(session_id="isolated-session", final_output="Isolated answer")
    )
    orchestrator.isolated_codex_runner = isolated_runner
    orchestrator.config.watcher.bob_ultimate_mode = True
    orchestrator.config.watcher.bob_ultimate_mode_codex_home_mode = "default"
    browser.thread_messages[("bob_company", "slack:C999", "1776912200.000001")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776912200.000001",
            message_ts="1776912200.000001",
            author_actor_id="U123",
            text="bob use default home",
        )
    ]

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="slack:C999",
        message_ts="1776912200.000001",
        author_actor_id="U123",
        text="bob use default home",
    )

    assert len(runner.new_session_calls) == 1
    assert isolated_runner.new_session_calls == []
    record = store.get_by_thread("bob_company", "slack:C999", "1776912200.000001")
    assert record is not None
    assert record.codex_session_id == "session-123"


def test_ultimate_invocation_uses_isolated_runner_when_ultimate_codex_home_mode_is_isolated(
    fake_environment,
):
    orchestrator, browser, store, runner = fake_environment
    isolated_runner = FakeCodexRunner(
        next_result=CodexRunResult(session_id="isolated-session", final_output="Isolated answer")
    )
    orchestrator.isolated_codex_runner = isolated_runner
    orchestrator.config.watcher.bob_ultimate_mode = True
    orchestrator.config.watcher.bob_ultimate_mode_codex_home_mode = "isolated"
    browser.thread_messages[("bob_company", "bob_private_channel", "1776912300.000001")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1776912300.000001",
            message_ts="1776912300.000001",
            author_actor_id="U999",
            text="hello there",
        ),
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1776912300.000001",
            message_ts="1776912305.000001",
            author_actor_id="U123",
            text="bob use isolated home",
        ),
    ]

    orchestrator.handle_ultimate_invocation(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1776912300.000001",
        message_ts="1776912305.000001",
        author_actor_id="U123",
        text="bob use isolated home",
    )

    assert runner.new_session_calls == []
    assert len(isolated_runner.new_session_calls) == 1
    record = store.get_by_thread("bob_company", "bob_private_channel", "1776912300.000001")
    assert record is not None
    assert record.codex_session_id == "isolated-session"


def test_ultimate_waiting_approval_accepts_bob_prefixed_approve(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.watcher.bob_ultimate_mode = True
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776913000.000001",
        root_ts="1776913000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_APPROVAL,
        approval_request_id="APR-001",
        approval_command_summary="git status -sb",
    )
    runner.next_resume_result = CodexRunResult(
        session_id="session-123",
        final_output="approved",
    )
    browser.thread_messages[("bob_company", "slack:C999", "1776913000.000001")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776913000.000001",
            message_ts="1776913000.000001",
            author_actor_id="U999",
            text="please approve",
        ),
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776913000.000001",
            message_ts="1776913001.000001",
            author_actor_id="U123",
            text="bob approve APR-001",
        ),
    ]

    orchestrator.handle_ultimate_invocation(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776913000.000001",
        message_ts="1776913001.000001",
        author_actor_id="U123",
        text="bob approve APR-001",
    )

    assert runner.resume_calls[-1]["prompt"] == "approve APR-001"
    assert browser.updated_messages["1776913001.000001"][-1].endswith(
        "_*Bob :white_check_mark::*_ approved"
    )


def test_ultimate_invocation_restarts_with_new_session_when_resume_rollout_is_missing(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.watcher.bob_ultimate_mode = True
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776914000.000001",
        root_ts="1776914000.000001",
        codex_session_id="stale-session",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    runner.next_resume_result = CodexRunResult(
        session_id="stale-session",
        failure_text="Error: thread/resume: thread/resume failed: no rollout found for thread id stale-session",
    )
    runner.next_result = CodexRunResult(
        session_id="fresh-session",
        final_output="recovered",
    )
    browser.thread_messages[("bob_company", "slack:C999", "1776914000.000001")] = [
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776914000.000001",
            message_ts="1776914000.000001",
            author_actor_id="U999",
            text="old context",
        ),
        SlackThreadMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="1776914000.000001",
            message_ts="1776914001.000001",
            author_actor_id="U123",
            text="bob try again",
        ),
    ]

    orchestrator.handle_ultimate_invocation(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776914000.000001",
        message_ts="1776914001.000001",
        author_actor_id="U123",
        text="bob try again",
    )

    assert len(runner.resume_calls) == 1
    assert len(runner.new_session_calls) == 1
    record = store.get_by_thread("bob_company", "slack:C999", "1776914000.000001")
    assert record is not None
    assert record.codex_session_id == "fresh-session"
    assert record.status is SessionStatus.CLOSED_IDLE
    assert browser.updated_messages["1776914001.000001"][-1].endswith(
        "_*Bob :white_check_mark::*_ recovered"
    )


def test_process_scheduled_actions_runs_up_to_global_concurrency_limit(tmp_path):
    store = BobStateStore(tmp_path / "bob.sqlite3")
    store.initialize()
    browser = FakeSlackBrowser()
    runner = FakeCodexRunner()
    config = AppConfig(
        defaults=DefaultSettings(
            default_cwd=str(tmp_path),
            additional_roots=[str(tmp_path / "roots")],
            allowed_actor_ids=["U123"],
        ),
        orchestrator=OrchestratorSettings(
            max_concurrent_tasks=5,
            max_concurrent_per_thread=1,
        ),
        workspaces=[
            WorkspaceConfig(
                name="bob_company",
                channels=[
                    ChannelConfig(
                        name="bob_private_channel",
                        persistent_memory_mode="owner_only",
                        persistent_memory_owner="bob_owner_handle",
                        effective_default_cwd=str(tmp_path),
                        effective_additional_roots=[str(tmp_path / "roots")],
                        effective_accept_root_bob_requests=True,
                    )
                ],
            )
        ],
    )

    release_event = threading.Event()
    started_lock = threading.Lock()
    started_threads: List[str] = []

    def blocking_run_new_session(
        prompt: str,
        cwd: str,
        additional_roots: List[str],
        sandbox_mode: Optional[str] = None,
        workspace_write_writable_roots: Optional[List[str]] = None,
        on_session_started: Optional[Callable[[str], None]] = None,
    ) -> CodexRunResult:
        del cwd
        del additional_roots
        del sandbox_mode
        del workspace_write_writable_roots
        thread_marker = prompt.splitlines()[-1]
        with started_lock:
            started_threads.append(thread_marker)
        if on_session_started is not None:
            on_session_started("session-{0}".format(len(started_threads)))
        release_event.wait(timeout=5)
        return CodexRunResult(
            session_id="session-{0}".format(len(started_threads)),
            final_output="done",
        )

    runner.run_new_session = blocking_run_new_session  # type: ignore[method-assign]
    orchestrator = BobOrchestrator(
        browser=browser,
        state_store=store,
        codex_runner=runner,
        config=config,
    )

    for index in range(6):
        thread_ts = "174346100{0}.000001".format(index)
        orchestrator.handle_new_root_message(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            message_ts=thread_ts,
            author_actor_id="U123",
            text="Bob, task {0}".format(index),
        )

    deadline = time.time() + 5
    while time.time() < deadline:
        orchestrator.process_scheduled_actions()
        with started_lock:
            if len(started_threads) >= 5:
                break
        time.sleep(0.01)

    with started_lock:
        assert len(started_threads) == 5

    queued = store.list_tasks(status=TaskStatus.QUEUED)
    assert len(queued) == 1

    release_event.set()
    deadline = time.time() + 5
    while time.time() < deadline:
        orchestrator.process_scheduled_actions()
        if not store.list_tasks(status=TaskStatus.QUEUED) and not store.list_tasks(
            status=TaskStatus.RUNNING
        ):
            break
        time.sleep(0.01)

    assert store.list_tasks(status=TaskStatus.QUEUED) == []
    assert store.list_tasks(status=TaskStatus.RUNNING) == []


def test_same_thread_tasks_do_not_overlap(tmp_path):
    store = BobStateStore(tmp_path / "bob.sqlite3")
    store.initialize()
    browser = FakeSlackBrowser()
    runner = FakeCodexRunner()
    config = AppConfig(
        defaults=DefaultSettings(
            default_cwd=str(tmp_path),
            additional_roots=[str(tmp_path / "roots")],
            allowed_actor_ids=["U123"],
        ),
        orchestrator=OrchestratorSettings(
            max_concurrent_tasks=5,
            max_concurrent_per_thread=1,
        ),
        workspaces=[
            WorkspaceConfig(
                name="bob_company",
                channels=[
                    ChannelConfig(
                        name="bob_private_channel",
                        persistent_memory_mode="owner_only",
                        persistent_memory_owner="bob_owner_handle",
                        effective_default_cwd=str(tmp_path),
                        effective_additional_roots=[str(tmp_path / "roots")],
                        effective_accept_root_bob_requests=True,
                    )
                ],
            )
        ],
    )

    release_first = threading.Event()
    second_started = threading.Event()
    call_order: List[str] = []

    def blocking_run_new_session(
        prompt: str,
        cwd: str,
        additional_roots: List[str],
        sandbox_mode: Optional[str] = None,
        workspace_write_writable_roots: Optional[List[str]] = None,
        on_session_started: Optional[Callable[[str], None]] = None,
    ) -> CodexRunResult:
        del cwd
        del additional_roots
        del sandbox_mode
        del workspace_write_writable_roots
        call_order.append(prompt.splitlines()[-1])
        if on_session_started is not None:
            on_session_started("session-{0}".format(len(call_order)))
        if len(call_order) == 1:
            release_first.wait(timeout=5)
        else:
            second_started.set()
        return CodexRunResult(
            session_id="session-{0}".format(len(call_order)),
            final_output="done",
        )

    def blocking_resume_session(
        session_id: str,
        prompt: str,
        cwd: str,
        sandbox_mode: Optional[str] = None,
        workspace_write_writable_roots: Optional[List[str]] = None,
    ) -> CodexRunResult:
        del session_id
        del cwd
        del sandbox_mode
        del workspace_write_writable_roots
        call_order.append(prompt.splitlines()[-1])
        second_started.set()
        return CodexRunResult(
            session_id="session-1",
            final_output="done",
        )

    runner.run_new_session = blocking_run_new_session  # type: ignore[method-assign]
    runner.resume_session = blocking_resume_session  # type: ignore[method-assign]
    orchestrator = BobOrchestrator(
        browser=browser,
        state_store=store,
        codex_runner=runner,
        config=config,
    )

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, first",
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        orchestrator.process_scheduled_actions()
        if store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001") is not None:
            break
        time.sleep(0.01)

    orchestrator.handle_thread_reply(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461001.000001",
        author_actor_id="U123",
        text="Bob, second",
    )

    deadline = time.time() + 5
    while time.time() < deadline and len(call_order) < 1:
        orchestrator.process_scheduled_actions()
        time.sleep(0.01)

    assert call_order == ["Bob, first"]
    assert second_started.is_set() is False

    release_first.set()
    deadline = time.time() + 5
    while time.time() < deadline:
        orchestrator.process_scheduled_actions()
        if second_started.is_set():
            break
        time.sleep(0.01)

    assert second_started.is_set() is True


def test_closed_idle_reply_resumes_same_session(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461020.000001",
        author_actor_id="U123",
        text="What about a follow-up?",
    )

    assert len(runner.resume_calls) == 1
    assert runner.resume_calls[0]["cwd"] == "/tmp/project"
    assert browser.reactions == [
        {
            "workspace_name": "bob_company",
            "channel_name": "bob_private_channel",
            "message_ts": "1743461020.000001",
            "emoji_name": "ok_hand",
        }
    ]
    assert browser.thread_posts["1743461000.000001"][-1] == "_*Bob :white_check_mark::*_ Follow-up answer"


def test_closed_idle_reply_marks_running_before_resume_returns(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    runner.next_resume_result = CodexRunResult(
        session_id="session-123",
        final_output="Follow-up answer",
    )
    observed = {}

    def _record_running_state(_call):
        record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
        observed["status"] = record.status if record is not None else None
        observed["posts"] = list(browser.thread_posts.get("1743461000.000001", []))

    runner.on_resume = _record_running_state

    orchestrator.handle_thread_reply(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461020.000001",
        author_actor_id="U123",
        text="What about a follow-up?",
    )

    assert observed["status"] is SessionStatus.RUNNING
    assert observed["posts"] == []


def test_root_message_continues_when_reaction_fails(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    browser.reaction_error = RuntimeError("reaction failed")

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, hi there",
    )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_IDLE
    assert browser.thread_posts["1743461000.000001"][-1] == "_*Bob :white_check_mark::*_ Final answer"


def test_approval_accept_posts_final_message_without_working_status(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461050.000001",
        author_actor_id="U123",
        text="approve APR-001",
    )

    assert browser.thread_posts["1743461000.000001"][-1] == "_*Bob :white_check_mark::*_ Approved answer"


def test_approval_accept_marks_running_before_resume_returns(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_APPROVAL,
        approval_request_id="APR-001",
        approval_command_summary="git status -sb",
    )
    runner.next_resume_result = CodexRunResult(
        session_id="session-123",
        final_output="Approved answer",
    )
    observed = {}

    def _record_running_state(_call):
        record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
        observed["status"] = record.status if record is not None else None
        observed["posts"] = list(browser.thread_posts.get("1743461000.000001", []))

    runner.on_resume = _record_running_state

    orchestrator.handle_thread_reply(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461050.000001",
        author_actor_id="U123",
        text="approve APR-001",
    )

    assert observed["status"] is SessionStatus.RUNNING
    assert observed["posts"] == []


def test_closed_idle_reply_resume_reasserts_disabled_memory_policy(fake_environment):
    orchestrator, _browser, store, runner = fake_environment
    orchestrator.config.workspaces[0].channels.append(
        ChannelConfig(
            name="bob_test_channel",
            persistent_memory_mode="disabled",
            effective_default_cwd=orchestrator.config.defaults.default_cwd,
            effective_accept_root_bob_requests=True,
        )
    )
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_test_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )

    orchestrator.handle_thread_reply(
        workspace_name="bob_company",
        channel_name="bob_test_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461010.000001",
        author_actor_id="U123",
        text="Keep investigating",
    )

    prompt = runner.resume_calls[0]["prompt"]
    assert "personal assistant" in prompt
    assert "CTDM tickets" in prompt
    assert "internal topics" in prompt
    assert "checking work status" in prompt
    assert "approved Slack channels" in prompt
    assert "always use `Bob`" in prompt
    assert "Do not tell the user to use `Codex` as the default name" in prompt
    assert "channel: bob_test_channel" in prompt
    assert "persistent_memory_mode: disabled" in prompt
    assert "do not update personal session notes" in prompt.lower()


def test_closed_idle_reply_from_non_owner_resumes_when_workspace_is_unrestricted(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.defaults.allowed_actor_ids = []
    orchestrator.config.workspaces[0].channel_defaults.allowed_actor_ids = []
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461020.000001",
        author_actor_id="U999",
        text="What about a follow-up?",
    )

    assert len(runner.resume_calls) == 1
    assert browser.thread_posts["1743461000.000001"][-1] == "_*Bob :white_check_mark::*_ Follow-up answer"


def test_waiting_reply_deletes_previous_wait_prompt_before_resuming(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
    orchestrator.config.workspaces[0].channel_defaults.allowed_actor_ids = []
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461020.000001",
        author_actor_id="U999",
        text="Option A",
    )

    assert browser.deleted_messages == ["1743461001.000001"]
    assert runner.resume_calls[0]["prompt"].endswith("User request from Slack:\nOption A")


def test_process_due_reminders_posts_reminder_and_schedules_next_one(fake_environment):
    orchestrator, browser, store, _runner = fake_environment
    orchestrator.config.lifecycle.reminder_minutes = [30, 60]
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.WAITING_FOR_INPUT
    assert record.reminder_count == 1
    assert record.reminder_due_at == 5 + 60 * 60
    assert "reminder" in browser.thread_posts["1743461000.000001"][-1].lower()


def test_process_due_auto_closes_closes_waiting_session_and_deletes_prompt(fake_environment):
    orchestrator, browser, store, _runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_TIMEOUT
    assert browser.deleted_messages == ["1743461001.000001"]
    assert "timed out" in browser.thread_posts["1743461000.000001"][-1].lower()


def test_failed_reply_resumes_same_session(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461021.000001",
        author_actor_id="U123",
        text="Try again",
    )

    assert len(runner.resume_calls) == 1
    assert runner.resume_calls[0]["cwd"] == "/tmp/project"
    assert browser.thread_posts["1743461000.000001"][-1] == "_*Bob :white_check_mark::*_ Recovered answer"


def test_closed_idle_reply_rebinds_when_resume_returns_new_session_id(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="stale-session",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    runner.next_resume_result = CodexRunResult(
        session_id="fresh-session",
        final_output="resumed on a fresh thread",
    )

    orchestrator.handle_thread_reply(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461010.000001",
        author_actor_id="U123",
        text="continue",
    )

    assert len(runner.resume_calls) == 1
    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.codex_session_id == "fresh-session"
    assert record.status is SessionStatus.CLOSED_IDLE
    assert browser.thread_posts["1743461000.000001"][-1] == (
        "_*Bob :white_check_mark::*_ resumed on a fresh thread"
    )


def test_closed_idle_reply_timeout_marks_session_closed_timeout_and_posts_resume_hint(
    fake_environment,
):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    runner.next_resume_result = CodexRunResult(
        session_id="session-123",
        failure_text="codex exec timed out after 600s",
    )

    orchestrator.handle_thread_reply(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461021.000001",
        author_actor_id="U123",
        text="Try again",
    )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_TIMEOUT
    assert record.last_error == "codex exec timed out after 600s"
    assert browser.thread_posts["1743461000.000001"][-1] == (
        "_*Bob timed out :hourglass_flowing_sand::*_ "
        "codex exec timed out after 600s Reply again in this thread to resume."
    )


def test_closed_idle_reply_non_timeout_failure_marks_session_failed_and_posts_error(
    fake_environment,
):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    runner.next_resume_result = CodexRunResult(
        session_id="session-123",
        failure_text="Command failed with exit code 1",
    )

    orchestrator.handle_thread_reply(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461021.000001",
        author_actor_id="U123",
        text="Try again",
    )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.FAILED
    assert record.last_error == "Command failed with exit code 1"
    assert browser.thread_posts["1743461000.000001"][-1] == (
        "_*Bob hit an error :exclamation::*_ Reply again in this thread to retry."
    )


def test_closed_idle_reply_resume_exception_restores_previous_status_and_releases_claim(
    fake_environment,
):
    orchestrator, _browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )
    runner.resume_error = RuntimeError("resume failed")

    with pytest.raises(RuntimeError, match="resume failed"):
        orchestrator.handle_thread_reply(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1743461000.000001",
            message_ts="1743461020.000001",
            author_actor_id="U123",
            text="Try again",
        )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.CLOSED_IDLE
    assert not store.has_processed_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461020.000001",
        purpose="thread_reply",
    )


def test_approval_accept_resume_exception_restores_waiting_status_and_releases_claim(
    fake_environment,
):
    orchestrator, _browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.WAITING_FOR_APPROVAL,
        approval_request_id="APR-001",
        approval_command_summary="git status -sb",
    )
    runner.resume_error = RuntimeError("resume failed")

    with pytest.raises(RuntimeError, match="resume failed"):
        orchestrator.handle_thread_reply(
            workspace_name="bob_company",
            channel_name="bob_private_channel",
            thread_ts="1743461000.000001",
            message_ts="1743461050.000001",
            author_actor_id="U123",
            text="approve APR-001",
        )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.WAITING_FOR_APPROVAL
    assert record.approval_request_id == "APR-001"
    assert not store.has_processed_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461050.000001",
        purpose="thread_reply",
    )


def test_second_waiting_input_prompt_is_posted(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    runner.next_result = CodexRunResult(
        session_id="session-123",
        wait_kind="input",
        wait_message="First question?",
    )
    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461030.000001",
        author_actor_id="U123",
        text="Answer one",
    )

    posts = browser.thread_posts["1743461000.000001"]
    assert "_*Bob needs input :exclamation::*_ First question?" in posts
    assert "_*Bob needs input :exclamation::*_ Second question?" in posts
    assert posts.count("_*Bob needs input :exclamation::*_ Second question?") == 1
    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob run command",
    )

    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob run safe command",
    )

    assert runner.resume_calls == [
        {
            "session_id": "session-123",
            "prompt": "approve APR-001",
            "cwd": str(store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001").cwd),
            "sandbox_mode": None,
            "workspace_write_writable_roots": None,
        }
    ]
    posts = browser.thread_posts["1743461000.000001"]
    assert all("_*Bob needs approval :exclamation::*_" not in post for post in posts)
    assert posts[-1] == "_*Bob :white_check_mark::*_ Auto-approved answer"


def test_high_risk_approval_still_requires_slack_prompt(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    runner.next_result = CodexRunResult(
        session_id="session-123",
        wait_kind="approval",
        wait_message="rm -rf /tmp/demo APR-001",
    )

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob run risky command",
    )

    assert runner.resume_calls == []
    record = store.get_by_thread("bob_company", "bob_private_channel", "1743461000.000001")
    assert record is not None
    assert record.status is SessionStatus.WAITING_FOR_APPROVAL
    assert browser.thread_posts["1743461000.000001"][-1].startswith(
        "_*Bob needs approval :exclamation::*_"
    )


def test_approval_accept_resumes_same_session_with_cwd(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
            "sandbox_mode": None,
            "workspace_write_writable_roots": None,
        }
    ]
    assert browser.thread_posts["1743461000.000001"][-1] == "_*Bob :white_check_mark::*_ Approved answer"


def test_approval_accept_preserves_channel_sandbox_mode_on_resume(fake_environment):
    orchestrator, _browser, store, runner = fake_environment
    channel = orchestrator.config.workspaces[0].channels[0]
    channel.effective_codex_sandbox_mode = "danger-full-access"
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
            "sandbox_mode": "danger-full-access",
            "workspace_write_writable_roots": None,
        }
    ]


def test_approval_accept_preserves_channel_workspace_write_writable_roots_on_resume(fake_environment):
    orchestrator, _browser, store, runner = fake_environment
    channel = orchestrator.config.workspaces[0].channels[0]
    channel.effective_codex_sandbox_mode = "workspace-write"
    channel.effective_codex_workspace_write_writable_roots = [
        "/Users/bob_owner_handle/workspace",
        "/Users/bob_owner_handle/scratch",
        "/tmp",
    ]
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
            "sandbox_mode": "workspace-write",
            "workspace_write_writable_roots": [
                "/Users/bob_owner_handle/workspace",
                "/Users/bob_owner_handle/scratch",
                "/tmp",
            ],
        }
    ]


def test_deny_and_cancel_have_distinct_audit_messages(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
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
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461040.000001",
        author_actor_id="U123",
        text="deny APR-001",
    )

    assert "denied" in browser.thread_posts["1743461000.000001"][-1].lower()
    assert runner.resume_calls == []
