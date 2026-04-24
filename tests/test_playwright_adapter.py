import threading
import time

from playwright._impl._errors import TargetClosedError

from playwright.sync_api import Error as PlaywrightError

from personal_slack_agent.slack.playwright_adapter import PlaywrightSlackAdapter


class FakeWebSocket:
    def __init__(self, url: str):
        self.url = url
        self.handlers = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event, payload):
        for handler in self.handlers.get(event, []):
            handler(payload)


class FakePage:
    def __init__(self, url: str = ""):
        self.url = url
        self.goto_calls = []
        self.reload_calls = []
        self.handlers = {}
        self.closed = False

    def goto(self, url: str, **kwargs) -> None:
        del kwargs
        self.goto_calls.append(url)
        self.url = url

    def reload(self, **kwargs) -> None:
        self.reload_calls.append(kwargs)

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event, payload):
        for handler in self.handlers.get(event, []):
            handler(payload)

    def evaluate(self, script, arg):
        del script
        selector = arg["selector"]
        if selector == '[data-qa="channel_sidebar_name_yifanche-bob"]':
            return "C0AQT4S6QHM"
        if selector == '[data-qa="channel_sidebar_name_missing-channel"]':
            return None
        raise AssertionError("Unexpected selector: {0}".format(selector))

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, pages=None):
        self.pages = pages or []
        self.new_page_calls = 0
        self.closed = False

    def new_page(self):
        self.new_page_calls += 1
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, contexts=None):
        self.contexts = contexts or []
        self.new_context_calls = 0
        self.closed = False
        self.browser_cdp_session = None

    def new_context(self):
        self.new_context_calls += 1
        context = FakeContext()
        self.contexts.append(context)
        return context

    def new_browser_cdp_session(self):
        if self.browser_cdp_session is None:
            raise AssertionError("No browser CDP session configured")
        return self.browser_cdp_session

    def close(self):
        self.closed = True


class FakeBrowserCDPSession:
    def __init__(self, on_create_target=None):
        self.calls = []
        self.detached = False
        self._on_create_target = on_create_target

    def send(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == "Target.createTarget":
            if self._on_create_target is not None:
                self._on_create_target(params)
            return {"targetId": "target-123"}
        if method == "Target.closeTarget":
            return {"success": True}
        raise AssertionError("Unexpected CDP method: {0}".format(method))

    def detach(self):
        self.detached = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser, dedicated_context: FakeContext):
        self.browser = browser
        self.dedicated_context = dedicated_context
        self.connect_over_cdp_calls = []
        self.launch_persistent_context_calls = []

    def connect_over_cdp(self, cdp_url: str, **kwargs):
        self.connect_over_cdp_calls.append((cdp_url, kwargs))
        return self.browser

    def launch_persistent_context(self, user_data_dir, **kwargs):
        self.launch_persistent_context_calls.append((user_data_dir, kwargs))
        return self.dedicated_context


class FakePlaywright:
    def __init__(self, chromium: FakeChromium):
        self.chromium = chromium
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeSyncPlaywright:
    def __init__(self, playwright_obj: FakePlaywright):
        self.playwright_obj = playwright_obj

    def start(self):
        return self.playwright_obj


def _loader_for(chromium: FakeChromium):
    sync_obj = FakeSyncPlaywright(FakePlaywright(chromium))

    def _load():
        return lambda: sync_obj

    return _load, sync_obj


def test_connect_cleans_up_failed_shared_browser_attach_before_retry():
    first_playwright = FakePlaywright(
        FakeChromium(
            browser=FakeBrowser([FakeContext([])]),
            dedicated_context=FakeContext([]),
        )
    )

    def fail_connect_over_cdp(cdp_url: str, **kwargs):
        first_attempts.append((cdp_url, kwargs))
        raise PlaywrightError("cdp attach failed")

    first_attempts = []
    first_playwright.chromium.connect_over_cdp = fail_connect_over_cdp  # type: ignore[method-assign]

    second_playwright = FakePlaywright(
        FakeChromium(
            browser=FakeBrowser([FakeContext([])]),
            dedicated_context=FakeContext([]),
        )
    )

    sync_objs = [
        FakeSyncPlaywright(first_playwright),
        FakeSyncPlaywright(second_playwright),
    ]

    def loader():
        return lambda: sync_objs.pop(0)

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
        playwright_loader=loader,
    )

    try:
        adapter.connect()
    except PlaywrightError as exc:
        assert str(exc) == "cdp attach failed"
    else:
        raise AssertionError("Expected PlaywrightError on first connect")

    assert first_attempts == [("http://127.0.0.1:9222", {"timeout": 10000})]
    assert first_playwright.stopped is True
    assert adapter._playwright is None
    assert adapter._browser is None

    browser = adapter.connect()

    assert browser is second_playwright.chromium.browser


