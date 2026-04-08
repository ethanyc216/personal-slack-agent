from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional


@dataclass(frozen=True)
class SlackApiSession:
    origin: str
    token: str


def extract_api_session_from_request(url: str, post_data: str) -> Optional[SlackApiSession]:
    origin_match = re.match(r"^(https://[^/]+)/api/", url)
    token_match = re.search(r'name="token"\r?\n\r?\n([^\r\n]+)', post_data or "")
    if not origin_match or not token_match:
        return None
    return SlackApiSession(
        origin=origin_match.group(1),
        token=token_match.group(1),
    )
