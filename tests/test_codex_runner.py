import subprocess

from personal_slack_agent.codex_runner import (
    SubprocessCodexRunner,
    build_new_session_command,
    build_resume_command,
    parse_jsonl_events,
)


def test_build_new_session_command_includes_json_and_roots():
    command = build_new_session_command(
        prompt="Bob, hi there",
        cwd="/Users/bob_owner_handle/Code/OHAI/ctdm",
        additional_roots=["/Users/bob_owner_handle/Code", "/tmp/work"],
    )

    assert command[:4] == ["codex", "exec", "--json", "--skip-git-repo-check"]
    assert command[-1] == "-"
    assert "Bob, hi there" not in command
    assert command.count("--add-dir") == 2
    assert "--cd" in command
    assert command[command.index("--cd") + 1] == "/Users/bob_owner_handle/Code/OHAI/ctdm"
    assert "--sandbox" not in command


def test_build_resume_session_command_includes_session_and_prompt():
    command = build_resume_command(
        session_id="session-123",
        prompt="Continue with the fix",
    )

    assert command == [
        "codex",
        "exec",
        "resume",
        "--json",
        "--skip-git-repo-check",
        "session-123",
        "-",
    ]


def test_build_commands_include_explicit_sandbox_mode_when_requested():
    new_command = build_new_session_command(
        prompt="Bob, hi there",
        cwd="/Users/bob_owner_handle/Code/OHAI/ctdm",
        additional_roots=[],
        sandbox_mode="danger-full-access",
    )
    resume_command = build_resume_command(
        session_id="session-123",
        prompt="Continue with the fix",
        sandbox_mode="danger-full-access",
    )

    assert "--sandbox" in new_command
    assert new_command[new_command.index("--sandbox") + 1] == "danger-full-access"
    assert resume_command == [
        "codex",
        "exec",
        "resume",
        "--json",
        "--skip-git-repo-check",
        "-c",
        'sandbox_mode="danger-full-access"',
        "session-123",
        "-",
    ]


def test_build_commands_include_workspace_write_writable_roots_override_when_requested():
    roots = ["/Users/bob_owner_handle/workspace", "/Users/bob_owner_handle/scratch", "/tmp"]

    new_command = build_new_session_command(
        prompt="Bob, hi there",
        cwd="/Users/bob_owner_handle/Code/OHAI/ctdm",
        additional_roots=[],
        sandbox_mode="workspace-write",
        workspace_write_writable_roots=roots,
    )
    resume_command = build_resume_command(
        session_id="session-123",
        prompt="Continue with the fix",
        sandbox_mode="workspace-write",
        workspace_write_writable_roots=roots,
    )

    assert new_command == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-c",
        'sandbox_workspace_write.writable_roots=["/Users/bob_owner_handle/workspace", "/Users/bob_owner_handle/scratch", "/tmp"]',
        "--cd",
        "/Users/bob_owner_handle/Code/OHAI/ctdm",
        "-",
    ]
    assert resume_command == [
        "codex",
        "exec",
        "resume",
        "--json",
        "--skip-git-repo-check",
        "-c",
        'sandbox_mode="workspace-write"',
        "-c",
        'sandbox_workspace_write.writable_roots=["/Users/bob_owner_handle/workspace", "/Users/bob_owner_handle/scratch", "/tmp"]',
        "session-123",
        "-",
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

    def fake_exec(command, _cwd=None, input_text=None):
        calls.append((command, input_text))
        return '\n'.join(
            [
                '{"type":"session_meta","payload":{"id":"session-123"}}',
                '{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Final answer"}],"phase":"final"}}',
            ]
        )

    runner = SubprocessCodexRunner(exec_command=fake_exec)

    result = runner.run_new_session(
        prompt="Bob, hi there",
        cwd="/Users/bob_owner_handle/Code/OHAI/ctdm",
        additional_roots=["/Users/bob_owner_handle/Code"],
    )

    assert calls == [
        (
            [
                "codex",
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--cd",
                "/Users/bob_owner_handle/Code/OHAI/ctdm",
                "--add-dir",
                "/Users/bob_owner_handle/Code",
                "-",
            ],
            "Bob, hi there",
        )
    ]
    assert result.session_id == "session-123"
    assert result.final_output == "Final answer"


def test_subprocess_codex_runner_executes_resume_and_parses_result():
    calls = []

    def fake_exec(command, _cwd=None, input_text=None):
        calls.append((command, input_text))
        return '\n'.join(
            [
                '{"type":"session_meta","payload":{"id":"session-123"}}',
                '{"type":"event_msg","payload":{"wait_kind":"input","wait_message":"Need more detail"}}',
            ]
        )

    runner = SubprocessCodexRunner(exec_command=fake_exec)

    result = runner.resume_session("session-123", "continue", "/tmp/project")

    assert calls == [
        (
            [
                "codex",
                "exec",
                "resume",
                "--json",
                "--skip-git-repo-check",
                "session-123",
                "-",
            ],
            "continue",
        )
    ]
    assert result.session_id == "session-123"
    assert result.wait_kind == "input"
    assert result.wait_message == "Need more detail"


def test_subprocess_codex_runner_returns_failure_text_for_nonzero_exit_without_json():
    def fake_exec(_command, _cwd=None, input_text=None):
        raise RuntimeError("Not inside a trusted directory and --skip-git-repo-check was not specified.")

    runner = SubprocessCodexRunner(exec_command=fake_exec)

    result = runner.resume_session("session-123", "continue", "/tmp/project")

    assert result.failure_text == "Not inside a trusted directory and --skip-git-repo-check was not specified."


def test_default_exec_command_runs_subprocess_from_requested_cwd(monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text, cwd, env, timeout, input):
        calls.append(
            {
                "command": command,
                "check": check,
                "capture_output": capture_output,
                "text": text,
                "cwd": cwd,
                "env": env,
                "timeout": timeout,
                "input": input,
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
                "--json",
                "--skip-git-repo-check",
                "session-123",
                "-",
            ],
            "check": False,
            "capture_output": True,
            "text": True,
            "cwd": "/tmp/project",
            "env": None,
            "timeout": 600.0,
            "input": "continue",
        }
    ]