def test_shared_browser_connects_over_cdp_and_reuses_matching_tab():
    matching_page = FakePage("https://example.enterprise.slack.com/client/T1/C1")
    shared_context = FakeContext([matching_page])
    chromium = FakeChromium(
        browser=FakeBrowser([shared_context]),
        dedicated_context=FakeContext(),
    )
    loader, _ = _loader_for(chromium)
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
        playwright_loader=loader,
    )

    page = adapter.select_bob_tab("https://example.enterprise.slack.com/")

    assert page is matching_page
    assert chromium.connect_over_cdp_calls == [
        ("http://127.0.0.1:9222", {"timeout": 10000})
    ]
    assert shared_context.new_page_calls == 0


def test_shared_browser_creates_non_focused_workspace_target_when_missing():
    shared_context = FakeContext([])
    browser = FakeBrowser([shared_context])

    def on_create_target(params):
        shared_context.pages.append(FakePage(params["url"]))

    browser.browser_cdp_session = FakeBrowserCDPSession(on_create_target=on_create_target)
    chromium = FakeChromium(
        browser=browser,
        dedicated_context=FakeContext(),
    )
    loader, _sync = _loader_for(chromium)
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
        playwright_loader=loader,
    )

    page = adapter.select_bob_tab("https://app.slack.com/client/T123/C123")

    assert page.url == "https://app.slack.com/client/T123/C123"
    assert shared_context.new_page_calls == 0
    assert browser.browser_cdp_session.calls == [
        (
            "Target.createTarget",
            {
                "url": "https://app.slack.com/client/T123/C123",
                "background": False,
                "focus": False,
            },
        )
    ]
    assert browser.browser_cdp_session.detached is True


def test_dedicated_browser_launches_persistent_context_and_uses_workspace_tab():
    dedicated_context = FakeContext([])
    chromium = FakeChromium(
        browser=FakeBrowser(),
        dedicated_context=dedicated_context,
    )
    loader, _ = _loader_for(chromium)
    adapter = PlaywrightSlackAdapter(
        browser_mode="dedicated_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
        chrome_executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        browser_user_data_dir="/tmp/bob-profile",
        playwright_loader=loader,
    )

    page = adapter.select_bob_tab("https://example.enterprise.slack.com/")

    assert chromium.connect_over_cdp_calls == []
    assert len(chromium.launch_persistent_context_calls) == 1
    user_data_dir, kwargs = chromium.launch_persistent_context_calls[0]
    assert user_data_dir == "/tmp/bob-profile"
    assert kwargs["executable_path"].endswith("Google Chrome")
    assert page.goto_calls == ["https://example.enterprise.slack.com/"]


def test_close_shared_browser_detaches_without_closing_user_browser():
    shared_context = FakeContext([])
    shared_browser = FakeBrowser([shared_context])
    dedicated_context = FakeContext([])
    chromium = FakeChromium(shared_browser, dedicated_context)
    loader, sync_obj = _loader_for(chromium)
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
        playwright_loader=loader,
    )
    adapter.connect()

    adapter.close()

    assert shared_browser.closed is False
    assert dedicated_context.closed is False
    assert sync_obj.playwright_obj.stopped is True


def test_close_dedicated_browser_closes_managed_context():
    shared_browser = FakeBrowser([FakeContext([])])
    dedicated_context = FakeContext([])
    chromium = FakeChromium(shared_browser, dedicated_context)
    loader, sync_obj = _loader_for(chromium)
    adapter = PlaywrightSlackAdapter(
        browser_mode="dedicated_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
        browser_user_data_dir="/tmp/bob-profile",
        playwright_loader=loader,
    )
    adapter.connect()

    adapter.close()

    assert dedicated_context.closed is True
    assert sync_obj.playwright_obj.stopped is True


