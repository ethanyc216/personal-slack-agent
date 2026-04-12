from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.error
import urllib.request
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

from ..models import (
    DEDICATED_BROWSER_MODE,
    DEFAULT_SLACK_SIGNIN_URL,
    SHARED_BROWSER_MODE,
)
from .api_client import SlackApiClient
from .auth import SlackApiSession, extract_api_session_from_request
from .browser import SlackRootMessage, SlackThreadReplyMessage


def _load_sync_playwright():
    from playwright.sync_api import sync_playwright

    return sync_playwright


class PlaywrightSlackAdapter:
    _SLACK_API_TIMEOUT_MS = 15000

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
        self._realtime_subscriptions: Dict[str, Callable[[Any], None]] = {}

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
        if self._browser is not None and self.browser_mode != SHARED_BROWSER_MODE:
            self._browser.close()
        if self._context is not None:
            self._context.close()
            self._context = None
        self._browser = None
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

    def get_channel_id(self, workspace_name: str, channel_name: str) -> str:
        _team_id, channel_id = self._parse_workspace_target(
            self._channel_url(workspace_name, channel_name)
        )
        if not channel_id:
            raise RuntimeError(
                "Could not determine Slack channel id for workspace {0}.".format(workspace_name)
            )
        return channel_id

    def subscribe_to_realtime_frames(
        self,
        workspace_name: str,
        on_frame,
        on_disconnect,
    ) -> None:
        if workspace_name in self._realtime_subscriptions:
            return
        page = self._workspace_page(workspace_name)

        def handle_websocket(websocket: Any) -> None:
            if not self._is_slack_socket_url(str(getattr(websocket, "url", ""))):
                return

            websocket.on(
                "framereceived",
                lambda payload: on_frame(
                    payload.decode("utf-8", errors="replace")
                    if isinstance(payload, bytes)
                    else str(payload)
                ),
            )
            websocket.on("close", lambda _payload: on_disconnect())
            websocket.on("socketerror", lambda _payload: on_disconnect())

        page.on("websocket", handle_websocket)
        page.reload(wait_until="domcontentloaded", timeout=15000)
        self._realtime_subscriptions[workspace_name] = handle_websocket

    def post_thread_reply(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        text: str,
    ) -> str:
        payload = self._api_client(workspace_name).chat_post_message(
            channel_id=self.get_channel_id(workspace_name, channel_name),
            thread_ts=thread_ts,
            text=text,
            reply_broadcast=False,
        )
        if not payload.get("ok"):
            raise RuntimeError("Slack API chat.postMessage failed: {0}".format(payload.get("error")))
        latest_ts = str(payload.get("ts") or payload.get("message", {}).get("ts") or "")
        if not latest_ts:
            raise RuntimeError("Slack API post succeeded but no reply timestamp was returned.")
        return latest_ts

    def post_root_message(
        self,
        workspace_name: str,
        channel_name: str,
        text: str,
    ) -> str:
        payload = self._api_client(workspace_name).chat_post_message(
            channel_id=self.get_channel_id(workspace_name, channel_name),
            text=text,
            thread_ts=None,
            reply_broadcast=False,
        )
        if not payload.get("ok"):
            raise RuntimeError("Slack API chat.postMessage failed: {0}".format(payload.get("error")))
        latest_ts = str(payload.get("ts") or payload.get("message", {}).get("ts") or "")
        if not latest_ts:
            raise RuntimeError("Slack API post succeeded but no message timestamp was returned.")
        return latest_ts

    def upload_text_snippet(
        self,
        workspace_name: str,
        channel_name: str,
        thread_ts: str,
        filename: str,
        content: str,
    ) -> str:
        upload_name = self._snippet_filename(filename)
        payload = self._api_client(workspace_name).files_get_upload_url_external(
            filename=upload_name,
            length=len(content.encode("utf-8")),
        )
        if not payload.get("ok"):
            raise RuntimeError(
                "Slack API files.getUploadURLExternal failed: {0}".format(payload.get("error"))
            )
        upload_url = str(payload.get("upload_url") or "")
        file_id = str(payload.get("file_id") or "")
        if not upload_url or not file_id:
            raise RuntimeError("Slack upload URL flow returned an incomplete payload.")
        self._upload_external_bytes(upload_url, content.encode("utf-8"))
        completion = self._api_client(workspace_name).files_complete_upload_external(
            files=[{"id": file_id, "title": filename}],
            channel_id=self.get_channel_id(workspace_name, channel_name),
            thread_ts=thread_ts,
        )
        if not completion.get("ok"):
            raise RuntimeError(
                "Slack API files.completeUploadExternal failed: {0}".format(
                    completion.get("error")
                )
            )
        return file_id

    def list_root_messages(
        self,
        workspace_name: str,
        channel_name: str,
        oldest: str | None = None,
        latest: str | None = None,
        limit: int = 50,
    ) -> list[SlackRootMessage]:
        payload = self._api_client(workspace_name).conversations_history(
            channel_id=self.get_channel_id(workspace_name, channel_name),
            limit=limit,
            oldest=oldest,
            latest=latest,
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
        oldest: str | None = None,
        limit: int = 200,
    ) -> list[SlackThreadReplyMessage]:
        payload = self._api_client(workspace_name).conversations_replies(
            channel_id=self.get_channel_id(workspace_name, channel_name),
            thread_ts=thread_ts,
            limit=limit,
            oldest=oldest,
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
        del workspace_name
        del channel_name
        del message_ts
        raise NotImplementedError("Slack message deletion is not implemented yet.")

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

    def _workspace_page(self, workspace_name: str) -> Any:
        return self.select_bob_tab(self._workspace_urls.get(workspace_name))

    def _channel_url(self, workspace_name: str, channel_name: str) -> str:
        cached = self._channel_urls.get((workspace_name, channel_name))
        if cached:
            return cached
        workspace_url = self._workspace_urls.get(workspace_name)
        team_id, _channel_id = self._parse_workspace_target(workspace_url)
        if not workspace_url or not team_id:
            raise RuntimeError(
                "Could not determine Slack workspace route for workspace {0}.".format(workspace_name)
            )
        resolved_channel_id = self._resolve_channel_id(workspace_name, channel_name)
        resolved_url = "https://app.slack.com/client/{0}/{1}".format(team_id, resolved_channel_id)
        self._channel_urls[(workspace_name, channel_name)] = resolved_url
        return resolved_url

    def _snippet_filename(self, filename: str) -> str:
        candidate = PurePosixPath(filename).name
        if candidate:
            return candidate
        return filename.replace("/", "__")

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

    def _resolve_channel_id(self, workspace_name: str, channel_name: str) -> str:
        try:
            return self._resolve_sidebar_channel_id(workspace_name, channel_name)
        except RuntimeError:
            pass

        api_client = self._api_client(workspace_name)
        channel_id = self._channel_id_from_conversations_payload(
            api_client.users_conversations(
                limit=999,
                types="public_channel,private_channel",
            ),
            channel_name,
        )
        if channel_id is not None:
            return channel_id

        channel_id = self._channel_id_from_conversations_payload(
            api_client.conversations_list(
                limit=999,
                types="public_channel,private_channel",
                exclude_archived=True,
            ),
            channel_name,
        )
        if channel_id is not None:
            return channel_id

        channel_id = self._channel_id_from_search(
            payload=api_client.search_messages(
                query="in:{0}".format(self._channel_sidebar_key(channel_name)),
                count=20,
                page=1,
            ),
            channel_name=channel_name,
        )
        if channel_id is not None:
            return channel_id

        raise RuntimeError(
            "Could not resolve Slack channel id for channel {0}.".format(channel_name)
        )

    def _channel_id_from_conversations_payload(
        self,
        payload: Dict[str, Any],
        channel_name: str,
    ) -> Optional[str]:
        if not payload.get("ok"):
            return None
        expected_name = self._channel_sidebar_key(channel_name)
        for item in payload.get("channels", []):
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("id") or "").strip()
            candidate_name = self._channel_sidebar_key(str(item.get("name") or ""))
            if candidate_id and candidate_name == expected_name:
                return candidate_id
        return None

    def _channel_id_from_search(
        self,
        payload: Dict[str, Any],
        channel_name: str,
    ) -> Optional[str]:
        if not payload.get("ok"):
            return None
        expected_name = self._channel_sidebar_key(channel_name)
        messages = payload.get("messages")
        if not isinstance(messages, dict):
            return None
        for item in messages.get("matches", []):
            if not isinstance(item, dict):
                continue
            channel = item.get("channel")
            if not isinstance(channel, dict):
                continue
            candidate_id = str(channel.get("id") or "").strip()
            candidate_name = self._channel_sidebar_key(str(channel.get("name") or ""))
            if candidate_id and candidate_name == expected_name:
                return candidate_id
        return None

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
        return page

    def _upload_external_bytes(self, upload_url: str, content: bytes) -> None:
        request = urllib.request.Request(
            upload_url,
            data=content,
            method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30.0) as response:
                status = int(getattr(response, "status", 0))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("Slack snippet upload failed: {0}".format(exc)) from exc
        if status < 200 or status >= 300:
            raise RuntimeError("Slack snippet upload failed with status {0}.".format(status))

    def _extract_api_session_info(self, url: str, post_data: str) -> Tuple[Optional[str], Optional[str]]:
        session = extract_api_session_from_request(url, post_data or "")
        if session is None:
            return None, None
        return session.token, session.origin

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

    def _api_client(self, workspace_name: str) -> SlackApiClient:
        token, origin = self._discover_api_session(workspace_name)
        session = SlackApiSession(origin=origin, token=token)
        return SlackApiClient(
            workspace_name=workspace_name,
            session=session,
            call_api=lambda method_name, params: self._call_slack_api(
                workspace_name=workspace_name,
                method_name=method_name,
                params=params,
            ),
        )

    def _call_slack_api(
        self,
        workspace_name: str,
        method_name: str,
        params: Dict[str, Any],
        retry_on_auth_error: bool = True,
        retry_on_closed_page_error: bool = True,
    ) -> Dict[str, Any]:
        token, origin = self._discover_api_session(workspace_name)
        page = self._api_page(origin)
        try:
            payload = page.evaluate(
                """
async ({origin, methodName, token, params, timeoutMs}) => {
  const form = new FormData();
  form.append('token', token);
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) continue;
    form.append(key, String(value));
  }
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  let resp;
  let text;
  try {
    resp = await fetch(origin + '/api/' + methodName, {
      method: 'POST',
      body: form,
      credentials: 'include',
      signal: controller.signal,
    });
    text = await resp.text();
  } catch (err) {
    if (err && err.name === 'AbortError') {
      return { status: 408, body: { ok: false, error: 'request_timeout' } };
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
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
                    "timeoutMs": self._SLACK_API_TIMEOUT_MS,
                },
            )
        except PlaywrightError as exc:
            if retry_on_closed_page_error and _is_closed_page_error(exc):
                self.close()
                return self._call_slack_api(
                    workspace_name=workspace_name,
                    method_name=method_name,
                    params=params,
                    retry_on_auth_error=retry_on_auth_error,
                    retry_on_closed_page_error=False,
                )
            raise
        body = payload.get("body") if isinstance(payload, dict) else None
        if (
            retry_on_closed_page_error
            and isinstance(body, dict)
            and body.get("error") == "request_timeout"
        ):
            self.close()
            return self._call_slack_api(
                workspace_name=workspace_name,
                method_name=method_name,
                params=params,
                retry_on_auth_error=retry_on_auth_error,
                retry_on_closed_page_error=False,
            )
        if (
            retry_on_auth_error
            and isinstance(body, dict)
            and body.get("error") in {"not_authed", "invalid_auth"}
        ):
            self._api_sessions.pop(workspace_name, None)
            self._workspace_api_contexts.pop(workspace_name, None)
            return self._call_slack_api(
                workspace_name=workspace_name,
                method_name=method_name,
                params=params,
                retry_on_auth_error=False,
                retry_on_closed_page_error=retry_on_closed_page_error,
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
            thread_ts = str(item.get("thread_ts") or message_ts)
            messages.append(
                SlackRootMessage(
                    workspace_name=workspace_name,
                    channel_name=channel_name,
                    thread_ts=thread_ts,
                    message_ts=message_ts,
                    author_actor_id=str(item.get("user") or ""),
                    text=str(item.get("text") or ""),
                )
            )
        messages.sort(key=lambda item: float(item.message_ts))
        return messages

    def _is_slack_socket_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme != "wss":
            return False
        hostname = parsed.hostname or ""
        return hostname.endswith(".slack.com")

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


def _is_closed_page_error(error: Exception) -> bool:
    return "target page, context or browser has been closed" in str(error).lower()