def test_default_exec_command_merges_env_overrides(monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text, cwd, env, timeout, input):
        calls.append(
            {
                "command": command,
                "check": check,
                "capture_output": capture_output,
                "text": text,
                "cwd": cwd,
                "env": env,
                "timeout": timeout,
                "input": input,
            }
        )

        class CompletedProcess:
            returncode = 0
            stdout = '{"type":"session_meta","payload":{"id":"session-123"}}\n'
            stderr = ""

        return CompletedProcess()

    monkeypatch.setattr("personal_slack_agent.codex_runner.subprocess.run", fake_run)
    runner = SubprocessCodexRunner(env_overrides={"CODEX_HOME": "/tmp/bob-codex-home"})

    result = runner.resume_session("session-123", "continue", "/tmp/project")

    assert result.session_id == "session-123"
    assert calls[0]["env"]["CODEX_HOME"] == "/tmp/bob-codex-home"
    assert calls[0]["command"] == [
        "codex",
        "exec",
        "resume",
        "--json",
        "--skip-git-repo-check",
        "session-123",
        "-",
    ]
    assert calls[0]["input"] == "continue"


def test_subprocess_codex_runner_uses_configured_sandbox_mode(monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text, cwd, env, timeout, input):
        calls.append(command)

        class CompletedProcess:
            returncode = 0
            stdout = '{"type":"session_meta","payload":{"id":"session-123"}}\n'
            stderr = ""

        return CompletedProcess()

    monkeypatch.setattr("personal_slack_agent.codex_runner.subprocess.run", fake_run)
    runner = SubprocessCodexRunner(sandbox_mode="danger-full-access")

    result = runner.resume_session("session-123", "continue", "/tmp/project")

    assert result.session_id == "session-123"
    assert calls == [[
        "codex",
        "exec",
        "resume",
        "--json",
        "--skip-git-repo-check",
        "-c",
        'sandbox_mode="danger-full-access"',
        "session-123",
        "-",
    ]]


def test_subprocess_codex_runner_includes_workspace_write_writable_roots(monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text, cwd, env, timeout, input):
        calls.append(command)

        class CompletedProcess:
            returncode = 0
            stdout = '{"type":"session_meta","payload":{"id":"session-123"}}\n'
            stderr = ""

        return CompletedProcess()

    monkeypatch.setattr("personal_slack_agent.codex_runner.subprocess.run", fake_run)
    runner = SubprocessCodexRunner(sandbox_mode="workspace-write")

    result = runner.resume_session(
        "session-123",
        "continue",
        "/tmp/project",
        workspace_write_writable_roots=[
            "/Users/bob_owner_handle/workspace",
            "/Users/bob_owner_handle/scratch",
            "/tmp",
        ],
    )

    assert result.session_id == "session-123"
    assert calls == [[
        "codex",
        "exec",
        "resume",
        "--json",
        "--skip-git-repo-check",
        "-c",
        'sandbox_mode="workspace-write"',
        "-c",
        'sandbox_workspace_write.writable_roots=["/Users/bob_owner_handle/workspace", "/Users/bob_owner_handle/scratch", "/tmp"]',
        "session-123",
        "-",
    ]]


