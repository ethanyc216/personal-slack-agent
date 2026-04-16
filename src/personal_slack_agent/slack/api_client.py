from __future__ import annotations

import json
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

    def users_conversations(
        self,
        limit: int = 200,
        types: str | None = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if types is not None:
            params["types"] = types
        return self._call_api("users.conversations", params)

    def conversations_list(
        self,
        limit: int = 200,
        types: str | None = None,
        exclude_archived: bool = True,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "limit": limit,
            "exclude_archived": str(exclude_archived).lower(),
        }
        if types is not None:
            params["types"] = types
        return self._call_api("conversations.list", params)

    def search_messages(
        self,
        query: str,
        count: int = 20,
        page: int = 1,
    ) -> Dict[str, Any]:
        return self._call_api(
            "search.messages",
            {
                "query": query,
                "count": count,
                "page": page,
            },
        )

    def api_test(self) -> Dict[str, Any]:
        return self._call_api("api.test", {})

    def chat_post_message(
        self,
        channel_id: str,
        text: str,
        thread_ts: str | None = None,
        reply_broadcast: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "channel": channel_id,
            "text": text,
            "reply_broadcast": str(reply_broadcast).lower(),
        }
        if thread_ts is not None:
            params["thread_ts"] = thread_ts
        return self._call_api("chat.postMessage", params)

    def reactions_add(
        self,
        channel_id: str,
        name: str,
        timestamp: str,
    ) -> Dict[str, Any]:
        return self._call_api(
            "reactions.add",
            {
                "channel": channel_id,
                "name": name,
                "timestamp": timestamp,
            },
        )

    def files_get_upload_url_external(
        self,
        filename: str,
        length: int,
    ) -> Dict[str, Any]:
        return self._call_api(
            "files.getUploadURLExternal",
            {
                "filename": filename,
                "length": str(length),
            },
        )

    def files_complete_upload_external(
        self,
        files: list[dict[str, str]],
        channel_id: str | None = None,
        thread_ts: str | None = None,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "files": json.dumps(files, separators=(",", ":")),
        }
        if channel_id is not None:
            params["channel_id"] = channel_id
        if thread_ts is not None:
            params["thread_ts"] = thread_ts
        return self._call_api("files.completeUploadExternal", params)
