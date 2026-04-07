from personal_slack_agent.slack.playwright_adapter import PlaywrightSlackAdapter


class FakePage:
    def __init__(self, url: str = ""):
        self.url = url
        self.goto_calls = []

    def goto(self, url: str) -> None:
        self.goto_calls.append(url)
        self.url = url


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
        self.started = False

    def start(self):
        self.started = True
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


def test_shared_browser_opens_workspace_url_when_tab_missing():
    shared_context = FakeContext([])
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

    assert shared_context.new_page_calls == 1
    assert page.goto_calls == ["https://example.enterprise.slack.com/"]


def test_shared_browser_falls_back_to_global_signin_url_when_workspace_url_missing():
    shared_context = FakeContext([])
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

    page = adapter.select_bob_tab(None)

    assert page.goto_calls == ["https://slack.com/signin?entry_point=nav_menu#/signin"]


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


def test_close_stops_playwright_and_closes_active_browser_or_context():
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

    assert shared_browser.closed is True
    assert sync_obj.playwright_obj.stopped is True


def test_root_messages_from_payload_filters_missing_message_ts():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )

    messages = adapter._root_messages_from_payload(
        workspace_name="oracle",
        channel_name="yifanche-private",
        payload=[
            {
                "thread_ts": "1774999116.837699",
                "message_ts": "1774999116.837699",
                "author_actor_id": "U123",
                "text": "Bob, hi",
            },
            {
                "thread_ts": "",
                "message_ts": "",
                "author_actor_id": "U123",
                "text": "ignored",
            },
        ],
    )

    assert len(messages) == 1
    assert messages[0].message_ts == "1774999116.837699"
    assert messages[0].thread_ts == "1774999116.837699"
    assert messages[0].text == "Bob, hi"


def test_thread_replies_from_payload_uses_parent_thread_ts():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )

    replies = adapter._thread_replies_from_payload(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1774999116.837699",
        payload=[
            {
                "message_ts": "1774999349.509569",
                "author_actor_id": "U123",
                "text": "codex Bob: hi",
            }
        ],
    )

    assert len(replies) == 1
    assert replies[0].thread_ts == "1774999116.837699"
    assert replies[0].message_ts == "1774999349.509569"
    assert replies[0].text == "codex Bob: hi"


def test_channel_sidebar_key_normalizes_spaces_to_dashes():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )

    assert adapter._channel_sidebar_key("My Private Channel") == "my-private-channel"


def test_root_messages_from_payload_keeps_sender_on_compact_followup_messages():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )

    messages = adapter._root_messages_from_payload(
        workspace_name="oracle",
        channel_name="yifanche-private",
        payload=[
            {
                "thread_ts": "1.1",
                "message_ts": "1.1",
                "author_actor_id": "U123",
                "text": "first",
            },
            {
                "thread_ts": "1.2",
                "message_ts": "1.2",
                "author_actor_id": "",
                "text": "second",
            },
        ],
    )

    assert messages[1].author_actor_id == "U123"


def test_thread_replies_from_payload_keeps_sender_on_compact_followup_messages():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )

    replies = adapter._thread_replies_from_payload(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1774999116.837699",
        payload=[
            {
                "message_ts": "2.1",
                "author_actor_id": "U123",
                "text": "reply one",
            },
            {
                "message_ts": "2.2",
                "author_actor_id": "",
                "text": "reply two",
            },
        ],
    )

    assert replies[1].author_actor_id == "U123"


def test_thread_route_url_uses_workspace_channel_and_exact_thread_ts():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )

    thread_url = adapter._thread_route_url(
        workspace_url="https://app.slack.com/client/T12345678/C12345678",
        channel_name="yifanche-private",
        thread_ts="1774999116.837699",
    )

    assert thread_url == "https://app.slack.com/client/T12345678/C12345678/thread/C12345678-1774999116.837699"


