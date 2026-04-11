from personal_slack_agent.codex_runner import (
    SubprocessCodexRunner,
    build_new_session_command,
    build_resume_command,
    parse_jsonl_events,
)


def test_build_new_session_command_includes_json_and_roots():
    command = build_new_session_command(
        prompt="Bob, hi there",
        cwd="/Users/yifanche/Code/OHAI/ctdm",
        additional_roots=["/Users/yifanche/Code", "/tmp/work"],
    )

    assert command[:4] == ["codex", "exec", "--json", "--skip-git-repo-check"]
    assert command[-1] == "Bob, hi there"
    assert command.count("--add-dir") == 2
    assert "--cd" in command
    assert command[command.index("--cd") + 1] == "/Users/yifanche/Code/OHAI/ctdm"


def test_build_resume_session_command_includes_session_and_prompt():
    command = build_resume_command(
        session_id="session-123",
        prompt="Continue with the fix",
    )

    assert command == [
        "codex",
        "exec",
        "resume",
        "session-123",
        "--json",
        "--skip-git-repo-check",
        "Continue with the fix",
    ]


def test_parse_jsonl_events_extracts_final_message():
    payload = """
{"type":"session_meta","payload":{"id":"session-123"}}
{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Final answer"}],"phase":"final"}}
""".strip()

    result = parse_jsonl_events(payload.splitlines())

    assert result.session_id == "session-123"
    assert result.final_output == "Final answer"
    assert result.wait_kind is None
    assert result.failure_text is None


def test_parse_jsonl_events_keeps_first_final_response_item_when_multiple_exist():
    payload = """
{"type":"session_meta","payload":{"id":"session-123"}}
{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Bob is fine."}],"phase":"final"}}
{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"There is nothing queued to resume."}],"phase":"final"}}
""".strip()

    result = parse_jsonl_events(payload.splitlines())

    assert result.session_id == "session-123"
    assert result.final_output == "Bob is fine."


def test_parse_jsonl_events_extracts_normalized_wait_and_failure():
    payload = """
{"type":"session_meta","payload":{"id":"session-123"}}
{"type":"event_msg","payload":{"wait_kind":"approval","wait_message":"Approve command?"}}
{"type":"event_msg","payload":{"failure_text":"Command failed with exit code 1"}}
""".strip()

    result = parse_jsonl_events(payload.splitlines())

    assert result.session_id == "session-123"
    assert result.wait_kind == "approval"
    assert result.wait_message == "Approve command?"
    assert result.failure_text == "Command failed with exit code 1"


def test_parse_jsonl_events_supports_current_thread_started_and_agent_message_format():
    payload = """
{"type":"thread.started","thread_id":"session-456"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Intermediate note"}}
{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"Final answer from current format"}}
{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}
""".strip()

    result = parse_jsonl_events(payload.splitlines())

    assert result.session_id == "session-456"
    assert result.final_output == "Final answer from current format"


def test_parse_jsonl_events_prefers_response_item_final_over_item_completed_messages():
    payload = """
{"type":"thread.started","thread_id":"session-456"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Intermediate note"}}
{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Correct final answer"}],"phase":"final"}}
{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":"Later unrelated artifact"}}
""".strip()

    result = parse_jsonl_events(payload.splitlines())

    assert result.session_id == "session-456"
    assert result.final_output == "Correct final answer"


def test_subprocess_codex_runner_executes_new_session_and_parses_result():
    calls = []

    def fake_exec(command, _cwd=None):
        calls.append(command)
        return '\n'.join(
            [
                '{"type":"session_meta","payload":{"id":"session-123"}}',
                '{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Final answer"}],"phase":"final"}}',
            ]
        )

    runner = SubprocessCodexRunner(exec_command=fake_exec)

    result = runner.run_new_session(
        prompt="Bob, hi there",
        cwd="/Users/yifanche/Code/OHAI/ctdm",
        additional_roots=["/Users/yifanche/Code"],
    )

    assert calls == [[
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--cd",
        "/Users/yifanche/Code/OHAI/ctdm",
        "--add-dir",
        "/Users/yifanche/Code",
        "Bob, hi there",
    ]]
    assert result.session_id == "session-123"
    assert result.final_output == "Final answer"


def test_subprocess_codex_runner_executes_resume_and_parses_result():
    calls = []

    def fake_exec(command, _cwd=None):
        calls.append(command)
        return '\n'.join(
            [
                '{"type":"session_meta","payload":{"id":"session-123"}}',
                '{"type":"event_msg","payload":{"wait_kind":"input","wait_message":"Need more detail"}}',
            ]
        )

    runner = SubprocessCodexRunner(exec_command=fake_exec)

    result = runner.resume_session("session-123", "continue", "/tmp/project")

    assert calls == [[
        "codex",
        "exec",
        "resume",
        "session-123",
        "--json",
        "--skip-git-repo-check",
        "continue",
    ]]
    assert result.session_id == "session-123"
    assert result.wait_kind == "input"
    assert result.wait_message == "Need more detail"


def test_subprocess_codex_runner_returns_failure_text_for_nonzero_exit_without_json():
    def fake_exec(_command, _cwd=None):
        raise RuntimeError("Not inside a trusted directory and --skip-git-repo-check was not specified.")

    runner = SubprocessCodexRunner(exec_command=fake_exec)

    result = runner.resume_session("session-123", "continue", "/tmp/project")

    assert result.failure_text == "Not inside a trusted directory and --skip-git-repo-check was not specified."


def test_default_exec_command_runs_subprocess_from_requested_cwd(monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text, cwd):
        calls.append(
            {
                "command": command,
                "check": check,
                "capture_output": capture_output,
                "text": text,
                "cwd": cwd,
            }
        )

        class CompletedProcess:
            returncode = 0
            stdout = '{"type":"session_meta","payload":{"id":"session-123"}}\n'
            stderr = ""

        return CompletedProcess()

    monkeypatch.setattr("personal_slack_agent.codex_runner.subprocess.run", fake_run)
    runner = SubprocessCodexRunner()

    result = runner.resume_session("session-123", "continue", "/tmp/project")

    assert result.session_id == "session-123"
    assert calls == [
        {
            "command": [
                "codex",
                "exec",
                "resume",
                "session-123",
                "--json",
                "--skip-git-repo-check",
                "continue",
            ],
            "check": False,
            "capture_output": True,
            "text": True,
            "cwd": "/tmp/project",
        }
    ]