def test_streaming_exec_command_sends_prompt_over_stdin(monkeypatch):
    calls = []
    stdin_writes = []

    class FakeStdin:
        def write(self, value):
            stdin_writes.append(value)

        def close(self):
            stdin_writes.append("<closed>")

    class FakeStdout:
        def __iter__(self):
            return iter(
                [
                    '{"type":"thread.started","thread_id":"session-123"}\n',
                    '{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Final answer"}],"phase":"final"}}\n',
                ]
            )

    class FakeStderr:
        def read(self):
            return ""

    class FakeProcess:
        stdout = FakeStdout()
        stderr = FakeStderr()
        stdin = FakeStdin()

        def wait(self):
            return 0

        def kill(self):
            raise AssertionError("process should not be killed")

    def fake_popen(command, text, stdout, stderr, stdin, cwd, env):
        calls.append(
            {
                "command": command,
                "text": text,
                "stdout": stdout,
                "stderr": stderr,
                "stdin": stdin,
                "cwd": cwd,
                "env": env,
            }
        )
        return FakeProcess()

    monkeypatch.setattr("personal_slack_agent.codex_runner.subprocess.Popen", fake_popen)
    runner = SubprocessCodexRunner(env_overrides={"CODEX_HOME": "/tmp/bob-codex-home"})

    result = runner.run_new_session(
        prompt="Bob, hi there",
        cwd="/tmp/project",
        additional_roots=[],
        on_session_started=lambda _session_id: None,
    )

    assert result.session_id == "session-123"
    assert result.final_output == "Final answer"
    assert calls[0]["command"][-1] == "-"
    assert calls[0]["stdin"] == subprocess.PIPE
    assert calls[0]["env"]["CODEX_HOME"] == "/tmp/bob-codex-home"
    assert stdin_writes == ["Bob, hi there", "<closed>"]


def test_streaming_exec_command_starts_timeout_before_writing_stdin(monkeypatch):
    events = []

    class FakeTimer:
        daemon = False

        def __init__(self, interval, function):
            self.interval = interval
            self.function = function

        def start(self):
            events.append("timer_start")

        def cancel(self):
            events.append("timer_cancel")

    class FakeStdin:
        def write(self, value):
            del value
            events.append("stdin_write")

        def close(self):
            events.append("stdin_close")

    class FakeStdout:
        def __iter__(self):
            return iter([])

    class FakeStderr:
        def read(self):
            return ""

    class FakeProcess:
        stdout = FakeStdout()
        stderr = FakeStderr()
        stdin = FakeStdin()

        def wait(self):
            return 0

        def kill(self):
            raise AssertionError("process should not be killed")

    def fake_popen(command, text, stdout, stderr, stdin, cwd, env):
        del command
        del text
        del stdout
        del stderr
        del stdin
        del cwd
        del env
        return FakeProcess()

    monkeypatch.setattr("personal_slack_agent.codex_runner.subprocess.Popen", fake_popen)
    monkeypatch.setattr("personal_slack_agent.codex_runner.threading.Timer", FakeTimer)
    runner = SubprocessCodexRunner(exec_timeout_seconds=60)

    runner.run_new_session(
        prompt="large prompt",
        cwd="/tmp/project",
        additional_roots=[],
        on_session_started=lambda _session_id: None,
    )

    assert events[:3] == ["timer_start", "stdin_write", "stdin_close"]


def test_streaming_exec_command_drains_child_output_after_broken_stdin_pipe(monkeypatch):
    stdin_events = []

    class FakeStdin:
        def write(self, value):
            del value
            stdin_events.append("write")
            raise BrokenPipeError("child closed stdin")

        def close(self):
            stdin_events.append("close")

    class FakeStdout:
        def __iter__(self):
            return iter([])

    class FakeStderr:
        def read(self):
            return "codex exited before reading stdin"

    class FakeProcess:
        stdout = FakeStdout()
        stderr = FakeStderr()
        stdin = FakeStdin()

        def wait(self):
            return 70

        def kill(self):
            raise AssertionError("process should not be killed")

    def fake_popen(command, text, stdout, stderr, stdin, cwd, env):
        del command
        del text
        del stdout
        del stderr
        del stdin
        del cwd
        del env
        return FakeProcess()

    monkeypatch.setattr("personal_slack_agent.codex_runner.subprocess.Popen", fake_popen)
    runner = SubprocessCodexRunner()

    result = runner.run_new_session(
        prompt="large prompt",
        cwd="/tmp/project",
        additional_roots=[],
        on_session_started=lambda _session_id: None,
    )

    assert result.failure_text == "codex exited before reading stdin"
    assert stdin_events == ["write", "close"]


def test_default_exec_command_returns_timeout_failure_text(monkeypatch):
    def fake_run(command, check, capture_output, text, cwd, env, timeout, input):
        raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)

    monkeypatch.setattr("personal_slack_agent.codex_runner.subprocess.run", fake_run)
    runner = SubprocessCodexRunner(exec_timeout_seconds=42)

    result = runner.resume_session("session-123", "continue", "/tmp/project")

    assert result.failure_text == "codex exec timed out after 42s"


def test_default_exec_command_returns_exit_code_when_process_has_no_output(monkeypatch):
    def fake_run(command, check, capture_output, text, cwd, env, timeout, input):
        class CompletedProcess:
            returncode = 70
            stdout = ""
            stderr = ""

        return CompletedProcess()

    monkeypatch.setattr("personal_slack_agent.codex_runner.subprocess.run", fake_run)
    runner = SubprocessCodexRunner()

    result = runner.resume_session("session-123", "continue", "/tmp/project")

    assert result.failure_text == "codex exec failed with exit code 70"
