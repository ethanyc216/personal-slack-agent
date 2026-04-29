import json
import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Iterable, List, Mapping, Optional


@dataclass
class CodexRunResult:
    session_id: Optional[str] = None
    final_output: Optional[str] = None
    wait_kind: Optional[str] = None
    wait_message: Optional[str] = None
    failure_text: Optional[str] = None


class SubprocessCodexRunner:
    def __init__(
        self,
        exec_command: Optional[Callable[[List[str], Optional[str], Optional[str]], str]] = None,
        env_overrides: Optional[Mapping[str, str]] = None,
        sandbox_mode: Optional[str] = None,
        exec_timeout_seconds: Optional[float] = 600.0,
    ) -> None:
        self._exec_command = exec_command or self._default_exec_command
        self._env_overrides = dict(env_overrides or {})
        self._sandbox_mode = sandbox_mode
        self._exec_timeout_seconds = exec_timeout_seconds

    def run_new_session(
        self,
        prompt: str,
        cwd: str,
        additional_roots: List[str],
        sandbox_mode: Optional[str] = None,
        workspace_write_writable_roots: Optional[List[str]] = None,
        on_session_started: Optional[Callable[[str], None]] = None,
    ) -> CodexRunResult:
        command = build_new_session_command(
            prompt=prompt,
            cwd=cwd,
            additional_roots=additional_roots,
            sandbox_mode=sandbox_mode or self._sandbox_mode,
            workspace_write_writable_roots=workspace_write_writable_roots,
        )
        return self._run_and_parse(
            command,
            cwd=cwd,
            input_text=prompt,
            on_session_started=on_session_started,
        )

    def resume_session(
        self,
        session_id: str,
        prompt: str,
        cwd: str,
        sandbox_mode: Optional[str] = None,
        workspace_write_writable_roots: Optional[List[str]] = None,
    ) -> CodexRunResult:
        command = build_resume_command(
            session_id=session_id,
            prompt=prompt,
            sandbox_mode=sandbox_mode or self._sandbox_mode,
            workspace_write_writable_roots=workspace_write_writable_roots,
        )
        return self._run_and_parse(command, cwd=cwd, input_text=prompt)

    def _default_exec_command(
        self,
        command: List[str],
        cwd: Optional[str] = None,
        input_text: Optional[str] = None,
    ) -> str:
        env = None
        if self._env_overrides:
            env = os.environ.copy()
            env.update(self._env_overrides)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env,
                timeout=self._exec_timeout_seconds,
                input=input_text,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(_timeout_failure_text(exc.timeout)) from exc
        output = completed.stdout or ""
        if completed.returncode == 0:
            return output

        stderr = (completed.stderr or "").strip()
        combined = "\n".join(part for part in [output.strip(), stderr] if part).strip()
        raise RuntimeError(combined or _exit_code_failure_text(completed.returncode))

    def _streaming_exec_command(
        self,
        command: List[str],
        cwd: Optional[str] = None,
        input_text: Optional[str] = None,
        on_session_started: Optional[Callable[[str], None]] = None,
    ) -> str:
        env = None
        if self._env_overrides:
            env = os.environ.copy()
            env.update(self._env_overrides)
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        assert process.stdout is not None
        stdout_lines: List[str] = []
        session_started_notified = False
        timed_out = False
        timer: Optional[threading.Timer] = None
        if self._exec_timeout_seconds is not None:
            def _kill_process() -> None:
                nonlocal timed_out
                timed_out = True
                try:
                    process.kill()
                except OSError:
                    return

            timer = threading.Timer(self._exec_timeout_seconds, _kill_process)
            timer.daemon = True
            timer.start()

        try:
            if process.stdin is not None:
                try:
                    if input_text is not None:
                        process.stdin.write(input_text)
                except BrokenPipeError:
                    pass
                finally:
                    try:
                        process.stdin.close()
                    except BrokenPipeError:
                        pass

            for line in process.stdout:
                stdout_lines.append(line)
                if session_started_notified or on_session_started is None:
                    continue
                session_id = _extract_session_started_id(line)
                if session_id is None:
                    continue
                on_session_started(session_id)
                session_started_notified = True

            stderr_output = process.stderr.read() if process.stderr is not None else ""
            returncode = process.wait()
        finally:
            if timer is not None:
                timer.cancel()

        if timed_out:
            raise RuntimeError(_timeout_failure_text(self._exec_timeout_seconds))

        output = "".join(stdout_lines)
        if returncode == 0:
            return output

        stderr = (stderr_output or "").strip()
        combined = "\n".join(part for part in [output.strip(), stderr] if part).strip()
        raise RuntimeError(combined or _exit_code_failure_text(returncode))

    def _run_and_parse(
        self,
        command: List[str],
        cwd: Optional[str] = None,
        input_text: Optional[str] = None,
        on_session_started: Optional[Callable[[str], None]] = None,
    ) -> CodexRunResult:
        try:
            if on_session_started is None:
                output = self._exec_command(command, cwd, input_text)
            else:
                output = self._streaming_exec_command(
                    command,
                    cwd=cwd,
                    input_text=input_text,
                    on_session_started=on_session_started,
                )
        except Exception as exc:
            return CodexRunResult(failure_text=str(exc).strip() or exc.__class__.__name__)
        return parse_jsonl_events(output.splitlines())


