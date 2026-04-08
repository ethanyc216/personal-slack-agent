from personal_slack_agent.slack.api_client import SlackApiClient
from personal_slack_agent.slack.auth import SlackApiSession
from personal_slack_agent.slack.playwright_adapter import PlaywrightSlackAdapter


def test_conversations_history_uses_private_api_post_shape():
    calls = []

    def fake_call(method_name, params):
        calls.append((method_name, params))
        return {"ok": True, "messages": []}

    client = SlackApiClient(
        workspace_name="workspace",
        session=SlackApiSession(
            origin="https://example.enterprise.slack.com",
            token="xoxc-demo-token",
        ),
        call_api=fake_call,
    )

    client.conversations_history(channel_id="C123", limit=50)

    assert calls == [("conversations.history", {"channel": "C123", "limit": 50})]


def test_conversations_history_supports_oldest_cursor_params():
    calls = []

    def fake_call(method_name, params):
        calls.append((method_name, params))
        return {"ok": True, "messages": []}

    client = SlackApiClient(
        workspace_name="workspace",
        session=SlackApiSession(
            origin="https://example.enterprise.slack.com",
            token="xoxc-demo-token",
        ),
        call_api=fake_call,
    )

    client.conversations_history(channel_id="C123", limit=50, oldest="10.0")

    assert calls == [
        (
            "conversations.history",
            {
                "channel": "C123",
                "limit": 50,
                "oldest": "10.0",
                "inclusive": "false",
            },
        )
    ]


def test_conversations_history_supports_latest_window_params():
    calls = []

    def fake_call(method_name, params):
        calls.append((method_name, params))
        return {"ok": True, "messages": []}

    client = SlackApiClient(
        workspace_name="workspace",
        session=SlackApiSession(
            origin="https://example.enterprise.slack.com",
            token="xoxc-demo-token",
        ),
        call_api=fake_call,
    )

    client.conversations_history(channel_id="C123", limit=50, latest="55.0")

    assert calls == [
        (
            "conversations.history",
            {
                "channel": "C123",
                "limit": 50,
                "latest": "55.0",
                "inclusive": "false",
            },
        )
    ]


def test_playwright_adapter_api_client_delegates_to_underlying_slack_api_call():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter._discover_api_session = lambda workspace_name: (  # type: ignore[method-assign]
        "xoxc-demo-token",
        "https://example.enterprise.slack.com",
    )
    calls = []

    def fake_call(workspace_name, method_name, params, retry_on_auth_error=True):
        calls.append((workspace_name, method_name, params, retry_on_auth_error))
        return {"ok": True, "messages": []}

    adapter._call_slack_api = fake_call  # type: ignore[method-assign]

    client = adapter._api_client("workspace")
    payload = client.conversations_history(channel_id="C123", limit=50)

    assert payload == {"ok": True, "messages": []}
    assert calls == [
        (
            "workspace",
            "conversations.history",
            {"channel": "C123", "limit": 50},
            True,
        )
    ]


def test_conversations_replies_supports_oldest_cursor_params():
    calls = []

    def fake_call(method_name, params):
        calls.append((method_name, params))
        return {"ok": True, "messages": []}

    client = SlackApiClient(
        workspace_name="workspace",
        session=SlackApiSession(
            origin="https://example.enterprise.slack.com",
            token="xoxc-demo-token",
        ),
        call_api=fake_call,
    )

    client.conversations_replies(
        channel_id="C123",
        thread_ts="5.0",
        limit=200,
        oldest="10.0",
    )

    assert calls == [
        (
            "conversations.replies",
            {
                "channel": "C123",
                "ts": "5.0",
                "limit": 200,
                "oldest": "10.0",
                "inclusive": "false",
            },
        )
    ]
