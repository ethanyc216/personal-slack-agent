from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List, Tuple


@dataclass(frozen=True)
class GeneratedFile:
    path: str
    content: str


_FILE_BLOCK_PATTERN = re.compile(
    r"""
    ^[ \t]*
    (?:[-*+][ \t]+)?
    (?:\*\*)?
    (?:
        `(?P<backtick_path>[^`\n]+)` |
        "(?P<quoted_path>[^"\n]+)" |
        (?P<plain_path>[A-Za-z0-9_./-]+\.[A-Za-z0-9._-]+)
    )
    (?:\*\*)?
    [ \t]*:?
    [ \t]*\n
    ```[^\n`]*\n
    (?P<content>.*?)
    \n```
    [ \t]*(?=\n|$)
    """,
    re.MULTILINE | re.DOTALL | re.VERBOSE,
)


def extract_generated_files(final_output: str) -> Tuple[str, List[GeneratedFile]]:
    files: List[GeneratedFile] = []
    summary_parts: List[str] = []
    cursor = 0

    for match in _FILE_BLOCK_PATTERN.finditer(final_output):
        before = final_output[cursor : match.start()].strip()
        if before:
            summary_parts.append(before)
        path = (
            match.group("backtick_path")
            or match.group("quoted_path")
            or match.group("plain_path")
            or ""
        ).strip()
        content = match.group("content").rstrip("\n")
        files.append(GeneratedFile(path=path, content=content))
        cursor = match.end()

    after = final_output[cursor:].strip()
    if after:
        summary_parts.append(after)

    summary = "\n\n".join(part for part in summary_parts if part).strip()
    return summary, files


_SLACK_CODE_FENCE_LANGUAGE_PATTERN = re.compile(r"(?m)^```[^\n`]+\n")


def normalize_slack_markdown(text: str) -> str:
    return _SLACK_CODE_FENCE_LANGUAGE_PATTERN.sub("```\n", text)
