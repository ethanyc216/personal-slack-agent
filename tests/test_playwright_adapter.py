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

    def new_context(self):
        self.new_context_calls += 1
        context = FakeContext()
        self.contexts.append(context)
        return context

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser, dedicated_context: FakeContext):
        self.browser = browser
        self.dedicated_context = dedicated_context
        self.connect_over_cdp_calls = []
        self.launch_persistent_context_calls = []

    def connect_over_cdp(self, cdp_url: str):
        self.connect_over_cdp_calls.append(cdp_url)
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
    assert chromium.connect_over_cdp_calls == ["http://127.0.0.1:9222"]
    assert shared_context.new_page_calls == 0


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

    class ApiPage:
        def __init__(self):
            self.tokens = []

        def evaluate(self, script, payload):
            del script
            self.tokens.append(payload["token"])
            if payload["token"] == "stale-token":
                return {"status": 200, "body": {"ok": False, "error": "invalid_auth"}}
            return {"status": 200, "body": {"ok": True, "messages": []}}

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_api_contexts(
        {"oracle": ("stale-token", "https://oracle.enterprise.slack.com")}
    )
    discovery_page = DiscoveryPage()
    api_page = ApiPage()
    adapter._workspace_page = lambda workspace_name: discovery_page  # type: ignore[method-assign]
    adapter._api_page = lambda origin: api_page  # type: ignore[method-assign]

    payload = adapter._call_slack_api("oracle", "conversations.history", {})

    assert payload["ok"] is True
    assert api_page.tokens == ["stale-token", "fresh-token"]


def test_call_slack_api_retries_once_when_api_page_is_closed():
    class ClosedPage:
        def evaluate(self, script, payload):
            del script
            del payload
            raise PlaywrightError("Page.evaluate: Target page, context or browser has been closed")

    class HealthyPage:
        def __init__(self):
            self.calls = []

        def evaluate(self, script, payload):
            del script
            self.calls.append(payload["token"])
            return {"status": 200, "body": {"ok": True, "messages": []}}

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_api_contexts(
        {"oracle": ("fresh-token", "https://oracle.enterprise.slack.com")}
    )
    healthy_page = HealthyPage()
    pages = [ClosedPage(), healthy_page]
    close_calls = []

    adapter._api_page = lambda origin: pages.pop(0)  # type: ignore[method-assign]
    adapter.close = lambda: close_calls.append("closed")  # type: ignore[method-assign]

    payload = adapter._call_slack_api("oracle", "conversations.history", {})

    assert payload["ok"] is True
    assert close_calls == ["closed"]
    assert healthy_page.calls == ["fresh-token"]


def test_call_slack_api_retries_once_when_api_page_closes_mid_call():
    class ApiPage:
        def __init__(self, should_close: bool):
            self.should_close = should_close
            self.tokens = []

        def evaluate(self, script, payload):
            del script
            self.tokens.append(payload["token"])
            if self.should_close:
                raise TargetClosedError("Target page, context or browser has been closed")
            return {"status": 200, "body": {"ok": True, "messages": []}}

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_api_contexts(
        {"oracle": ("token-1", "https://oracle.enterprise.slack.com")}
    )
    pages = [ApiPage(should_close=True), ApiPage(should_close=False)]
    adapter._api_page = lambda origin: pages.pop(0)  # type: ignore[method-assign]

    payload = adapter._call_slack_api("oracle", "conversations.history", {})

    assert payload["ok"] is True
    assert len(pages) == 0
