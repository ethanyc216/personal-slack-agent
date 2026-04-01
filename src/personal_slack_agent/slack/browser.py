from dataclasses import dataclass
from typing import List, Protocol


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


class SlackBrowserAdapter(Protocol):
    def list_root_messages(
        self,
        workspace_name: str,
        channel_name: str,
    ) -> List[SlackRootMessage]:
        ...

    def list_thread_replies(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> List[SlackThreadReplyMessage]:
        ...

    def post_thread_reply(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        text: str,
    ) -> str:
        ...

    def delete_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
    ) -> None:
        ...

    def find_existing_bob_messages(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> List[str]:
        ...