def test_channel_url_uses_legacy_seeded_route_when_provided():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_urls({"workspace": "https://app.slack.com/client/T123/C111"})
    adapter.set_channel_urls({("workspace", "other-channel"): "https://app.slack.com/client/T123/C222"})

    assert adapter._channel_url("workspace", "other-channel") == "https://app.slack.com/client/T123/C222"


def test_channel_url_resolves_sidebar_channel_id_without_click_and_caches_it():
    class FakeWorkspacePage:
        def __init__(self):
            self.calls = 0

        def evaluate(self, script, arg):
            del script
            self.calls += 1
            assert arg["selector"] == '[data-qa="channel_sidebar_name_yifanche-bob"]'
            return "C0AQT4S6QHM"

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    page = FakeWorkspacePage()
    adapter.set_workspace_urls({"oracle": "https://app.slack.com/client/E655JKQRX/C03J3TXQBSP"})
    adapter._workspace_page = lambda workspace_name: page  # type: ignore[method-assign]

    first = adapter._channel_url("oracle", "yifanche-bob")
    second = adapter._channel_url("oracle", "yifanche-bob")

    assert first == "https://app.slack.com/client/E655JKQRX/C0AQT4S6QHM"
    assert second == "https://app.slack.com/client/E655JKQRX/C0AQT4S6QHM"
    assert page.calls == 1


def test_channel_url_raises_when_sidebar_channel_is_not_rendered():
    class FakeWorkspacePage:
        def evaluate(self, script, arg):
            del script, arg
            return None

    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )
    adapter.set_workspace_urls({"oracle": "https://app.slack.com/client/E655JKQRX/C03J3TXQBSP"})
    adapter._workspace_page = lambda workspace_name: FakeWorkspacePage()  # type: ignore[method-assign]

    try:
        adapter._channel_url("oracle", "missing-channel")
    except RuntimeError as exc:
        assert "missing-channel" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected RuntimeError when sidebar channel id cannot be resolved.")


def test_extract_api_session_info_reads_token_and_origin_from_request():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )

    token, origin = adapter._extract_api_session_info(
        "https://example.enterprise.slack.com/api/conversations.history?_x_id=abc",
        "------WebKitFormBoundary\r\nContent-Disposition: form-data; name=\"token\"\r\n\r\nxoxc-12345\r\n------WebKitFormBoundary--\r\n",
    )

    assert token == "xoxc-12345"
    assert origin == "https://example.enterprise.slack.com"


def test_root_messages_from_api_payload_maps_thread_and_author_fields():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )

    messages = adapter._root_messages_from_api_payload(
        workspace_name="oracle",
        channel_name="yifanche-private",
        payload={
            "messages": [
                {
                    "ts": "1.1",
                    "thread_ts": "1.1",
                    "user": "U123",
                    "text": "Bob, hi",
                },
                {
                    "ts": "1.2",
                    "user": "U123",
                    "text": "second",
                },
            ]
        },
    )

    assert [item.thread_ts for item in messages] == ["1.1", "1.2"]
    assert messages[0].author_actor_id == "U123"
    assert messages[1].text == "second"


def test_thread_replies_from_api_payload_skips_root_message():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )

    replies = adapter._thread_replies_from_api_payload(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1.1",
        payload={
            "messages": [
                {"ts": "1.1", "thread_ts": "1.1", "user": "U123", "text": "root"},
                {"ts": "1.2", "thread_ts": "1.1", "user": "U123", "text": "reply"},
            ]
        },
    )

    assert len(replies) == 1
    assert replies[0].message_ts == "1.2"
    assert replies[0].text == "reply"


def test_thread_replies_from_api_payload_returns_empty_for_thread_not_found():
    adapter = PlaywrightSlackAdapter(
        browser_mode="shared_browser",
        cdp_url="http://127.0.0.1:9222",
        slack_signin_url="https://slack.com/signin?entry_point=nav_menu#/signin",
    )

    replies = adapter._thread_replies_from_api_payload(
        workspace_name="oracle",
        channel_name="yifanche-private",
        thread_ts="1.1",
        payload={"ok": False, "error": "thread_not_found"},
    )

    assert replies == []
