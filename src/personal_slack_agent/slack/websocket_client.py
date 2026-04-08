from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any, Callable, Dict, Optional

from .events import SlackRealtimeEvent
from .events import normalize_slack_ws_event


class SlackWebsocketClient:
    def __init__(
        self,
        on_event: Callable[[SlackRealtimeEvent], None],
        on_invalid_frame: Optional[Callable[[str], None]] = None,
        on_reconnect: Optional[Callable[[int, float], None]] = None,
        backoff_seconds: Optional[Callable[[int], float]] = None,
    ):
        self._on_event = on_event
        self._on_invalid_frame = on_invalid_frame
        self._on_reconnect = on_reconnect
        self._backoff_seconds = backoff_seconds or _default_backoff_seconds
        self._reconnect_attempt = 0

    def handle_raw_frame(self, raw_frame: str) -> None:
        try:
            payload = json.loads(raw_frame)
        except JSONDecodeError:
            if self._on_invalid_frame is not None:
                self._on_invalid_frame(raw_frame)
            return

        if not isinstance(payload, dict):
            if self._on_invalid_frame is not None:
                self._on_invalid_frame(raw_frame)
            return
        self.handle_frame(payload)

    def handle_frame(self, payload: Dict[str, Any]) -> None:
        event = normalize_slack_ws_event(payload)
        if event is not None:
            self._on_event(event)

    def handle_disconnect(self) -> float:
        self._reconnect_attempt += 1
        backoff = float(self._backoff_seconds(self._reconnect_attempt))
        if self._on_reconnect is not None:
            self._on_reconnect(self._reconnect_attempt, backoff)
        return backoff

    def reset_reconnect_attempts(self) -> None:
        self._reconnect_attempt = 0


def _default_backoff_seconds(attempt: int) -> float:
    return min(30.0, float(2 ** max(0, attempt - 1)))
