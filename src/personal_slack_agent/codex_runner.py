import json
import os
import subprocess
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
        exec_command: Optional[Callable[[List[str], Optional[str]], str]] = None,
        env_overrides: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._exec_command = exec_command or self._default_exec_command
        self._env_overrides = dict(env_overrides or {})

    def run_new_session(self, prompt: str, cwd: str, additional_roots: List[str]) -> CodexRunResult:
        command = build_new_session_command(prompt=prompt, cwd=cwd, additional_roots=additional_roots)
        return self._run_and_parse(command, cwd=cwd)

    def resume_session(self, session_id: str, prompt: str, cwd: str) -> CodexRunResult:
        command = build_resume_command(session_id=session_id, prompt=prompt)
        return self._run_and_parse(command, cwd=cwd)

    def _run_and_parse(self, command: List[str], cwd: Optional[str] = None) -> CodexRunResult:
        try:
            output = self._exec_command(command, cwd)
        except Exception as exc:
            return CodexRunResult(failure_text=str(exc).strip() or exc.__class__.__name__)
        return parse_jsonl_events(output.splitlines())

    def _default_exec_command(self, command: List[str], cwd: Optional[str] = None) -> str:
        env = None
        if self._env_overrides:
            env = os.environ.copy()
            env.update(self._env_overrides)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
        )
        output = completed.stdout or ""
        if completed.returncode == 0:
            return output

        stderr = (completed.stderr or "").strip()
        combined = "\n".join(part for part in [output.strip(), stderr] if part).strip()
        raise RuntimeError(combined or "codex exec failed")


def build_new_session_command(prompt: str, cwd: str, additional_roots: List[str]) -> List[str]:
    command = ["codex", "exec", "--json", "--skip-git-repo-check", "--cd", cwd]
    for root in additional_roots:
        command.extend(["--add-dir", root])
    command.append(prompt)
    return command


def build_resume_command(session_id: str, prompt: str) -> List[str]:
    return ["codex", "exec", "resume", session_id, "--json", "--skip-git-repo-check", prompt]


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
