from personal_slack_agent.slack.websocket_client import SlackWebsocketClient


def test_websocket_client_filters_non_message_frames():
    events = []
    client = SlackWebsocketClient(on_event=events.append)

    client.handle_frame({"type": "presence_change"})
    client.handle_frame(
        {
            "type": "message",
            "subtype": "message_replied",
            "message": {
                "channel": "C123",
                "thread_ts": "1.0",
                "latest_reply": "2.0",
            },
        }
    )

    assert len(events) == 1
    assert events[0].kind == "thread_reply_seen"


def test_websocket_client_parses_raw_json_frames():
    events = []
    client = SlackWebsocketClient(on_event=events.append)

    client.handle_raw_frame('{"type":"message","channel":"C123","ts":"3.0","text":"hi"}')

    assert len(events) == 1
    assert events[0].kind == "root_message_seen"
    assert events[0].message_ts == "3.0"


def test_websocket_client_skips_invalid_json_and_calls_invalid_frame_hook():
    events = []
    invalid_frames = []
    client = SlackWebsocketClient(
        on_event=events.append,
        on_invalid_frame=invalid_frames.append,
    )

    client.handle_raw_frame("{not-json")

    assert events == []
    assert invalid_frames == ["{not-json"]


def test_websocket_client_calls_invalid_frame_hook_for_valid_non_object_json():
    events = []
    invalid_frames = []
    client = SlackWebsocketClient(
        on_event=events.append,
        on_invalid_frame=invalid_frames.append,
    )

    client.handle_raw_frame("[]")
    client.handle_raw_frame('"ping"')

    assert events == []
    assert invalid_frames == ["[]", '"ping"']


def test_websocket_client_disconnect_hook_receives_backoff_seconds():
    reconnect_attempts = []
    client = SlackWebsocketClient(
        on_event=lambda _event: None,
        on_reconnect=lambda attempt, backoff_seconds: reconnect_attempts.append(
            (attempt, backoff_seconds)
        ),
        backoff_seconds=lambda attempt: attempt * 0.5,
    )

    client.handle_disconnect()
    client.handle_disconnect()

    assert reconnect_attempts == [(1, 0.5), (2, 1.0)]


def test_websocket_client_reset_reconnect_attempts_restarts_backoff_sequence():
    reconnect_attempts = []
    client = SlackWebsocketClient(
        on_event=lambda _event: None,
        on_reconnect=lambda attempt, backoff_seconds: reconnect_attempts.append(
            (attempt, backoff_seconds)
        ),
        backoff_seconds=lambda attempt: attempt * 0.5,
    )

    client.handle_disconnect()
    client.handle_disconnect()
    client.reset_reconnect_attempts()
    client.handle_disconnect()

    assert reconnect_attempts == [(1, 0.5), (2, 1.0), (1, 0.5)]