def build_new_session_command(
    prompt: str,
    cwd: str,
    additional_roots: List[str],
    sandbox_mode: Optional[str] = None,
    workspace_write_writable_roots: Optional[List[str]] = None,
) -> List[str]:
    command = ["codex", "exec", "--json", "--skip-git-repo-check"]
    if sandbox_mode:
        command.extend(["--sandbox", sandbox_mode])
    if workspace_write_writable_roots is not None:
        command.extend(
            [
                "-c",
                _array_config_override(
                    "sandbox_workspace_write.writable_roots",
                    workspace_write_writable_roots,
                ),
            ]
        )
    command.extend(["--cd", cwd])
    for root in additional_roots:
        command.extend(["--add-dir", root])
    command.append("-")
    return command


def build_resume_command(
    session_id: str,
    prompt: str,
    sandbox_mode: Optional[str] = None,
    workspace_write_writable_roots: Optional[List[str]] = None,
) -> List[str]:
    command = ["codex", "exec", "resume", "--json", "--skip-git-repo-check"]
    if sandbox_mode:
        command.extend(["-c", _string_config_override("sandbox_mode", sandbox_mode)])
    if workspace_write_writable_roots is not None:
        command.extend(
            [
                "-c",
                _array_config_override(
                    "sandbox_workspace_write.writable_roots",
                    workspace_write_writable_roots,
                ),
            ]
        )
    command.extend([session_id, "-"])
    return command


def parse_jsonl_events(lines: Iterable[str]) -> CodexRunResult:
    result = CodexRunResult()
    saw_response_item_final = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type")
        payload = event.get("payload")

        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str):
                result.session_id = thread_id
            continue

        if event_type == "session_meta" and isinstance(payload, dict):
            session_id = payload.get("id")
            if isinstance(session_id, str):
                result.session_id = session_id
            continue

        if event_type == "item.completed":
            item = event.get("item")
            if not saw_response_item_final and isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    result.final_output = text.strip()
            continue

        if event_type == "response_item" and isinstance(payload, dict):
            if (
                not saw_response_item_final
                and payload.get("type") == "message"
                and payload.get("phase") == "final"
            ):
                result.final_output = _extract_output_text(payload.get("content", []))
                if result.final_output:
                    saw_response_item_final = True
            continue

        if event_type == "event_msg" and isinstance(payload, dict):
            wait_kind = payload.get("wait_kind")
            wait_message = payload.get("wait_message")
            failure_text = payload.get("failure_text")
            if isinstance(wait_kind, str):
                result.wait_kind = wait_kind
            if isinstance(wait_message, str):
                result.wait_message = wait_message
            if isinstance(failure_text, str):
                result.failure_text = failure_text
    return result


def _extract_output_text(content: object) -> Optional[str]:
    if not isinstance(content, list):
        return None

    texts: List[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "output_text":
            continue
        text = item.get("text")
        if isinstance(text, str):
            texts.append(text)

    if not texts:
        return None

    final_text = "\n".join(texts).strip()
    return final_text or None


def _string_config_override(key: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '{0}="{1}"'.format(key, escaped)


def _array_config_override(key: str, values: List[str]) -> str:
    return "{0}={1}".format(key, json.dumps(values))


def _extract_session_started_id(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if event.get("type") != "thread.started":
        return None
    thread_id = event.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    return None


def _timeout_failure_text(timeout_seconds: Optional[float]) -> str:
    if timeout_seconds is None:
        return "codex exec timed out"
    if float(timeout_seconds).is_integer():
        rendered = str(int(timeout_seconds))
    else:
        rendered = "{0:.1f}".format(timeout_seconds)
    return "codex exec timed out after {0}s".format(rendered)


def _exit_code_failure_text(returncode: Optional[int]) -> str:
    if returncode is None:
        return "codex exec failed"
    return "codex exec failed with exit code {0}".format(returncode)
