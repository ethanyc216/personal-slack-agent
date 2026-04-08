from personal_slack_agent.slack.events import SlackRealtimeEvent
from personal_slack_agent.slack.events import normalize_slack_ws_event


def test_normalize_root_message_event_emits_root_message_signal():
    payload = {
        "type": "message",
        "channel": "C123",
        "ts": "1775027000.111111",
        "text": "Bob, please check this",
    }

    event = normalize_slack_ws_event(payload)

    assert event == SlackRealtimeEvent(
        kind="root_message_seen",
        channel_id="C123",
        thread_ts=None,
        message_ts="1775027000.111111",
    )


def test_normalize_message_replied_event_emits_thread_reply_signal():
    payload = {
        "type": "message",
        "subtype": "message_replied",
        "message": {
            "channel": "C123",
            "thread_ts": "1774999116.837699",
            "latest_reply": "1775027491.643739",
        },
    }

    event = normalize_slack_ws_event(payload)

    assert event == SlackRealtimeEvent(
        kind="thread_reply_seen",
        channel_id="C123",
        thread_ts="1774999116.837699",
        message_ts="1775027491.643739",
    )


def test_normalize_message_replied_event_supports_documented_shape():
    payload = {
        "type": "message",
        "subtype": "message_replied",
        "channel": "C999",
        "message": {
            "ts": "1774999116.837699",
            "thread_ts": "1774999116.837699",
            "replies": [{"user": "U123", "ts": "1775027491.643739"}],
        },
    }

    event = normalize_slack_ws_event(payload)

    assert event == SlackRealtimeEvent(
        kind="thread_reply_seen",
        channel_id="C999",
        thread_ts="1774999116.837699",
        message_ts="1775027491.643739",
    )


def test_normalize_plain_thread_message_event_emits_thread_reply_signal():
    payload = {
        "type": "message",
        "channel": "C321",
        "thread_ts": "1774999116.837699",
        "ts": "1775027491.643739",
        "text": "plain thread reply",
    }

    event = normalize_slack_ws_event(payload)

    assert event == SlackRealtimeEvent(
        kind="thread_reply_seen",
        channel_id="C321",
        thread_ts="1774999116.837699",
        message_ts="1775027491.643739",
    )


def test_normalize_ignores_non_message_payloads():
    assert normalize_slack_ws_event({"type": "presence_change"}) is None


def test_normalize_ignores_root_messages_without_channel_or_timestamp():
    assert normalize_slack_ws_event({"type": "message", "ts": "123.456"}) is None
    assert normalize_slack_ws_event({"type": "message", "channel": "C123"}) is None
