from dataclasses import asdict
import json
import sqlite3

from personal_slack_agent.cli import agent as agent_module
from personal_slack_agent.cli.agent import run_once
from personal_slack_agent.models import (
    ChannelConfig,
    DefaultSettings,
    WorkspaceConfig,
)
from personal_slack_agent.slack import SlackRootMessage, SlackThreadReplyMessage


def test_slack_message_contract_dataclasses_preserve_required_fields():
    root = SlackRootMessage(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, summarize this",
    )
    reply = SlackThreadReplyMessage(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1743461000.000001",
        message_ts="1743461010.000001",
        author_actor_id="U123",
        text="Please continue",
    )

    assert asdict(root) == {
        "workspace_name": "oracle",
        "channel_name": "yifanche-private",
        "thread_ts": "1743461000.000001",
        "message_ts": "1743461000.000001",
        "author_actor_id": "U123",
        "text": "Bob, summarize this",
    }
    assert asdict(reply) == {
        "workspace_name": "oracle",
        "channel_name": "yifanche-private",
        "thread_ts": "1743461000.000001",
        "message_ts": "1743461010.000001",
        "author_actor_id": "U123",
        "text": "Please continue",
    }


def test_run_once_builds_runtime_stack_and_executes_watcher_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / "bob.toml"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config_file.write_text(
        "\n".join(
            [
                "[defaults]",
                'default_cwd = "{0}"'.format(workspace_root),
                'allowed_actor_ids = ["U123"]',
                'browser_mode = "shared_browser"',
                "",
                "[[workspaces]]",
                'name = "oracle"',
                'allowed_actor_ids = ["U123"]',
                'slack_url = "https://app.slack.com/client/T12345678/C12345678"',
                "",
                "[[workspaces.channels]]",
                'name = "yifanche-private"',
                'persistent_memory_mode = "owner_only"',
                'persistent_memory_owner = "yifanche"',
            ]
        ),
        encoding="utf-8",
    )

    calls = {
        "cycle": 0,
        "workspace_urls": None,
        "workspace_api_contexts": None,
        "channel_urls": None,
        "runner_kwargs": None,
    }

    class FakeBrowser:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_workspace_urls(self, workspace_urls):
            calls["workspace_urls"] = dict(workspace_urls)

        def set_workspace_api_contexts(self, workspace_api_contexts):
            calls["workspace_api_contexts"] = dict(workspace_api_contexts)

        def set_channel_urls(self, channel_urls):
            calls["channel_urls"] = dict(channel_urls)

        def close(self):
            return None

    class FakeRunner:
        def __init__(self, **kwargs):
            calls["runner_kwargs"] = kwargs

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def process_scheduled_actions(self):
            return None

    class FakeWatcher:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_cycle(self):
            calls["cycle"] += 1

    monkeypatch.setattr(agent_module, "PlaywrightSlackAdapter", FakeBrowser)
    monkeypatch.setattr(agent_module, "SubprocessCodexRunner", FakeRunner)
    monkeypatch.setattr(agent_module, "BobOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(agent_module, "SlackWatcher", FakeWatcher)

    exit_code = run_once(config_file)

    assert exit_code == 0
    assert calls["cycle"] == 1
    assert calls["workspace_urls"] == {
        "oracle": "https://app.slack.com/client/T12345678/C12345678"
    }
    assert calls["workspace_api_contexts"] == {}
    assert calls["channel_urls"] == {}
    assert calls["runner_kwargs"]["env_overrides"]["CODEX_HOME"].endswith("/codex-home")


def test_prepare_bob_codex_home_links_config_without_hooks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text('model = "gpt-5.4"\n', encoding="utf-8")
    (codex_home / "hooks.json").write_text('{"hooks":{}}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    bob_home = agent_module._prepare_bob_codex_home(tmp_path / "state" / "codex-home")

    assert bob_home == tmp_path / "state" / "codex-home"
    assert (bob_home / "config.toml").exists()
    assert (bob_home / "config.toml").read_text(encoding="utf-8") == 'model = "gpt-5.4"\n'
    assert not (bob_home / "hooks.json").exists()


def test_prepare_bob_codex_home_replaces_existing_real_skill_directory_with_symlink(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    source_skills = codex_home / "skills"
    source_skills.mkdir(parents=True)
    (source_skills / "cds-ops-skill").mkdir()
    (source_skills / "cds-ops-skill" / "SKILL.md").write_text(
        "Use when doing CDS ops.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    bob_home_root = tmp_path / "state" / "codex-home"
    existing_skills = bob_home_root / "skills"
    (existing_skills / ".system").mkdir(parents=True)
    (existing_skills / ".system" / ".codex-system-skills.marker").write_text(
        "marker\n",
        encoding="utf-8",
    )

    bob_home = agent_module._prepare_bob_codex_home(bob_home_root)

    assert bob_home == bob_home_root
    assert (bob_home / "skills").is_symlink()
    assert (bob_home / "skills").resolve() == source_skills.resolve()
    assert (bob_home / "skills" / "cds-ops-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "Use when doing CDS ops.\n"


def test_prepare_bob_codex_home_rewrites_migrated_runtime_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text('model = "gpt-5.4"\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    old_home = "/private/tmp/personal-slack-agent/custom-bob-home"
    shell_snapshot = bob_home / "shell_snapshots" / "snapshot.sh"
    shell_snapshot.parent.mkdir(parents=True, exist_ok=True)
    shell_snapshot.write_text(
        "\n".join(
            [
                "export CODEX_HOME={0}".format(old_home),
                "export HOME=/tmp/personal-slack-agent-smoke",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    history = bob_home / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(
        '{"rollout_path":"%s/sessions/2026/04/12/example.jsonl"}\n' % old_home,
        encoding="utf-8",
    )

    state_db = bob_home / "state_5.sqlite"
    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                agent_nickname TEXT,
                agent_role TEXT,
                memory_mode TEXT NOT NULL DEFAULT 'enabled',
                model TEXT,
                reasoning_effort TEXT,
                agent_path TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE agent_jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                instruction TEXT NOT NULL,
                output_schema_json TEXT,
                input_headers_json TEXT NOT NULL,
                input_csv_path TEXT NOT NULL,
                output_csv_path TEXT NOT NULL,
                auto_export INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                started_at INTEGER,
                completed_at INTEGER,
                last_error TEXT,
                max_runtime_seconds INTEGER
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-1",
                old_home + "/sessions/2026/04/12/example.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories"]}' % old_home,
            ),
        )
        connection.execute(
            """
            INSERT INTO agent_jobs (
                id, name, status, instruction, input_headers_json,
                input_csv_path, output_csv_path, created_at, updated_at
            ) VALUES (?, 'job', 'queued', 'instruction', '{}', ?, ?, 0, 0)
            """,
            (
                "job-1",
                old_home + "/tmp/input.csv",
                old_home + "/tmp/output.csv",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    assert prepared == bob_home
    expected_home = str(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        rollout_path, sandbox_policy = connection.execute(
            "SELECT rollout_path, sandbox_policy FROM threads WHERE id = 'thread-1'"
        ).fetchone()
        input_csv_path, output_csv_path = connection.execute(
            "SELECT input_csv_path, output_csv_path FROM agent_jobs WHERE id = 'job-1'"
        ).fetchone()
    finally:
        connection.close()

    assert rollout_path == expected_home + "/sessions/2026/04/12/example.jsonl"
    assert sandbox_policy == (
        '{"type":"workspace-write","writable_roots":["%s/memories"]}' % expected_home
    )
    assert input_csv_path == expected_home + "/tmp/input.csv"
    assert output_csv_path == expected_home + "/tmp/output.csv"
    assert shell_snapshot.read_text(encoding="utf-8").splitlines()[0] == (
        "export CODEX_HOME={0}".format(expected_home)
    )
    assert expected_home in history.read_text(encoding="utf-8")
    assert old_home not in history.read_text(encoding="utf-8")


def test_prepare_bob_codex_home_rewrites_threads_when_agent_jobs_table_missing(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    old_home = "/private/tmp/personal-slack-agent/custom-bob-home"
    state_db = bob_home / "state_5.sqlite"
    state_db.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                agent_nickname TEXT,
                agent_role TEXT,
                memory_mode TEXT NOT NULL DEFAULT 'enabled',
                model TEXT,
                reasoning_effort TEXT,
                agent_path TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-1",
                old_home + "/sessions/2026/04/12/example.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories"]}' % old_home,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        rollout_path, sandbox_policy = connection.execute(
            "SELECT rollout_path, sandbox_policy FROM threads WHERE id = 'thread-1'"
        ).fetchone()
    finally:
        connection.close()

    assert prepared == bob_home
    assert rollout_path == str(bob_home) + "/sessions/2026/04/12/example.jsonl"
    assert sandbox_policy == (
        '{"type":"workspace-write","writable_roots":["%s/memories"]}' % bob_home
    )


def test_prepare_bob_codex_home_rewrites_agent_jobs_when_only_agent_jobs_are_stale(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    old_home = "/private/tmp/personal-slack-agent/custom-bob-home"
    state_db = bob_home / "state_5.sqlite"
    state_db.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                agent_nickname TEXT,
                agent_role TEXT,
                memory_mode TEXT NOT NULL DEFAULT 'enabled',
                model TEXT,
                reasoning_effort TEXT,
                agent_path TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE agent_jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                instruction TEXT NOT NULL,
                output_schema_json TEXT,
                input_headers_json TEXT NOT NULL,
                input_csv_path TEXT NOT NULL,
                output_csv_path TEXT NOT NULL,
                auto_export INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                started_at INTEGER,
                completed_at INTEGER,
                last_error TEXT,
                max_runtime_seconds INTEGER
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-1",
                str(bob_home) + "/sessions/2026/04/12/example.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories"]}' % bob_home,
            ),
        )
        connection.execute(
            """
            INSERT INTO agent_jobs (
                id, name, status, instruction, input_headers_json,
                input_csv_path, output_csv_path, created_at, updated_at
            ) VALUES (?, 'job', 'queued', 'instruction', '{}', ?, ?, 0, 0)
            """,
            (
                "job-1",
                old_home + "/tmp/input.csv",
                old_home + "/tmp/output.csv",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        input_csv_path, output_csv_path = connection.execute(
            "SELECT input_csv_path, output_csv_path FROM agent_jobs WHERE id = 'job-1'"
        ).fetchone()
    finally:
        connection.close()

    assert prepared == bob_home
    assert input_csv_path == str(bob_home) + "/tmp/input.csv"
    assert output_csv_path == str(bob_home) + "/tmp/output.csv"


def test_prepare_bob_codex_home_rewrites_threads_without_sandbox_policy_column(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    old_home = "/private/tmp/personal-slack-agent/custom-bob-home"
    state_db = bob_home / "state_5.sqlite"
    state_db.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                approval_mode TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', 'never')
            """,
            (
                "thread-1",
                old_home + "/sessions/2026/04/12/example.jsonl",
                str(tmp_path / "repo"),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        rollout_path = connection.execute(
            "SELECT rollout_path FROM threads WHERE id = 'thread-1'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert prepared == bob_home
    assert rollout_path == str(bob_home) + "/sessions/2026/04/12/example.jsonl"


def test_prepare_bob_codex_home_does_not_rewrite_unrelated_memories_root(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    old_home = "/private/tmp/personal-slack-agent/custom-bob-home"
    unrelated_memories = "/Users/yifanche/workspace/project/memories"
    state_db = bob_home / "state_5.sqlite"
    state_db.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                agent_nickname TEXT,
                agent_role TEXT,
                memory_mode TEXT NOT NULL DEFAULT 'enabled',
                model TEXT,
                reasoning_effort TEXT,
                agent_path TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-1",
                old_home + "/sessions/2026/04/12/example.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories","%s"]}'
                % (old_home, unrelated_memories),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        sandbox_policy = connection.execute(
            "SELECT sandbox_policy FROM threads WHERE id = 'thread-1'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert prepared == bob_home
    assert '{"type":"workspace-write","writable_roots":["%s/memories","%s"]}' % (
        bob_home,
        unrelated_memories,
    ) == sandbox_policy


def test_prepare_bob_codex_home_rewrites_sandbox_policy_when_only_sandbox_policy_is_stale(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    old_home = "/private/tmp/personal-slack-agent/custom-bob-home"
    state_db = bob_home / "state_5.sqlite"
    state_db.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                agent_nickname TEXT,
                agent_role TEXT,
                memory_mode TEXT NOT NULL DEFAULT 'enabled',
                model TEXT,
                reasoning_effort TEXT,
                agent_path TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-1",
                str(bob_home) + "/sessions/2026/04/12/example.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories"]}' % old_home,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        sandbox_policy = connection.execute(
            "SELECT sandbox_policy FROM threads WHERE id = 'thread-1'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert prepared == bob_home
    assert sandbox_policy == (
        '{"type":"workspace-write","writable_roots":["%s/memories"]}' % bob_home
    )


def test_prepare_bob_codex_home_does_not_rewrite_session_transcript_text(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    old_home = "/private/tmp/personal-slack-agent/custom-bob-home"
    session_file = bob_home / "sessions" / "2026" / "04" / "12" / "rollout.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    original_line = (
        '{"type":"message","payload":{"text":"old path %s should remain as transcript text"}}\n'
        % old_home
    )
    session_file.write_text(original_line, encoding="utf-8")

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    assert prepared == bob_home
    assert session_file.read_text(encoding="utf-8") == original_line


def test_prepare_bob_codex_home_handles_mixed_tmp_aliases_without_corrupting_paths(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    old_private_home = "/private/tmp/personal-slack-agent/custom-bob-home"
    old_tmp_home = "/tmp/personal-slack-agent/custom-bob-home"

    shell_snapshot = bob_home / "shell_snapshots" / "snapshot.sh"
    shell_snapshot.parent.mkdir(parents=True, exist_ok=True)
    shell_snapshot.write_text(
        "export CODEX_HOME={0}\n".format(old_tmp_home),
        encoding="utf-8",
    )
    history = bob_home / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(
        '{"rollout_path":"%s/sessions/2026/04/12/example.jsonl"}\n' % old_tmp_home,
        encoding="utf-8",
    )

    state_db = bob_home / "state_5.sqlite"
    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                agent_nickname TEXT,
                agent_role TEXT,
                memory_mode TEXT NOT NULL DEFAULT 'enabled',
                model TEXT,
                reasoning_effort TEXT,
                agent_path TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-1",
                old_private_home + "/sessions/2026/04/12/example.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories"]}'
                % old_private_home,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        rollout_path, sandbox_policy = connection.execute(
            "SELECT rollout_path, sandbox_policy FROM threads WHERE id = 'thread-1'"
        ).fetchone()
    finally:
        connection.close()

    assert prepared == bob_home
    assert rollout_path == str(bob_home) + "/sessions/2026/04/12/example.jsonl"
    assert sandbox_policy == (
        '{"type":"workspace-write","writable_roots":["%s/memories"]}' % bob_home
    )
    assert json.loads(history.read_text(encoding="utf-8")) == {
        "rollout_path": str(bob_home) + "/sessions/2026/04/12/example.jsonl"
    }
    assert shell_snapshot.read_text(encoding="utf-8") == (
        "export CODEX_HOME={0}\n".format(bob_home)
    )


def test_prepare_bob_codex_home_does_not_double_rewrite_current_paths_on_rename(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    old_home = str(tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home")
    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home-v2"

    shell_snapshot = bob_home / "shell_snapshots" / "snapshot.sh"
    shell_snapshot.parent.mkdir(parents=True, exist_ok=True)
    shell_snapshot.write_text(
        "export CODEX_HOME={0}\n".format(str(bob_home)),
        encoding="utf-8",
    )
    history = bob_home / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(
        '{"rollout_path":"%s/sessions/2026/04/12/current.jsonl"}\n' % bob_home,
        encoding="utf-8",
    )

    state_db = bob_home / "state_5.sqlite"
    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                agent_nickname TEXT,
                agent_role TEXT,
                memory_mode TEXT NOT NULL DEFAULT 'enabled',
                model TEXT,
                reasoning_effort TEXT,
                agent_path TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-1",
                old_home + "/sessions/2026/04/12/stale.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories"]}' % old_home,
            ),
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-2",
                str(bob_home) + "/sessions/2026/04/12/current.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories"]}' % bob_home,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        stale_path, current_path = connection.execute(
            "SELECT rollout_path FROM threads ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    assert prepared == bob_home
    assert stale_path[0] == str(bob_home) + "/sessions/2026/04/12/stale.jsonl"
    assert current_path[0] == str(bob_home) + "/sessions/2026/04/12/current.jsonl"
    assert json.loads(history.read_text(encoding="utf-8")) == {
        "rollout_path": str(bob_home) + "/sessions/2026/04/12/current.jsonl"
    }
    assert shell_snapshot.read_text(encoding="utf-8") == (
        "export CODEX_HOME={0}\n".format(bob_home)
    )


def test_prepare_bob_codex_home_rewrites_renamed_target_when_only_sandbox_policy_is_stale(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    old_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home-v2"
    state_db = bob_home / "state_5.sqlite"
    state_db.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                agent_nickname TEXT,
                agent_role TEXT,
                memory_mode TEXT NOT NULL DEFAULT 'enabled',
                model TEXT,
                reasoning_effort TEXT,
                agent_path TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-1",
                str(bob_home) + "/sessions/2026/04/12/current.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories"]}' % old_home,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        sandbox_policy = connection.execute(
            "SELECT sandbox_policy FROM threads WHERE id = 'thread-1'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert prepared == bob_home
    assert sandbox_policy == (
        '{"type":"workspace-write","writable_roots":["%s/memories"]}' % bob_home
    )


def test_prepare_bob_codex_home_does_not_rewrite_same_parent_sibling_memories_root(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    old_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home-v2"
    sibling_home = tmp_path / "workspace" / "personal-slack-agent" / "shared-cache"
    state_db = bob_home / "state_5.sqlite"
    state_db.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                agent_nickname TEXT,
                agent_role TEXT,
                memory_mode TEXT NOT NULL DEFAULT 'enabled',
                model TEXT,
                reasoning_effort TEXT,
                agent_path TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-1",
                str(bob_home) + "/sessions/2026/04/12/current.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories","%s/memories"]}'
                % (old_home, sibling_home),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        sandbox_policy = connection.execute(
            "SELECT sandbox_policy FROM threads WHERE id = 'thread-1'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert prepared == bob_home
    assert sandbox_policy == (
        '{"type":"workspace-write","writable_roots":["%s/memories","%s/memories"]}'
        % (bob_home, sibling_home)
    )


def test_prepare_bob_codex_home_does_not_rewrite_same_basename_different_parent_memories_root(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    old_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home-v2"
    unrelated_same_basename = tmp_path / "other-root" / "custom-bob-home"
    state_db = bob_home / "state_5.sqlite"
    state_db.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                agent_nickname TEXT,
                agent_role TEXT,
                memory_mode TEXT NOT NULL DEFAULT 'enabled',
                model TEXT,
                reasoning_effort TEXT,
                agent_path TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-1",
                str(bob_home) + "/sessions/2026/04/12/current.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories","%s/memories"]}'
                % (old_home, unrelated_same_basename),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        sandbox_policy = connection.execute(
            "SELECT sandbox_policy FROM threads WHERE id = 'thread-1'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert prepared == bob_home
    assert sandbox_policy == (
        '{"type":"workspace-write","writable_roots":["%s/memories","%s/memories"]}'
        % (bob_home, unrelated_same_basename)
    )


def test_prepare_bob_codex_home_does_not_rewrite_same_parent_prefix_sibling_memories_root(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    old_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home"
    bob_home = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home-v2"
    prefix_sibling = tmp_path / "workspace" / "personal-slack-agent" / "custom-bob-home-archive"
    state_db = bob_home / "state_5.sqlite"
    state_db.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(state_db)
    try:
        connection.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                source TEXT NOT NULL,
                model_provider TEXT NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                sandbox_policy TEXT NOT NULL,
                approval_mode TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                has_user_event INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0,
                archived_at INTEGER,
                git_sha TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                cli_version TEXT NOT NULL DEFAULT '',
                first_user_message TEXT NOT NULL DEFAULT '',
                agent_nickname TEXT,
                agent_role TEXT,
                memory_mode TEXT NOT NULL DEFAULT 'enabled',
                model TEXT,
                reasoning_effort TEXT,
                agent_path TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO threads (
                id, rollout_path, created_at, updated_at, source, model_provider, cwd,
                title, sandbox_policy, approval_mode
            ) VALUES (?, ?, 0, 0, 'exec', 'oca', ?, 'title', ?, 'never')
            """,
            (
                "thread-1",
                str(bob_home) + "/sessions/2026/04/12/current.jsonl",
                str(tmp_path / "repo"),
                '{"type":"workspace-write","writable_roots":["%s/memories","%s/memories"]}'
                % (old_home, prefix_sibling),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    prepared = agent_module._prepare_bob_codex_home(bob_home)

    connection = sqlite3.connect(state_db)
    try:
        sandbox_policy = connection.execute(
            "SELECT sandbox_policy FROM threads WHERE id = 'thread-1'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert prepared == bob_home
    assert sandbox_policy == (
        '{"type":"workspace-write","writable_roots":["%s/memories","%s/memories"]}'
        % (bob_home, prefix_sibling)
    )


def test_run_once_uses_configured_bob_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / "bob.toml"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    configured_bob_home = tmp_path / "custom-bob-codex-home"
    config_file.write_text(
        "\n".join(
            [
                "[defaults]",
                'default_cwd = "{0}"'.format(workspace_root),
                'allowed_actor_ids = ["U123"]',
                'browser_mode = "shared_browser"',
                'bob_codex_home = "{0}"'.format(configured_bob_home),
                "",
                "[[workspaces]]",
                'name = "oracle"',
                'allowed_actor_ids = ["U123"]',
                'slack_url = "https://app.slack.com/client/T12345678/C12345678"',
                "",
                "[[workspaces.channels]]",
                'name = "yifanche-private"',
                'persistent_memory_mode = "owner_only"',
                'persistent_memory_owner = "yifanche"',
            ]
        ),
        encoding="utf-8",
    )

    calls = {"runner_kwargs": None}

    class FakeBrowser:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_workspace_urls(self, workspace_urls):
            return None

        def set_workspace_api_contexts(self, workspace_api_contexts):
            return None

        def set_channel_urls(self, channel_urls):
            return None

        def close(self):
            return None

    class FakeRunner:
        def __init__(self, **kwargs):
            calls["runner_kwargs"] = kwargs

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def process_scheduled_actions(self):
            return None

    class FakeWatcher:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_cycle(self):
            return None

    monkeypatch.setattr(agent_module, "PlaywrightSlackAdapter", FakeBrowser)
    monkeypatch.setattr(agent_module, "SubprocessCodexRunner", FakeRunner)
    monkeypatch.setattr(agent_module, "BobOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(agent_module, "SlackWatcher", FakeWatcher)

    exit_code = run_once(config_file)

    assert exit_code == 0
    assert calls["runner_kwargs"]["env_overrides"]["CODEX_HOME"] == str(
        configured_bob_home.resolve()
    )


def test_run_once_seeds_explicit_channel_urls_when_channel_ids_are_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / "bob.toml"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config_file.write_text(
        "\n".join(
            [
                "[defaults]",
                'default_cwd = "{0}"'.format(workspace_root),
                'allowed_actor_ids = ["U123"]',
                'browser_mode = "shared_browser"',
                "",
                "[[workspaces]]",
                'name = "oracle"',
                'allowed_actor_ids = ["U123"]',
                'slack_url = "https://app.slack.com/client/E655JKQRX/C040C3N43B8"',
                "",
                "[[workspaces.channels]]",
                'name = "yifanche-private"',
                'persistent_memory_mode = "owner_only"',
                'persistent_memory_owner = "yifanche"',
                "",
                "[[workspaces.channels]]",
                'name = "yifanche-bob-test"',
                'persistent_memory_mode = "disabled"',
                'slack_channel_id = "C0AS82WLCBU"',
            ]
        ),
        encoding="utf-8",
    )

    calls = {"channel_urls": None}

    class FakeBrowser:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def set_workspace_urls(self, workspace_urls):
            return None

        def set_workspace_api_contexts(self, workspace_api_contexts):
            return None

        def set_channel_urls(self, channel_urls):
            calls["channel_urls"] = dict(channel_urls)

        def close(self):
            return None

    class FakeRunner:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def process_scheduled_actions(self):
            return None

    class FakeWatcher:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_cycle(self):
            return None

    monkeypatch.setattr(agent_module, "PlaywrightSlackAdapter", FakeBrowser)
    monkeypatch.setattr(agent_module, "SubprocessCodexRunner", FakeRunner)
    monkeypatch.setattr(agent_module, "BobOrchestrator", FakeOrchestrator)
    monkeypatch.setattr(agent_module, "SlackWatcher", FakeWatcher)

    exit_code = run_once(config_file)

    assert exit_code == 0
    assert calls["channel_urls"] == {
        ("oracle", "yifanche-bob-test"): "https://app.slack.com/client/E655JKQRX/C0AS82WLCBU"
    }


def test_agent_parser_exposes_poll_interval_flag_with_default():
    args = agent_module.build_parser().parse_args([])
    assert args.poll_interval_seconds == 30.0

    overridden = agent_module.build_parser().parse_args(["--poll-interval-seconds", "5"])
    assert overridden.poll_interval_seconds == 5.0


def test_agent_parser_uses_environment_default_poll_interval(monkeypatch):
    monkeypatch.setenv("BOB_POLL_INTERVAL_SECONDS", "11")
    args = agent_module.build_parser().parse_args([])
    assert args.poll_interval_seconds == 11.0


def test_run_poll_loop_repeats_until_interrupted(tmp_path):
    calls = {"cycle": 0, "sleep": []}
    stop_request_path = tmp_path / "bob.stop"
    lock_file = tmp_path / "bob.lock"
    pid_file = tmp_path / "bob.pid"

    class FakeWatcher:
        def run_cycle(self):
            calls["cycle"] += 1
            if calls["cycle"] == 3:
                raise KeyboardInterrupt()

    class FakeOrchestrator:
        def process_scheduled_actions(self):
            return None

    agent_module.run_poll_loop(
        watcher=FakeWatcher(),
        orchestrator=FakeOrchestrator(),
        poll_interval_seconds=7.5,
        lock_file=lock_file,
        pid_file=pid_file,
        stop_request_path=stop_request_path,
        sleep_fn=calls["sleep"].append,
    )

    assert calls["cycle"] == 3
    assert calls["sleep"]
    assert all(duration <= 1.0 for duration in calls["sleep"])


def test_run_poll_cycle_processes_scheduled_actions_after_watcher():
    calls = []

    class FakeWatcher:
        def run_cycle(self):
            calls.append("watcher")

    class FakeOrchestrator:
        def process_scheduled_actions(self):
            calls.append("scheduled")

    agent_module.run_poll_cycle(
        watcher=FakeWatcher(),
        orchestrator=FakeOrchestrator(),
    )

    assert calls == ["watcher", "scheduled"]


def test_run_poll_cycle_consumes_reconcile_requests(tmp_path):
    calls = []
    reconcile_file = tmp_path / "bob.reconcile"
    reconcile_file.write_text("oracle\n", encoding="utf-8")

    class FakeWatcher:
        def request_workspace_reconcile(self, workspace_name):
            calls.append(("reconcile", workspace_name))

        def run_cycle(self):
            calls.append(("watcher", None))

    class FakeOrchestrator:
        def process_scheduled_actions(self):
            calls.append(("scheduled", None))

    agent_module.run_poll_cycle(
        watcher=FakeWatcher(),
        orchestrator=FakeOrchestrator(),
        reconcile_request_path=reconcile_file,
    )

    assert calls == [("reconcile", "oracle"), ("watcher", None), ("scheduled", None)]
    assert not reconcile_file.exists()


def test_run_poll_loop_stops_when_stop_request_file_exists(tmp_path):
    calls = {"cycle": 0, "sleep": []}
    stop_request_path = tmp_path / "bob.stop"
    lock_file = tmp_path / "bob.lock"
    pid_file = tmp_path / "bob.pid"

    class FakeWatcher:
        def run_cycle(self):
            calls["cycle"] += 1
            stop_request_path.write_text("stop\n", encoding="utf-8")

    class FakeOrchestrator:
        def process_scheduled_actions(self):
            return None

    agent_module.run_poll_loop(
        watcher=FakeWatcher(),
        orchestrator=FakeOrchestrator(),
        poll_interval_seconds=7.5,
        lock_file=lock_file,
        pid_file=pid_file,
        stop_request_path=stop_request_path,
        sleep_fn=calls["sleep"].append,
    )

    assert calls["cycle"] == 1
    assert lock_file.exists()
    assert pid_file.exists()


def test_run_poll_loop_continues_after_non_interrupt_cycle_error(tmp_path):
    calls = {"cycle": 0, "sleep": []}
    stop_request_path = tmp_path / "bob.stop"
    lock_file = tmp_path / "bob.lock"
    pid_file = tmp_path / "bob.pid"

    class FakeLogger:
        def __init__(self):
            self.exception_messages = []

        def exception(self, message, *args):
            if args:
                self.exception_messages.append(message % args)
                return
            self.exception_messages.append(message)

    logger = FakeLogger()

    class FakeWatcher:
        def run_cycle(self):
            calls["cycle"] += 1
            if calls["cycle"] == 1:
                raise RuntimeError("transient failure")
            if calls["cycle"] == 3:
                raise KeyboardInterrupt()

    class FakeOrchestrator:
        def process_scheduled_actions(self):
            return None

    agent_module.run_poll_loop(
        watcher=FakeWatcher(),
        orchestrator=FakeOrchestrator(),
        poll_interval_seconds=7.5,
        lock_file=lock_file,
        pid_file=pid_file,
        stop_request_path=stop_request_path,
        sleep_fn=calls["sleep"].append,
        logger=logger,
    )

    assert calls["cycle"] == 3
    assert logger.exception_messages == [
        "bob-agent poll cycle failed; continuing after 7.500s"
    ]
