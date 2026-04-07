from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..models import (
    DEDICATED_BROWSER_MODE,
    DEFAULT_SLACK_SIGNIN_URL,
    SHARED_BROWSER_MODE,
)
from .browser import SlackRootMessage, SlackThreadReplyMessage


def _load_sync_playwright():
    from playwright.sync_api import sync_playwright

    return sync_playwright


class PlaywrightSlackAdapter:
    def __init__(
        self,
        cdp_url: str,
        browser_mode: str = DEDICATED_BROWSER_MODE,
        slack_signin_url: str = DEFAULT_SLACK_SIGNIN_URL,
        chrome_executable_path: Optional[str] = None,
        browser_user_data_dir: Optional[str] = None,
        playwright_loader: Any = _load_sync_playwright,
    ):
        if browser_mode not in (SHARED_BROWSER_MODE, DEDICATED_BROWSER_MODE):
            raise ValueError("browser_mode must be shared_browser or dedicated_browser.")
        self.cdp_url = cdp_url
        self.browser_mode = browser_mode
        self.slack_signin_url = slack_signin_url
        self.chrome_executable_path = chrome_executable_path
        self.browser_user_data_dir = browser_user_data_dir or ""
        self._playwright_loader = playwright_loader
        self._playwright: Optional[Any] = None
        self._browser: Optional[Any] = None
        self._context: Optional[Any] = None
        self._workspace_urls: Dict[str, str] = {}
        self._channel_urls: Dict[Tuple[str, str], str] = {}
        self._api_sessions: Dict[str, Tuple[str, str]] = {}
        self._workspace_api_contexts: Dict[str, Tuple[str, str]] = {}

    def set_workspace_urls(self, workspace_urls: Dict[str, str]) -> None:
        self._workspace_urls = dict(workspace_urls)

    def set_channel_urls(self, channel_urls: Dict[Tuple[str, str], str]) -> None:
        self._channel_urls = dict(channel_urls)

    def set_workspace_api_contexts(self, workspace_api_contexts: Dict[str, Tuple[str, str]]) -> None:
        self._workspace_api_contexts = dict(workspace_api_contexts)

    def connect(self) -> Any:
        if self.browser_mode == SHARED_BROWSER_MODE and self._browser is not None:
            return self._browser
        if self.browser_mode == DEDICATED_BROWSER_MODE and self._context is not None:
            return self._context

        sync_playwright = self._playwright_loader()
        self._playwright = sync_playwright().start()
        if self.browser_mode == SHARED_BROWSER_MODE:
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
            return self._browser

        launch_kwargs = {"headless": False}
        if self.chrome_executable_path:
            launch_kwargs["executable_path"] = self.chrome_executable_path
        self._context = self._playwright.chromium.launch_persistent_context(
            self.browser_user_data_dir,
            **launch_kwargs,
        )
        return self._context

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def select_bob_tab(self, workspace_url_prefix: Optional[str]) -> Any:
        target_url = workspace_url_prefix or self.slack_signin_url
        runtime = self.connect()
        contexts = self._contexts(runtime)
        for context in contexts:
            for page in context.pages:
                if page.url.startswith(target_url):
                    return page

        if contexts:
            page = contexts[0].new_page()
        elif self.browser_mode == SHARED_BROWSER_MODE:
            page = runtime.new_context().new_page()
        else:
            page = runtime.new_page()
        page.goto(target_url)
        return page

    def _contexts(self, runtime: Any) -> list[Any]:
        if self.browser_mode == SHARED_BROWSER_MODE:
            return list(runtime.contexts)
        return [runtime]

    def _channel_sidebar_key(self, channel_name: str) -> str:
        return channel_name.strip().lower().replace(" ", "-")

    def _parse_workspace_target(self, workspace_url: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        if not workspace_url:
            return None, None
        prefix = "https://app.slack.com/client/"
        if not workspace_url.startswith(prefix):
            return None, None
        suffix = workspace_url[len(prefix):].split("?", 1)[0].strip("/")
        parts = suffix.split("/")
        if len(parts) < 2:
            return None, None
        return parts[0], parts[1]

    def _thread_route_url(
        self,
        workspace_url: Optional[str],
        channel_name: str,
        thread_ts: str,
    ) -> Optional[str]:
        del channel_name
        team_id, channel_id = self._parse_workspace_target(workspace_url)
        if not team_id or not channel_id:
            return None
        return "https://app.slack.com/client/{0}/{1}/thread/{1}-{2}".format(
            team_id,
            channel_id,
            thread_ts,
        )

    def _workspace_page(self, workspace_name: str) -> Any:
        return self.select_bob_tab(self._workspace_urls.get(workspace_name))

    def _channel_url(self, workspace_name: str, channel_name: str) -> Optional[str]:
        cached = self._channel_urls.get((workspace_name, channel_name))
        if cached:
            return cached
        workspace_url = self._workspace_urls.get(workspace_name)
        team_id, _channel_id = self._parse_workspace_target(workspace_url)
        if not workspace_url or not team_id:
            raise RuntimeError(
                "Could not determine Slack workspace route for workspace {0}.".format(workspace_name)
            )
        resolved_channel_id = self._resolve_sidebar_channel_id(workspace_name, channel_name)
        resolved_url = "https://app.slack.com/client/{0}/{1}".format(team_id, resolved_channel_id)
        self._channel_urls[(workspace_name, channel_name)] = resolved_url
        return resolved_url

    def _resolve_sidebar_channel_id(self, workspace_name: str, channel_name: str) -> str:
        page = self._workspace_page(workspace_name)
        selector = '[data-qa="channel_sidebar_name_{0}"]'.format(
            self._channel_sidebar_key(channel_name)
        )
        channel_id = page.evaluate(
            """
({ selector }) => {
  const nameNode = document.querySelector(selector);
  if (!nameNode) {
    return null;
  }
  const channelNode = nameNode.closest('[data-qa-channel-sidebar-channel-id]');
  if (channelNode) {
    return channelNode.getAttribute('data-qa-channel-sidebar-channel-id');
  }
  const itemNode = nameNode.closest('[data-item-key], [id]');
  if (!itemNode) {
    return null;
  }
  return itemNode.getAttribute('data-item-key') || itemNode.id || null;
}
            """,
            {"selector": selector},
        )
        if isinstance(channel_id, str) and channel_id.strip():
            return channel_id.strip()
        raise RuntimeError(
            "Could not resolve Slack channel id from the rendered sidebar for channel {0}.".format(
                channel_name
            )
        )

    def _api_page(self, origin: str) -> Any:
        runtime = self.connect()
        contexts = self._contexts(runtime)
        api_test_url = origin.rstrip("/") + "/api/api.test"
        for context in contexts:
            for page in context.pages:
                if page.url.startswith(origin):
                    return page

        if contexts:
            page = contexts[0].new_page()
        elif self.browser_mode == SHARED_BROWSER_MODE:
            page = runtime.new_context().new_page()
        else:
            page = runtime.new_page()
        page.goto(api_test_url, wait_until="commit", timeout=15000)
        page.wait_for_timeout(1000)
        return page

    def _extract_api_session_info(self, url: str, post_data: str) -> Tuple[Optional[str], Optional[str]]:
        origin_match = re.match(r"^(https://[^/]+)/api/", url)
        token_match = re.search(r'name="token"\r?\n\r?\n([^\r\n]+)', post_data or "")
        origin = origin_match.group(1) if origin_match else None
        token = token_match.group(1) if token_match else None
        return token, origin

    def _discover_api_session(self, workspace_name: str) -> Tuple[str, str]:
        if workspace_name in self._api_sessions:
            return self._api_sessions[workspace_name]
        if workspace_name in self._workspace_api_contexts:
            token, origin = self._workspace_api_contexts[workspace_name]
            self._api_sessions[workspace_name] = (token, origin)
            return token, origin

        page = self._workspace_page(workspace_name)
        seen: Dict[str, str] = {}

        def on_request(request: Any) -> None:
            if "/api/" not in request.url:
                return
            token, origin = self._extract_api_session_info(request.url, request.post_data or "")
            if token and origin:
                seen["token"] = token
                seen["origin"] = origin

        page.on("request", on_request)
        try:
            page.reload(wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(4000)
        finally:
            page.remove_listener("request", on_request)

        token = seen.get("token")
        origin = seen.get("origin")
        if not token or not origin:
            raise RuntimeError("Could not discover Slack Web API session token from the browser page.")
        self._api_sessions[workspace_name] = (token, origin)
        return token, origin

    def discover_api_session(self, workspace_name: str) -> Tuple[str, str]:
        return self._discover_api_session(workspace_name)

    def _call_slack_api(
        self,
        workspace_name: str,
        method_name: str,
        params: Dict[str, Any],
        retry_on_auth_error: bool = True,
    ) -> Dict[str, Any]:
        token, origin = self._discover_api_session(workspace_name)
        page = self._api_page(origin)
        payload = page.evaluate(
            """
async ({origin, methodName, token, params}) => {
  const form = new FormData();
  form.append('token', token);
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) continue;
    form.append(key, String(value));
  }
  const resp = await fetch(origin + '/api/' + methodName, {
    method: 'POST',
    body: form,
    credentials: 'include'
  });
  const text = await resp.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch (err) {
    json = { ok: false, error: 'non_json_response', raw: text };
  }
  return { status: resp.status, body: json };
}
            """,
            {
                "origin": origin,
                "methodName": method_name,
                "token": token,
                "params": params,
            },
        )
        body = payload.get("body") if isinstance(payload, dict) else None
        if (
            retry_on_auth_error
            and isinstance(body, dict)
            and body.get("error") in {"not_authed", "invalid_auth"}
        ):
            self._api_sessions.pop(workspace_name, None)
            return self._call_slack_api(
                workspace_name=workspace_name,
                method_name=method_name,
                params=params,
                retry_on_auth_error=False,
            )
        if not isinstance(body, dict):
            raise RuntimeError("Slack API call returned an unexpected payload shape.")
        return body

    def _root_messages_from_api_payload(
        self,
        workspace_name: str,
        channel_name: str,
        payload: Dict[str, Any],
    ) -> List[SlackRootMessage]:
        messages = []
        for item in payload.get("messages", []):
            if not isinstance(item, dict):
                continue
            message_ts = str(item.get("ts") or "").strip()
            if not message_ts:
                continue
            text = str(item.get("text") or "")
            author_actor_id = str(item.get("user") or "")
            thread_ts = str(item.get("thread_ts") or message_ts)
            messages.append(
                SlackRootMessage(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    message_ts=message_ts,
                    author_actor_id=author_actor_id,
                    text=text,
                )
            )
        messages.sort(key=lambda item: float(item.message_ts))
        return messages

    def _thread_replies_from_api_payload(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        payload: Dict[str, Any],
    ) -> List[SlackThreadReplyMessage]:
        if payload.get("ok") is False and payload.get("error") == "thread_not_found":
            return []
        replies: List[SlackThreadReplyMessage] = []
        for item in payload.get("messages", []):
            if not isinstance(item, dict):
                continue
            message_ts = str(item.get("ts") or "").strip()
            if not message_ts or message_ts == thread_ts:
                continue
            replies.append(
                SlackThreadReplyMessage(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    message_ts=message_ts,
                    author_actor_id=str(item.get("user") or ""),
                    text=str(item.get("text") or ""),
                )
            )
        replies.sort(key=lambda item: float(item.message_ts))
        return replies

    def _root_messages_from_payload(
        self,
        workspace_name: str,
        channel_name: str,
        payload: List[Dict[str, Any]],
    ) -> List[SlackRootMessage]:
        messages: List[SlackRootMessage] = []
        last_author_actor_id = ""
        for item in payload:
            message_ts = str(item.get("message_ts") or "").strip()
            if not message_ts:
                continue
            author_actor_id = str(item.get("author_actor_id") or "").strip() or last_author_actor_id
            if author_actor_id:
                last_author_actor_id = author_actor_id
            messages.append(
                SlackRootMessage(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=str(item.get("thread_ts") or message_ts),
                    message_ts=message_ts,
                    author_actor_id=author_actor_id,
                    text=str(item.get("text") or ""),
                )
            )
        return messages

    def _thread_replies_from_payload(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        payload: List[Dict[str, Any]],
    ) -> List[SlackThreadReplyMessage]:
        replies: List[SlackThreadReplyMessage] = []
        last_author_actor_id = ""
        for item in payload:
            message_ts = str(item.get("message_ts") or "").strip()
            if not message_ts:
                continue
            author_actor_id = str(item.get("author_actor_id") or "").strip() or last_author_actor_id
            if author_actor_id:
                last_author_actor_id = author_actor_id
            replies.append(
                SlackThreadReplyMessage(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    message_ts=message_ts,
                    author_actor_id=author_actor_id,
                    text=str(item.get("text") or ""),
                )
            )
        return replies

    def _ensure_channel_page(self, workspace_name: str, channel_name: str) -> Any:
        page = self.select_bob_tab(self._channel_url(workspace_name, channel_name))
        if channel_name not in page.title():
            selector = '[data-qa="channel_sidebar_name_{0}"]'.format(
                self._channel_sidebar_key(channel_name)
            )
            sidebar_item = page.locator(selector).first
            if sidebar_item.count():
                sidebar_item.click()
                page.wait_for_timeout(1000)
        return page

    def _thread_pane_matches(self, page: Any, thread_ts: str) -> bool:
        return bool(
            page.evaluate(
                """
({ threadTs }) => {
  const pane = document.querySelector('[data-qa="threads_flexpane"]');
  if (!pane) {
    return false;
  }
  return !!pane.querySelector('[data-qa="message_container"][data-msg-ts="' + threadTs + '"]');
}
                """,
                {"threadTs": thread_ts},
            )
        )

    def _open_thread_pane(self, workspace_name: str, channel_name: str, thread_ts: str) -> Any:
        page = self._ensure_channel_page(workspace_name, channel_name)
        if self._thread_pane_matches(page, thread_ts):
            return page

        container = page.locator(
            '[data-qa="message_pane"] [data-qa="message_container"][data-msg-ts="{0}"]'.format(
                thread_ts
            )
        ).first
        if not container.count():
            thread_url = self._thread_route_url(
                self._workspace_urls.get(workspace_name),
                channel_name,
                thread_ts,
            )
            if thread_url:
                page.goto(thread_url, wait_until="commit", timeout=5000)
                page.wait_for_timeout(1500)
                if self._thread_pane_matches(page, thread_ts):
                    return page
            raise RuntimeError("Could not find Slack message for thread {0}".format(thread_ts))
        container.scroll_into_view_if_needed()
        container.hover()
        page.wait_for_timeout(500)

        open_thread = container.locator('[data-qa="reply_bar_view_thread"]').first
        if open_thread.count():
            open_thread.click()
        else:
            start_thread = container.locator('[data-qa="start_thread"]').first
            if not start_thread.count():
                raise RuntimeError("Could not find a thread opener for message {0}".format(thread_ts))
            start_thread.click()

        page.wait_for_function(
            """
({ threadTs }) => {
  const pane = document.querySelector('[data-qa="threads_flexpane"]');
  if (!pane) {
    return false;
  }
  return !!pane.querySelector('[data-qa="message_container"][data-msg-ts="' + threadTs + '"]');
}
            """,
            arg={"threadTs": thread_ts},
        )
        return page

    def post_thread_reply(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        text: str,
    ) -> str:
        _team_id, channel_id = self._parse_workspace_target(self._channel_url(workspace_name, channel_name))
        if not channel_id:
            raise RuntimeError("Could not determine Slack channel id for workspace {0}".format(workspace_name))
        payload = self._call_slack_api(
            workspace_name=workspace_name,
            method_name="chat.postMessage",
            params={
                "channel": channel_id,
                "thread_ts": thread_ts,
                "reply_broadcast": "false",
                "text": text,
            },
        )
        if not payload.get("ok"):
            raise RuntimeError("Slack API chat.postMessage failed: {0}".format(payload.get("error")))
        latest_ts = str(payload.get("ts") or payload.get("message", {}).get("ts") or "")
        if not latest_ts:
            raise RuntimeError("Slack API post succeeded but no reply timestamp was returned.")
        return str(latest_ts)

    def list_root_messages(
        self,
        workspace_name: str,
        channel_name: str,
    ) -> list[SlackRootMessage]:
        _team_id, channel_id = self._parse_workspace_target(self._channel_url(workspace_name, channel_name))
        if not channel_id:
            raise RuntimeError("Could not determine Slack channel id for workspace {0}".format(workspace_name))
        payload = self._call_slack_api(
            workspace_name=workspace_name,
            method_name="conversations.history",
            params={
                "channel": channel_id,
                "limit": 50,
            },
        )
        if not payload.get("ok"):
            raise RuntimeError("Slack API conversations.history failed: {0}".format(payload.get("error")))
        return self._root_messages_from_api_payload(
            workspace_name=workspace_name,
            channel_name=channel_name,
            payload=payload,
        )

    def list_thread_replies(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> list[SlackThreadReplyMessage]:
        _team_id, channel_id = self._parse_workspace_target(self._channel_url(workspace_name, channel_name))
        if not channel_id:
            raise RuntimeError("Could not determine Slack channel id for workspace {0}".format(workspace_name))
        payload = self._call_slack_api(
            workspace_name=workspace_name,
            method_name="conversations.replies",
            params={
                "channel": channel_id,
                "ts": thread_ts,
                "limit": 200,
            },
        )
        if payload.get("ok") is False and payload.get("error") == "thread_not_found":
            return []
        if not payload.get("ok"):
            raise RuntimeError("Slack API conversations.replies failed: {0}".format(payload.get("error")))
        return self._thread_replies_from_api_payload(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
            payload=payload,
        )

    def delete_message(
        self,
        workspace_name: str,
        channel_name: str,
        message_ts: str,
    ) -> None:
        raise NotImplementedError("Slack DOM deletion is not implemented yet.")

    def find_existing_bob_messages(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
    ) -> list[str]:
        replies = self.list_thread_replies(
            workspace_name=workspace_name,
            channel_name=channel_name,
            thread_ts=thread_ts,
        )
        return [reply.text for reply in replies if reply.text]
