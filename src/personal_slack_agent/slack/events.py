from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SlackRealtimeEvent:
    kind: str
    channel_id: str
    thread_ts: Optional[str]
    message_ts: Optional[str]


def normalize_slack_ws_event(payload: Dict[str, Any]) -> Optional[SlackRealtimeEvent]:
    if payload.get("type") != "message":
        return None

    subtype = payload.get("subtype")
    if subtype == "message_replied":
        message = payload.get("message")
        if not isinstance(message, dict):
            return None

        channel_id = _as_non_empty_str(message.get("channel")) or _as_non_empty_str(
            payload.get("channel")
        )
        thread_ts = _as_non_empty_str(message.get("thread_ts")) or _as_non_empty_str(
            message.get("ts")
        )
        latest_reply = _as_non_empty_str(message.get("latest_reply")) or _find_latest_reply_ts(
            message.get("replies")
        )
        if channel_id is None or thread_ts is None or latest_reply is None:
            return None

        return SlackRealtimeEvent(
            kind="thread_reply_seen",
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=latest_reply,
        )

    if subtype is not None:
        return None

    channel_id = _as_non_empty_str(payload.get("channel"))
    message_ts = _as_non_empty_str(payload.get("ts"))
    thread_ts = _as_non_empty_str(payload.get("thread_ts"))
    if channel_id is None or message_ts is None:
        return None

    if thread_ts is not None and thread_ts != message_ts:
        return SlackRealtimeEvent(
            kind="thread_reply_seen",
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=message_ts,
        )

    return SlackRealtimeEvent(
        kind="root_message_seen",
        channel_id=channel_id,
        thread_ts=None,
        message_ts=message_ts,
    )


def _as_non_empty_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _find_latest_reply_ts(replies: Any) -> Optional[str]:
    if not isinstance(replies, list):
        return None

    for reply in reversed(replies):
        if not isinstance(reply, dict):
            continue
        reply_ts = _as_non_empty_str(reply.get("ts"))
        if reply_ts is not None:
            return reply_ts
    return None
