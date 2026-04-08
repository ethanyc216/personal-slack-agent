from __future__ import annotations

from typing import Any, Callable, Dict

from .auth import SlackApiSession


class SlackApiClient:
    def __init__(
        self,
        workspace_name: str,
        session: SlackApiSession,
        call_api: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    ):
        self.workspace_name = workspace_name
        self.session = session
        self._call_api = call_api

    def conversations_history(
        self,
        channel_id: str,
        limit: int = 50,
        oldest: str | None = None,
        latest: str | None = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"channel": channel_id, "limit": limit}
        if oldest is not None:
            params["oldest"] = oldest
            params["inclusive"] = "false"
        if latest is not None:
            params["latest"] = latest
            params["inclusive"] = "false"
        return self._call_api("conversations.history", params)

    def conversations_replies(
        self,
        channel_id: str,
        thread_ts: str,
        limit: int = 200,
        oldest: str | None = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"channel": channel_id, "ts": thread_ts, "limit": limit}
        if oldest is not None:
            params["oldest"] = oldest
            params["inclusive"] = "false"
        return self._call_api("conversations.replies", params)

    def chat_post_message(
        self,
        channel_id: str,
        thread_ts: str,
        text: str,
        reply_broadcast: bool = False,
    ) -> Dict[str, Any]:
        return self._call_api(
            "chat.postMessage",
            {
                "channel": channel_id,
                "thread_ts": thread_ts,
                "text": text,
                "reply_broadcast": str(reply_broadcast).lower(),
            },
        )