def test_get_channel_id_uses_seeded_route_when_provided():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_urls({"workspace": "https://app.slack.com/client/T123/C111"})
    adapter.set_channel_urls({("workspace", "other-channel"): "https://app.slack.com/client/T123/C222"})

    assert adapter.get_channel_id("workspace", "other-channel") == "C222"


def test_get_channel_id_resolves_sidebar_channel_id_and_caches_it():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    workspace_page = FakePage()
    adapter.set_workspace_urls({"oracle": "https://app.slack.com/client/E655JKQRX/C03J3TXQBSP"})
    adapter._workspace_page = lambda workspace_name: workspace_page  # type: ignore[method-assign]

    first = adapter.get_channel_id("oracle", "yifanche-bob")
    second = adapter.get_channel_id("oracle", "yifanche-bob")

    assert first == "C0AQT4S6QHM"
    assert second == "C0AQT4S6QHM"


def test_get_channel_id_falls_back_to_users_conversations_when_sidebar_lookup_fails():
    calls = []

    class FakeApiClient:
        def users_conversations(self, limit=200, types=None):
            calls.append(("users.conversations", limit, types))
            return {
                "ok": True,
                "channels": [
                    {
                        "id": "C0AS82WLCBU",
                        "name": "yifanche-bob-test",
                    }
                ],
            }

        def conversations_list(self, limit=200, types=None, exclude_archived=True):
            raise AssertionError("conversations.list should not be used when users.conversations works")

        def search_messages(self, query, count=20, page=1):
            raise AssertionError("search.messages should not be used when users.conversations works")

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_urls({"oracle": "https://app.slack.com/client/E655JKQRX/C03J3TXQBSP"})
    adapter._resolve_sidebar_channel_id = lambda workspace_name, channel_name: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("sidebar missing")
    )
    adapter._api_client = lambda workspace_name: FakeApiClient()  # type: ignore[method-assign]

    channel_id = adapter.get_channel_id("oracle", "yifanche-bob-test")

    assert channel_id == "C0AS82WLCBU"
    assert calls == [("users.conversations", 999, "public_channel,private_channel")]


def test_get_channel_id_falls_back_to_search_when_api_listing_is_restricted():
    calls = []

    class FakeApiClient:
        def users_conversations(self, limit=200, types=None):
            calls.append(("users.conversations", limit, types))
            return {"ok": False, "error": "enterprise_is_restricted"}

        def conversations_list(self, limit=200, types=None, exclude_archived=True):
            calls.append(("conversations.list", limit, types, exclude_archived))
            return {"ok": False, "error": "enterprise_is_restricted"}

        def search_messages(self, query, count=20, page=1):
            calls.append(("search.messages", query, count, page))
            return {
                "ok": True,
                "messages": {
                    "matches": [
                        {
                            "channel": {
                                "id": "C0AS82WLCBU",
                                "name": "yifanche-bob-test",
                            }
                        }
                    ]
                },
            }

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_urls({"oracle": "https://app.slack.com/client/E655JKQRX/C03J3TXQBSP"})
    adapter._resolve_sidebar_channel_id = lambda workspace_name, channel_name: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("sidebar missing")
    )
    adapter._api_client = lambda workspace_name: FakeApiClient()  # type: ignore[method-assign]

    channel_id = adapter.get_channel_id("oracle", "yifanche-bob-test")

    assert channel_id == "C0AS82WLCBU"
    assert calls == [
        ("users.conversations", 999, "public_channel,private_channel"),
        ("conversations.list", 999, "public_channel,private_channel", True),
        ("search.messages", "in:yifanche-bob-test", 20, 1),
    ]


def test_get_channel_id_raises_when_search_fallback_finds_no_exact_channel_match():
    class FakeApiClient:
        def users_conversations(self, limit=200, types=None):
            return {"ok": False, "error": "enterprise_is_restricted"}

        def conversations_list(self, limit=200, types=None, exclude_archived=True):
            return {"ok": False, "error": "enterprise_is_restricted"}

        def search_messages(self, query, count=20, page=1):
            del query
            del count
            del page
            return {
                "ok": True,
                "messages": {
                    "matches": [
                        {
                            "channel": {
                                "id": "C123",
                                "name": "some-other-channel",
                            }
                        }
                    ]
                },
            }

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_urls({"oracle": "https://app.slack.com/client/E655JKQRX/C03J3TXQBSP"})
    adapter._resolve_sidebar_channel_id = lambda workspace_name, channel_name: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("sidebar missing")
    )
    adapter._api_client = lambda workspace_name: FakeApiClient()  # type: ignore[method-assign]

    try:
        adapter.get_channel_id("oracle", "yifanche-bob-test")
    except RuntimeError as exc:
        assert str(exc) == "Could not resolve Slack channel id for channel yifanche-bob-test."
    else:
        raise AssertionError("Expected RuntimeError")


