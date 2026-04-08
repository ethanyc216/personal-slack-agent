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
