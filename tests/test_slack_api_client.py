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


def test_users_conversations_uses_expected_private_api_post_shape():
    calls = []

    def fake_call(method_name, params):
        calls.append((method_name, params))
        return {"ok": True, "channels": []}

    client = SlackApiClient(
        workspace_name="workspace",
        session=SlackApiSession(
            origin="https://example.enterprise.slack.com",
            token="xoxc-demo-token",
        ),
        call_api=fake_call,
    )

    client.users_conversations(limit=200, types="public_channel,private_channel")

    assert calls == [
        (
            "users.conversations",
            {
                "limit": 200,
                "types": "public_channel,private_channel",
            },
        )
    ]


def test_conversations_list_uses_expected_private_api_post_shape():
    calls = []

    def fake_call(method_name, params):
        calls.append((method_name, params))
        return {"ok": True, "channels": []}

    client = SlackApiClient(
        workspace_name="workspace",
        session=SlackApiSession(
            origin="https://example.enterprise.slack.com",
            token="xoxc-demo-token",
        ),
        call_api=fake_call,
    )

    client.conversations_list(
        limit=200,
        types="public_channel,private_channel",
        exclude_archived=True,
    )

    assert calls == [
        (
            "conversations.list",
            {
                "limit": 200,
                "types": "public_channel,private_channel",
                "exclude_archived": "true",
            },
        )
    ]


def test_reactions_add_uses_expected_private_api_post_shape():
    calls = []

    def fake_call(method_name, params):
        calls.append((method_name, params))
        return {"ok": True}

    client = SlackApiClient(
        workspace_name="workspace",
        session=SlackApiSession(
            origin="https://example.enterprise.slack.com",
            token="xoxc-demo-token",
        ),
        call_api=fake_call,
    )

    client.reactions_add(channel_id="C123", name="ack", timestamp="123.456")

    assert calls == [
        (
            "reactions.add",
            {
                "channel": "C123",
                "name": "ack",
                "timestamp": "123.456",
            },
        )
    ]


def test_search_messages_uses_expected_private_api_post_shape():
    calls = []

    def fake_call(method_name, params):
        calls.append((method_name, params))
        return {"ok": True, "messages": {"matches": []}}

    client = SlackApiClient(
        workspace_name="workspace",
        session=SlackApiSession(
            origin="https://example.enterprise.slack.com",
            token="xoxc-demo-token",
        ),
        call_api=fake_call,
    )

    client.search_messages(query="in:yifanche-bob-test", count=20, page=1)

    assert calls == [
        (
            "search.messages",
            {
                "query": "in:yifanche-bob-test",
                "count": 20,
                "page": 1,
            },
        )
    ]


def test_file_upload_api_methods_use_expected_private_api_shapes():
    calls = []

    def fake_call(method_name, params):
        calls.append((method_name, params))
        return {"ok": True}

    client = SlackApiClient(
        workspace_name="workspace",
        session=SlackApiSession(
            origin="https://example.enterprise.slack.com",
            token="xoxc-demo-token",
        ),
        call_api=fake_call,
    )

    client.files_get_upload_url_external(filename="README.md", length=42)
    client.files_complete_upload_external(
        files=[{"id": "F123", "title": "scripts/shepherd/README.md"}],
        channel_id="C123",
        thread_ts="5.0",
    )

    assert calls == [
        ("files.getUploadURLExternal", {"filename": "README.md", "length": "42"}),
        (
            "files.completeUploadExternal",
            {
                "files": '[{"id":"F123","title":"scripts/shepherd/README.md"}]',
                "channel_id": "C123",
                "thread_ts": "5.0",
            },
        ),
    ]
