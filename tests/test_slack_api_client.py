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

    client.reactions_add(channel_id="C123", name="ok_hand", timestamp="123.456")

    assert calls == [
        (
            "reactions.add",
            {
                "channel": "C123",
                "name": "ok_hand",
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

    client.search_messages(query="in:bob_test_channel", count=20, page=1)

    assert calls == [
        (
            "search.messages",
            {
                "query": "in:bob_test_channel",
                "count": 20,
                "page": 1,
            },
        )
    ]


def test_web_client_bootstrap_api_methods_use_expected_private_api_shapes():
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

    client.client_user_boot(reason="deferred-data")
    client.users_channel_sections_list(cursor="cursor-1", limit=25)
    client.client_counts()
    client.conversations_view(channel_id="C123", limit=50)
    client.conversations_list_prefs(channel_id="C123")

    assert calls == [
        ("client.userBoot", {"_x_reason": "deferred-data"}),
        ("users.channelSections.list", {"cursor": "cursor-1", "limit": 25}),
        ("client.counts", {}),
        ("conversations.view", {"channel": "C123", "limit": 50}),
        ("conversations.listPrefs", {"channel": "C123"}),
    ]


def test_playwright_adapter_exposes_raw_web_client_api_helpers():
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
        return {"ok": True, "method": method_name}

    adapter._call_slack_api = fake_call  # type: ignore[method-assign]

    assert adapter.client_user_boot("workspace", reason="deferred-data") == {
        "ok": True,
        "method": "client.userBoot",
    }
    assert adapter.users_channel_sections_list(
        "workspace",
        cursor="cursor-1",
        limit=25,
    ) == {"ok": True, "method": "users.channelSections.list"}
    assert adapter.client_counts("workspace") == {"ok": True, "method": "client.counts"}
    assert adapter.conversations_view("workspace", channel_id="C123", limit=50) == {
        "ok": True,
        "method": "conversations.view",
    }
    assert adapter.conversations_list_prefs("workspace", channel_id="C123") == {
        "ok": True,
        "method": "conversations.listPrefs",
    }

    assert calls == [
        (
            "workspace",
            "client.userBoot",
            {"_x_reason": "deferred-data"},
            True,
        ),
        (
            "workspace",
            "users.channelSections.list",
            {"cursor": "cursor-1", "limit": 25},
            True,
        ),
        ("workspace", "client.counts", {}, True),
        ("workspace", "conversations.view", {"channel": "C123", "limit": 50}, True),
        ("workspace", "conversations.listPrefs", {"channel": "C123"}, True),
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
