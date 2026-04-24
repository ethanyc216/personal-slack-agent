from dataclasses import dataclass
from typing import List, Optional, Protocol


@dataclass
class SlackRootMessage:
    workspace_name: str
    channel_name: str
    thread_ts: str
    message_ts: str
    author_actor_id: str
    text: str


@dataclass
class SlackThreadReplyMessage:
    workspace_name: str
    channel_name: str
    thread_ts: str
    message_ts: str
    author_actor_id: str
    text: str


@dataclass
class SlackThreadMessage:
    workspace_name: str
    channel_name: str
    thread_ts: str
    message_ts: str
    author_actor_id: str
    text: str


@dataclass
class SlackSearchMessage:
    workspace_name: str
    channel_id: str
    message_ts: str
    thread_ts: Optional[str]
    author_actor_id: str
    text: str


class SlackBrowserAdapter(Protocol):
    def get_channel_id(
        self,
        workspace_name: str,
        channel_name: str,
    ) -> str:
        ...

    def subscribe_to_realtime_frames(
        self,
        workspace_name: str,
        on_frame,
        on_disconnect,
    ) -> None:
        ...

    def list_root_messages(
        self,
        workspace_name: str,
        channel_name: str,
        oldest: Optional[str] = None,
        latest: Optional[str] = None,
        limit: int = 50,
    ) -> List[SlackRootMessage]:
        ...

    def list_accessible_conversation_ids(
        self,
        workspace_name: str,
    ) -> List[str]:
        ...

    def search_messages(
        self,
        workspace_name: str,
        query: str,
        count: int = 20,
        page: int = 1,
        sort: Optional[str] = None,
        sort_dir: Optional[str] = None,
    ) -> List[SlackSearchMessage]:
        ...

    def list_thread_replies(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        oldest: Optional[str] = None,
        limit: int = 200,
    ) -> List[SlackThreadReplyMessage]:
        ...

    def list_thread_messages(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> List[SlackThreadMessage]:
        ...

    def post_thread_reply(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        text: str,
    ) -> str:
        ...

    def post_root_message(
        self,
        workspace_name: str,
        channel_name: str,
        text: str,
    ) -> str:
        ...

    def upload_text_snippet(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        filename: str,
        content: str,
    ) -> str:
        ...

    def delete_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
    ) -> None:
        ...

    def update_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
        text: str,
    ) -> None:
        ...

    def add_reaction(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
        emoji_name: str,
    ) -> None:
        ...

    def find_existing_bob_messages(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> List[str]:
        ...