def test_subscribe_to_realtime_frames_registers_listener_and_reloads_page():
    page = FakePage("https://app.slack.com/client/T123/C123")
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter._workspace_page = lambda workspace_name: page  # type: ignore[method-assign]
    frames = []
    disconnects = []

    adapter.subscribe_to_realtime_frames(
        workspace_name="oracle",
        on_frame=frames.append,
        on_disconnect=lambda: disconnects.append("disconnect"),
    )

    websocket = FakeWebSocket("wss://wss-primary.slack.com/?token=xoxc-demo")
    page.emit("websocket", websocket)
    websocket.emit("framereceived", '{"type":"message"}')
    websocket.emit("close", websocket)

    assert page.reload_calls == [{"wait_until": "domcontentloaded", "timeout": 15000}]
    assert frames == ['{"type":"message"}']
    assert disconnects == ["disconnect"]


def test_subscribe_to_realtime_frames_accepts_non_primary_slack_socket_hosts():
    page = FakePage("https://app.slack.com/client/T123/C123")
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter._workspace_page = lambda workspace_name: page  # type: ignore[method-assign]
    frames = []

    adapter.subscribe_to_realtime_frames(
        workspace_name="oracle",
        on_frame=frames.append,
        on_disconnect=lambda: None,
    )

    websocket = FakeWebSocket("wss://wss-backup.slack.com/?token=xoxc-demo")
    page.emit("websocket", websocket)
    websocket.emit("framereceived", '{"type":"message"}')

    assert frames == ['{"type":"message"}']


def test_list_root_messages_uses_api_client_path_only():
    calls = []

    class FakeApiClient:
        def conversations_history(self, channel_id, limit=50, oldest=None, latest=None):
            calls.append((channel_id, limit, oldest, latest))
            return {
                "ok": True,
                "messages": [
                    {
                        "ts": "1774999116.837699",
                        "thread_ts": "1774999116.837699",
                        "user": "U123",
                        "text": "Bob, hi",
                    }
                ],
            }

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_urls({"oracle": "https://app.slack.com/client/T123/C111"})
    adapter.set_channel_urls(
        {("oracle", "yifanche-private"): "https://app.slack.com/client/T123/C222"}
    )
    adapter._api_client = lambda workspace_name: FakeApiClient()  # type: ignore[method-assign]
    adapter._workspace_page = lambda workspace_name: (_ for _ in ()).throw(AssertionError("DOM page should not be used"))  # type: ignore[method-assign]

    messages = adapter.list_root_messages("oracle", "yifanche-private")

    assert calls == [("C222", 50, None, None)]
    assert messages[0].message_ts == "1774999116.837699"
    assert messages[0].text == "Bob, hi"


def test_post_root_message_uses_api_client_path_only():
    calls = []

    class FakeApiClient:
        def chat_post_message(self, channel_id, text, thread_ts=None, reply_broadcast=False):
            calls.append((channel_id, text, thread_ts, reply_broadcast))
            return {
                "ok": True,
                "ts": "1775717794.417429",
                "message": {"ts": "1775717794.417429"},
            }

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_urls({"oracle": "https://app.slack.com/client/T123/C111"})
    adapter.set_channel_urls(
        {("oracle", "yifanche-bob"): "https://app.slack.com/client/T123/C222"}
    )
    adapter._api_client = lambda workspace_name: FakeApiClient()  # type: ignore[method-assign]

    message_ts = adapter.post_root_message("oracle", "yifanche-bob", "Bob, smoke ok")

    assert message_ts == "1775717794.417429"
    assert calls == [("C222", "Bob, smoke ok", None, False)]


