from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from .models import DEFAULT_ASSISTANT_NAMES


@dataclass(frozen=True)
class AssistantInvocation:
    alias: str
    configured_name: str
    remainder: str


def normalize_assistant_names(names: list[str]) -> list[str]:
    if not names:
        return list(DEFAULT_ASSISTANT_NAMES)
    normalized = []
    seen = set()
    for raw_name in names:
        name = raw_name.strip()
        if not name:
            raise ValueError("assistant names must not contain empty values")
        if any(unicodedata.category(char)[0] == "C" for char in name):
            raise ValueError("assistant names must not contain control characters")
        folded = name.casefold()
        if folded in seen:
            raise ValueError("assistant names must not contain duplicates")
        seen.add(folded)
        normalized.append(name)
    return normalized


def match_assistant_invocation(
    text: str,
    assistant_names: list[str],
) -> AssistantInvocation | None:
    stripped = text.strip()
    if not stripped:
        return None
    for configured_name in sorted(
        _effective_assistant_names(assistant_names),
        key=len,
        reverse=True,
    ):
        prefix = stripped[: len(configured_name)]
        if prefix.casefold() != configured_name.casefold():
            continue
        next_char = stripped[len(configured_name) : len(configured_name) + 1]
        if next_char and _is_name_continuation(next_char):
            continue
        remainder = stripped[len(configured_name) :]
        remainder = remainder.lstrip()
        remainder = remainder.lstrip(",:;!-?")
        remainder = remainder.lstrip()
        return AssistantInvocation(
            alias=prefix,
            configured_name=configured_name,
            remainder=remainder,
        )
    return None


def strip_assistant_prefix(text: str, assistant_names: list[str]) -> str:
    match = match_assistant_invocation(text, assistant_names)
    if match is None:
        return text.strip()
    return match.remainder


def assistant_label_from_text(
    text: str,
    assistant_names: list[str],
    fallback: str,
) -> str:
    match = match_assistant_invocation(text, assistant_names)
    if match is not None:
        return match.configured_name
    stripped = text.strip()
    if stripped.casefold().startswith("close"):
        next_char = stripped[5:6]
        if not next_char or not _is_name_continuation(next_char):
            close_match = match_assistant_invocation(stripped[5:].strip(), assistant_names)
            if close_match is not None and not close_match.remainder:
                return close_match.configured_name
    return _canonicalize_assistant_label(fallback, assistant_names)


def is_manual_close_request(text: str, assistant_names: list[str]) -> bool:
    stripped = text.strip()
    prefix_match = match_assistant_invocation(stripped, assistant_names)
    if prefix_match is not None:
        return prefix_match.remainder.casefold() == "close"
    lowered = stripped.casefold()
    if not lowered.startswith("close"):
        return False
    next_char = stripped[5:6]
    if next_char and _is_name_continuation(next_char):
        return False
    remainder = stripped[5:].strip()
    match = match_assistant_invocation(remainder, assistant_names)
    return match is not None and not match.remainder


def _is_name_continuation(char: str) -> bool:
    return char.isalnum() or char in {"_", "-"}


def _effective_assistant_names(assistant_names: list[str]) -> list[str]:
    return assistant_names or list(DEFAULT_ASSISTANT_NAMES)


def _canonicalize_assistant_label(label: str, assistant_names: list[str]) -> str:
    for configured_name in _effective_assistant_names(assistant_names):
        if label.casefold() == configured_name.casefold():
            return configured_name
    return label