def test_update_message_uses_api_client_path_only():
    calls = []

    class FakeApiClient:
        def chat_update(self, channel_id, ts, text):
            calls.append((channel_id, ts, text))
            return {
                "ok": True,
                "ts": ts,
                "message": {"ts": ts, "text": text},
            }

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_urls({"oracle": "https://app.slack.com/client/T123/C111"})
    adapter.set_channel_urls(
        {("oracle", "yifanche-bob"): "https://app.slack.com/client/T123/C222"}
    )
    adapter._api_client = lambda workspace_name: FakeApiClient()  # type: ignore[method-assign]

    adapter.update_message(
        "oracle",
        "yifanche-bob",
        "1775717794.417429",
        "bob can you do it?\n_*Bob is working on it :arrows_counterclockwise::*_",
    )

    assert calls == [
        (
            "C222",
            "1775717794.417429",
            "bob can you do it?\n_*Bob is working on it :arrows_counterclockwise::*_",
        )
    ]


def test_get_channel_id_accepts_runtime_channel_name():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )

    assert adapter.get_channel_id("oracle", "slack:C999") == "C999"


def test_list_accessible_conversation_ids_uses_api_client_path_only():
    class FakeApiClient:
        def users_conversations(self, limit=200, types=None):
            assert limit == 999
            assert types == "public_channel,private_channel,im,mpim"
            return {
                "ok": True,
                "channels": [
                    {"id": "C111"},
                    {"id": "D222"},
                    {"id": "G333"},
                    {"id": ""},
                ],
            }

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter._api_client = lambda workspace_name: FakeApiClient()  # type: ignore[method-assign]

    conversation_ids = adapter.list_accessible_conversation_ids("oracle")

    assert conversation_ids == ["C111", "D222", "G333"]


def test_list_thread_messages_uses_api_client_path_only():
    calls = []

    class FakeApiClient:
        def conversations_replies(self, channel_id, thread_ts, limit=200, oldest=None):
            calls.append((channel_id, thread_ts, limit, oldest))
            return {
                "ok": True,
                "messages": [
                    {
                        "ts": "1774999116.837699",
                        "thread_ts": "1774999116.837699",
                        "user": "U999",
                        "text": "can you say no?",
                    },
                    {
                        "ts": "1774999117.000000",
                        "thread_ts": "1774999116.837699",
                        "user": "U123",
                        "text": "bob can you do it?",
                    },
                ],
            }

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter._api_client = lambda workspace_name: FakeApiClient()  # type: ignore[method-assign]

    messages = adapter.list_thread_messages("oracle", "slack:C222", "1774999116.837699")

    assert calls == [("C222", "1774999116.837699", 200, None)]
    assert [(item.message_ts, item.author_actor_id, item.text) for item in messages] == [
        ("1774999116.837699", "U999", "can you say no?"),
        ("1774999117.000000", "U123", "bob can you do it?"),
    ]


def test_search_messages_parses_thread_ts_from_permalink():
    class FakeApiClient:
        def search_messages(self, query, count=20, page=1, sort=None, sort_dir=None):
            assert query == "bob"
            assert count == 50
            assert page == 1
            assert sort == "timestamp"
            assert sort_dir == "desc"
            return {
                "ok": True,
                "messages": {
                    "matches": [
                        {
                            "ts": "1777007562.458519",
                            "user": "U123",
                            "text": "bob please reply",
                            "channel": {"id": "C222", "name": "yifanche-bob-test"},
                            "permalink": "https://dyn.slack.com/archives/C222/p1777007562458519?thread_ts=1777006365.616769",
                        }
                    ]
                },
            }

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter._api_client = lambda workspace_name: FakeApiClient()  # type: ignore[method-assign]

    messages = adapter.search_messages(
        "oracle",
        query="bob",
        count=50,
        page=1,
        sort="timestamp",
        sort_dir="desc",
    )

    assert len(messages) == 1
    assert messages[0].channel_id == "C222"
    assert messages[0].message_ts == "1777007562.458519"
    assert messages[0].thread_ts == "1777006365.616769"


def test_post_root_message_serializes_concurrent_browser_api_calls():
    class SlowTransport:
        def __init__(self):
            self._lock = threading.Lock()
            self.inflight = 0
            self.max_inflight = 0
            self.texts = []
            self.counter = 0

        def __call__(self, origin, method_name, token, params):
            del origin
            del method_name
            del token
            with self._lock:
                self.inflight += 1
                self.max_inflight = max(self.max_inflight, self.inflight)
            try:
                time.sleep(0.05)
                with self._lock:
                    self.texts.append(params["text"])
                    self.counter += 1
                    counter = self.counter
                return {
                    "ok": True,
                    "ts": "1775717794.{0:06d}".format(counter),
                    "message": {"ts": "1775717794.{0:06d}".format(counter)},
                }
            finally:
                with self._lock:
                    self.inflight -= 1

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_api_contexts(
        {"oracle": ("token-1", "https://oracle.enterprise.slack.com")}
    )
    adapter.set_channel_urls(
        {("oracle", "yifanche-bob-test"): "https://app.slack.com/client/T123/C0AS82WLCBU"}
    )
    slow_transport = SlowTransport()
    adapter._post_slack_api_form = slow_transport  # type: ignore[method-assign]

    start = threading.Event()
    errors = []
    results = []

    def worker(text: str) -> None:
        start.wait(timeout=1)
        try:
            results.append(
                adapter.post_root_message("oracle", "yifanche-bob-test", text)
            )
        except Exception as exc:  # pragma: no cover - failure path asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("Bob, wrap-1",)),
        threading.Thread(target=worker, args=("Bob, wrap-2",)),
    ]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert len(results) == 2
    assert slow_transport.max_inflight == 1
    assert sorted(slow_transport.texts) == ["Bob, wrap-1", "Bob, wrap-2"]


def test_upload_text_snippet_uses_external_file_upload_flow():
    calls = []

    class FakeApiClient:
        def files_get_upload_url_external(self, filename, length):
            calls.append(("get", filename, length))
            return {
                "ok": True,
                "upload_url": "https://uploads.slack.test/upload",
                "file_id": "F123",
            }

        def files_complete_upload_external(self, files, channel_id=None, thread_ts=None):
            calls.append(("complete", files, channel_id, thread_ts))
            return {"ok": True, "files": [{"id": "F123"}]}

    uploads = []
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_urls({"oracle": "https://app.slack.com/client/T123/C111"})
    adapter.set_channel_urls(
        {("oracle", "yifanche-bob"): "https://app.slack.com/client/T123/C222"}
    )
    adapter._api_client = lambda workspace_name: FakeApiClient()  # type: ignore[method-assign]
    adapter._upload_external_bytes = lambda upload_url, content: uploads.append((upload_url, content))  # type: ignore[method-assign]

    file_id = adapter.upload_text_snippet(
        "oracle",
        "yifanche-bob",
        "5.0",
        "scripts/shepherd/README.md",
        "# hello\n",
    )

    assert file_id == "F123"
    assert uploads == [("https://uploads.slack.test/upload", b"# hello\n")]
    assert calls == [
        ("get", "README.md", 8),
        (
            "complete",
            [{"id": "F123", "title": "scripts/shepherd/README.md"}],
            "C222",
            "5.0",
        ),
    ]


def test_call_slack_api_rediscovers_when_seeded_workspace_auth_is_invalid():
    class FakeRequest:
        def __init__(self, url: str, post_data: str):
            self.url = url
            self.post_data = post_data

    class DiscoveryPage:
        def __init__(self):
            self.handlers = {}

        def on(self, event, handler):
            self.handlers.setdefault(event, []).append(handler)

        def reload(self, **kwargs):
            del kwargs
            for handler in self.handlers.get("request", []):
                    handler(
                        FakeRequest(
                            "https://oracle.enterprise.slack.com/api/conversations.history",
                            '------form\r\nContent-Disposition: form-data; name="token"\r\n\r\nfresh-token\r\n------form--',
                        )
                    )

        def wait_for_timeout(self, timeout_ms):
            del timeout_ms

        def remove_listener(self, event, handler):
            handlers = self.handlers.get(event, [])
            self.handlers[event] = [item for item in handlers if item is not handler]

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_api_contexts(
        {"oracle": ("stale-token", "https://oracle.enterprise.slack.com")}
    )
    discovery_page = DiscoveryPage()
    tokens = []
    adapter._workspace_page = lambda workspace_name: discovery_page  # type: ignore[method-assign]
    adapter._post_slack_api_form = lambda origin, method_name, token, params: (  # type: ignore[method-assign]
        tokens.append(token) or (
            {"ok": False, "error": "invalid_auth"}
            if token == "stale-token"
            else {"ok": True, "messages": []}
        )
    )

    payload = adapter._call_slack_api("oracle", "conversations.history", {})

    assert payload["ok"] is True
    assert tokens == ["stale-token", "fresh-token"]


def test_call_slack_api_uses_direct_http_transport_without_api_page():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_api_contexts(
        {"oracle": ("fresh-token", "https://oracle.enterprise.slack.com")}
    )
    calls = []
    adapter._api_page = lambda origin: (_ for _ in ()).throw(AssertionError("helper page should not be used"))  # type: ignore[method-assign]
    adapter._post_slack_api_form = lambda origin, method_name, token, params: (  # type: ignore[method-assign]
        calls.append((origin, method_name, token, params)) or {"ok": True, "messages": []}
    )

    payload = adapter._call_slack_api("oracle", "conversations.history", {})

    assert payload["ok"] is True
    assert calls == [
        (
            "https://oracle.enterprise.slack.com",
            "conversations.history",
            "fresh-token",
            {},
        )
    ]


def test_call_slack_api_retries_once_when_direct_http_request_times_out():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_api_contexts(
        {"oracle": ("fresh-token", "https://oracle.enterprise.slack.com")}
    )
    calls = []
    responses = [
        {"ok": False, "error": "request_timeout"},
        {"ok": True, "messages": []},
    ]
    adapter._post_slack_api_form = lambda origin, method_name, token, params: (  # type: ignore[method-assign]
        calls.append((origin, method_name, token, params)) or responses.pop(0)
    )

    payload = adapter._call_slack_api("oracle", "conversations.history", {})

    assert payload["ok"] is True
    assert calls == [
        (
            "https://oracle.enterprise.slack.com",
            "conversations.history",
            "fresh-token",
            {},
        ),
        (
            "https://oracle.enterprise.slack.com",
            "conversations.history",
            "fresh-token",
            {},
        ),
    ]

def test_api_page_marks_new_helper_page_for_cleanup():
    shared_context = FakeContext([FakePage("https://app.slack.com/client/T123/C123")])
    chromium = FakeChromium(
        browser=FakeBrowser([shared_context]),
        dedicated_context=FakeContext(),
    )
    loader, _sync = _loader_for(chromium)
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
        playwright_loader=loader,
    )

    page = adapter._api_page("https://oracle.enterprise.slack.com")

    assert page.url == "https://oracle.enterprise.slack.com/api/api.test"
    assert getattr(page, "_bob_should_close_after_use", False) is True


def test_api_page_marks_existing_helper_page_for_cleanup():
    helper_page = FakePage("https://oracle.enterprise.slack.com/api/api.test")
    shared_context = FakeContext([helper_page])
    chromium = FakeChromium(
        browser=FakeBrowser([shared_context]),
        dedicated_context=FakeContext(),
    )
    loader, _sync = _loader_for(chromium)
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
        playwright_loader=loader,
    )

    page = adapter._api_page("https://oracle.enterprise.slack.com")

    assert page is helper_page
    assert getattr(page, "_bob_should_close_after_use", False) is True


def test_api_page_creates_non_focused_helper_target_in_shared_browser():
    shared_context = FakeContext([FakePage("https://app.slack.com/client/T123/C123")])
    browser = FakeBrowser([shared_context])

    def on_create_target(params):
        shared_context.pages.append(FakePage(params["url"]))

    browser.browser_cdp_session = FakeBrowserCDPSession(on_create_target=on_create_target)
    chromium = FakeChromium(
        browser=browser,
        dedicated_context=FakeContext(),
    )
    loader, _sync = _loader_for(chromium)
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
        playwright_loader=loader,
    )

    page = adapter._api_page("https://oracle.enterprise.slack.com")

    assert page.url == "https://oracle.enterprise.slack.com/api/api.test"
    assert shared_context.new_page_calls == 0
    assert browser.browser_cdp_session.calls == [
        (
            "Target.createTarget",
            {
                "url": "https://oracle.enterprise.slack.com/api/api.test",
                "background": False,
                "focus": False,
            },
        )
    ]
    assert browser.browser_cdp_session.detached is True


def test_api_page_does_not_mark_existing_non_helper_same_origin_page_for_cleanup():
    workspace_page = FakePage("https://oracle.enterprise.slack.com/messages")
    shared_context = FakeContext([workspace_page])
    chromium = FakeChromium(
        browser=FakeBrowser([shared_context]),
        dedicated_context=FakeContext(),
    )
    loader, _sync = _loader_for(chromium)
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
        playwright_loader=loader,
    )

    page = adapter._api_page("https://oracle.enterprise.slack.com")

    assert page is workspace_page
    assert getattr(page, "_bob_should_close_after_use", False) is False
