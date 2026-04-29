# Bob Ultimate Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a feature-gated Bob ultimate mode that lets Bob owner invoke Bob with `bob ...` from any accessible Slack conversation, append Bob status/output into that same Slack message, and reuse the same Codex session on later explicit invocations in the same thread.

**Architecture:** Keep the existing thread/session store and configured-channel Bob flow, but add a runtime channel layer for unconfigured conversations, a watcher feature flag for workspace-wide explicit invocation, full-thread hydration per explicit invocation, and Slack `chat.update` support for same-message append delivery.

**Tech Stack:** Python 3, sqlite3, pytest, Slack Web API via Playwright-backed adapter

---

### Task 1: Add the feature flag and runtime config plumbing

**Files:**
- Modify: `src/personal_slack_agent/models.py`
- Modify: `src/personal_slack_agent/config.py`
- Modify: `config/bob.sample.toml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing config test for the new watcher flag**

```python
def test_watcher_bob_ultimate_mode_loads_and_dumps(tmp_path):
    config_path = tmp_path / "ultimate-mode.toml"
    config_path.write_text(
        """
        [defaults]
        default_cwd = "."

        [watcher]
        bob_ultimate_mode = true

        [[workspaces]]
        name = "bob_company"
        slack_url = "https://app.slack.com/client/T123/C123"
        slack_api_origin = "https://example.enterprise.slack.com"
        slack_api_token = "xoxc-test"

        [workspaces.channel_defaults]
        default_cwd = "."
        allowed_actor_ids = ["U123"]
        persistent_memory_mode = "disabled"
        """,
        encoding="utf-8",
    )

    loaded = load_config(config_path)

    assert loaded.watcher.bob_ultimate_mode is True
    rendered = dump_config(loaded)
    assert "bob_ultimate_mode = true" in rendered
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_config.py -k bob_ultimate_mode`
Expected: FAIL because `WatcherSettings` and config parsing do not yet define `bob_ultimate_mode`.

- [ ] **Step 3: Add the minimal config implementation**

```python
@dataclass
class WatcherSettings:
    root_batch_size: int = 50
    thread_batch_size: int = 200
    thread_reply_rate_limit_backoff_seconds: float = 60.0
    recent_terminal_thread_reconcile_limit: int = 6
    periodic_terminal_thread_reconcile_batch_size: int = 1
    historical_terminal_thread_reconcile_base_interval_seconds: float = 60.0
    historical_terminal_thread_reconcile_max_interval_seconds: float = 15 * 60.0
    bob_ultimate_mode: bool = False
```

```python
return WatcherSettings(
    root_batch_size=_positive_int(...),
    thread_batch_size=_positive_int(...),
    thread_reply_rate_limit_backoff_seconds=_optional_positive_float(...) or 60.0,
    recent_terminal_thread_reconcile_limit=_positive_int(...),
    periodic_terminal_thread_reconcile_batch_size=_positive_int(...),
    historical_terminal_thread_reconcile_base_interval_seconds=_optional_positive_float(...) or 60.0,
    historical_terminal_thread_reconcile_max_interval_seconds=_optional_positive_float(...) or 15 * 60.0,
    bob_ultimate_mode=_optional_bool(
        raw_watcher.get("bob_ultimate_mode", legacy_defaults.get("bob_ultimate_mode")),
        "watcher.bob_ultimate_mode",
        default=False,
    )
    or False,
)
```

```toml
[watcher]
root_batch_size = 50
thread_batch_size = 200
thread_reply_rate_limit_backoff_seconds = 60
recent_terminal_thread_reconcile_limit = 6
periodic_terminal_thread_reconcile_batch_size = 1
historical_terminal_thread_reconcile_base_interval_seconds = 60
historical_terminal_thread_reconcile_max_interval_seconds = 900
bob_ultimate_mode = false
```

- [ ] **Step 4: Run the config test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_config.py -k bob_ultimate_mode`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_config.py src/personal_slack_agent/models.py src/personal_slack_agent/config.py config/bob.sample.toml
git commit -m "feat: add bob ultimate mode config flag"
```

### Task 2: Add Slack message-update support and append-target models

**Files:**
- Modify: `src/personal_slack_agent/models.py`
- Modify: `src/personal_slack_agent/slack/api_client.py`
- Modify: `src/personal_slack_agent/slack/browser.py`
- Modify: `src/personal_slack_agent/slack/playwright_adapter.py`
- Test: `tests/test_playwright_adapter.py`

- [ ] **Step 1: Write the failing adapter test for message updates**

```python
def test_update_message_uses_chat_update(api_client_factory):
    calls = []

    class RecordingApiClient:
        def chat_update(self, channel_id, ts, text):
            calls.append({"channel_id": channel_id, "ts": ts, "text": text})
            return {"ok": True, "ts": ts, "text": text}

    adapter = api_client_factory(RecordingApiClient())

    adapter.update_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        text="bob can you do it?\nBob is working on it",
    )

    assert calls == [
        {
            "channel_id": "C123",
            "ts": "1743461000.000001",
            "text": "bob can you do it?\nBob is working on it",
        }
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_playwright_adapter.py -k update_message`
Expected: FAIL because the Slack API client, protocol, and adapter do not yet expose `chat.update`.

- [ ] **Step 3: Write the minimal implementation**

```python
def chat_update(
    self,
    channel_id: str,
    ts: str,
    text: str,
) -> Dict[str, Any]:
    return self._call_api(
        "chat.update",
        {
            "channel": channel_id,
            "ts": ts,
            "text": text,
        },
    )
```

```python
def update_message(
    self,
    workspace_name: str,
    channel_name: str,
    message_ts: str,
    text: str,
) -> None:
    ...
```

```python
payload = self._api_client(workspace_name).chat_update(
    channel_id=self.get_channel_id(workspace_name, channel_name),
    ts=message_ts,
    text=text,
)
if not payload.get("ok"):
    raise RuntimeError("Slack API chat.update failed: {0}".format(payload.get("error")))
```

- [ ] **Step 4: Run the adapter test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_playwright_adapter.py -k update_message`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_playwright_adapter.py src/personal_slack_agent/models.py src/personal_slack_agent/slack/api_client.py src/personal_slack_agent/slack/browser.py src/personal_slack_agent/slack/playwright_adapter.py
git commit -m "feat: add Slack message update support"
```

### Task 3: Add runtime channel resolution and workspace-wide watcher ingress

**Files:**
- Modify: `src/personal_slack_agent/models.py`
- Modify: `src/personal_slack_agent/config.py`
- Modify: `src/personal_slack_agent/slack/browser.py`
- Modify: `src/personal_slack_agent/slack/playwright_adapter.py`
- Modify: `src/personal_slack_agent/slack/events.py`
- Modify: `src/personal_slack_agent/slack/watcher.py`
- Test: `tests/test_slack_watcher.py`

- [ ] **Step 1: Write the failing watcher test for an unconfigured root invocation in ultimate mode**

```python
def test_watcher_routes_unconfigured_root_bob_message_when_ultimate_mode_enabled(tmp_path):
    state = BobStateStore(tmp_path / "bob.sqlite3")
    state.initialize()
    browser = FakeBrowser()
    browser.workspace_conversations["bob_company"] = {
        "C999": "proj-random",
    }
    browser.root_messages[("bob_company", "slack:C999")] = [
        SlackRootMessage(
            workspace_name="bob_company",
            channel_name="slack:C999",
            thread_ts="2.0",
            message_ts="2.0",
            author_actor_id="U123",
            text="bob review this",
        )
    ]
    orchestrator = RecordingOrchestrator()
    watcher = SlackWatcher(
        browser=browser,
        orchestrator=orchestrator,
        state_store=state,
        config=_ultimate_mode_config(tmp_path),
    )

    watcher.run_cycle()

    assert orchestrator.root_calls[-1]["channel_name"] == "slack:C999"
    assert orchestrator.root_calls[-1]["text"] == "bob review this"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_slack_watcher.py -k unconfigured_root_bob_message`
Expected: FAIL because watcher only iterates configured channels and cannot resolve runtime channel identities.

- [ ] **Step 3: Add the minimal runtime-channel implementation**

```python
def runtime_channel_name(slack_channel_id: str) -> str:
    return "slack:{0}".format(slack_channel_id)
```

```python
def build_runtime_channel(channel_defaults: WorkspaceChannelDefaults, slack_channel_id: str) -> ChannelConfig:
    channel = ChannelConfig(
        name=runtime_channel_name(slack_channel_id),
        slack_channel_id=slack_channel_id,
    )
    return apply_channel_defaults(DefaultSettings(), channel_defaults, channel)
```

```python
if self.config.watcher.bob_ultimate_mode:
    for channel_id in self.browser.list_accessible_conversation_ids(workspace.name):
        channel_name = runtime_channel_name(channel_id)
        self._channel_name_by_id[(workspace.name, channel_id)] = channel_name
        self.reconcile_channel_since_cursor(workspace.name, channel_name)
```

- [ ] **Step 4: Run the watcher test to verify it passes**

Run: `.venv/bin/python -m pytest -q tests/test_slack_watcher.py -k unconfigured_root_bob_message`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_slack_watcher.py src/personal_slack_agent/models.py src/personal_slack_agent/config.py src/personal_slack_agent/slack/browser.py src/personal_slack_agent/slack/playwright_adapter.py src/personal_slack_agent/slack/events.py src/personal_slack_agent/slack/watcher.py
git commit -m "feat: add runtime channel routing for bob ultimate mode"
```

### Task 4: Add one-shot inline invocation tasks and full-thread hydration

**Files:**
- Modify: `src/personal_slack_agent/models.py`
- Modify: `src/personal_slack_agent/state.py`
- Modify: `src/personal_slack_agent/orchestrator.py`
- Test: `tests/test_state.py`
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write the failing orchestrator test for inline reply invocation**

```python
def test_bob_ultimate_reply_invocation_updates_same_message_and_reuses_thread_session(fake_environment):
    orchestrator, browser, store, runner = fake_environment
    orchestrator.config.watcher.bob_ultimate_mode = True

    orchestrator.handle_ultimate_invocation(
        workspace_name="bob_company",
        channel_name="slack:C999",
        thread_ts="1776911047.025189",
        message_ts="1776911050.000200",
        author_actor_id="U123",
        text="bob can you do it?",
        thread_messages=[
            ("1776911047.025189", "U999", "can you say no?"),
            ("1776911050.000200", "U123", "bob can you do it?"),
        ],
    )

    assert browser.reactions[-1]["message_ts"] == "1776911050.000200"
    assert browser.updated_messages["1776911050.000200"][0].endswith("thread=`1776911047.025189`")
    assert browser.updated_messages["1776911050.000200"][-1].endswith("_*codex Bob :white_check_mark::*_ Final answer")
    assert len(runner.new_session_calls) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_orchestrator.py -k ultimate_reply_invocation`
Expected: FAIL because orchestrator has no ultimate-mode entry path, no inline append target, and no thread-hydration prompt wrapper.

- [ ] **Step 3: Write the minimal implementation**

```python
def handle_ultimate_invocation(..., thread_messages: List[ThreadMessage]) -> None:
    if not self.config.watcher.bob_ultimate_mode:
        return
    if not self._is_bob_root_message(text):
        return
    if not self._is_actor_allowed(...):
        return
    self._try_ack_message(...)
    self.state_store.enqueue_task(
        ...,
        task_kind=self._TASK_KIND_ULTIMATE_INVOCATION,
        prompt_text=text,
        response_mode="message_append",
        response_message_ts=message_ts,
        thread_context_json=self._serialize_thread_context(thread_messages),
    )
```

```python
def _build_ultimate_prompt(..., thread_messages: List[ThreadMessage], user_text: str) -> str:
    transcript = "\n".join(
        "[{0}] {1}: {2}".format(item.message_ts, item.author_actor_id, item.text)
        for item in thread_messages
    )
    return "{0}\n\nSlack thread transcript:\n{1}".format(
        self._build_codex_prompt(..., user_text),
        transcript,
    )
```

```python
def _append_to_message(..., message_ts: str, line: str) -> None:
    current = self.state_store.get_message_append_buffer(..., message_ts) or self._lookup_invocation_text(...)
    next_text = "{0}\n{1}".format(current, normalize_slack_markdown(line))
    self.browser.update_message(..., message_ts=message_ts, text=next_text)
    self.state_store.put_message_append_buffer(..., message_ts, next_text)
```

- [ ] **Step 4: Run the orchestrator/state tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_orchestrator.py -k ultimate`
Expected: PASS

Run: `.venv/bin/python -m pytest -q tests/test_state.py -k append_buffer`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_state.py tests/test_orchestrator.py src/personal_slack_agent/models.py src/personal_slack_agent/state.py src/personal_slack_agent/orchestrator.py
git commit -m "feat: add one-shot inline bob ultimate invocation flow"
```

### Task 5: Add watcher integration for explicit reply-only reentry and non-bob ignore behavior

**Files:**
- Modify: `src/personal_slack_agent/slack/watcher.py`
- Test: `tests/test_slack_watcher.py`

- [ ] **Step 1: Write the failing watcher tests for reply reentry and non-bob ignore**

```python
def test_watcher_ultimate_mode_ignores_non_bob_follow_up_reply(tmp_path):
    ...
    assert orchestrator.reply_calls == []
```

```python
def test_watcher_ultimate_mode_routes_later_bob_reply_in_same_thread(tmp_path):
    ...
    assert orchestrator.ultimate_calls[-1]["message_ts"] == "1776911060.000300"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_slack_watcher.py -k "ultimate_mode and (non_bob or later_bob_reply)"`
Expected: FAIL because watcher currently routes only tracked-thread replies and does not special-case ultimate-mode explicit reentry.

- [ ] **Step 3: Write the minimal watcher implementation**

```python
if self.config.watcher.bob_ultimate_mode and reply.text.strip().lower().startswith("bob"):
    self.orchestrator.handle_ultimate_invocation(
        ...,
        thread_messages=self.browser.list_thread_replies(...),
    )
    return
```

```python
if self.config.watcher.bob_ultimate_mode and not reply.text.strip().lower().startswith("bob"):
    return
```

- [ ] **Step 4: Run the watcher tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_slack_watcher.py -k ultimate_mode`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_slack_watcher.py src/personal_slack_agent/slack/watcher.py
git commit -m "feat: route explicit bob reply reentry in ultimate mode"
```

### Task 6: Add fallback behavior and run regressions

**Files:**
- Modify: `src/personal_slack_agent/orchestrator.py`
- Test: `tests/test_orchestrator.py`
- Test: `tests/test_config.py`
- Test: `tests/test_playwright_adapter.py`
- Test: `tests/test_slack_watcher.py`

- [ ] **Step 1: Write the failing fallback test**

```python
def test_bob_ultimate_mode_falls_back_to_thread_reply_when_message_update_fails(fake_environment):
    orchestrator, browser, _store, _runner = fake_environment
    orchestrator.config.watcher.bob_ultimate_mode = True
    browser.update_error = RuntimeError("chat.update failed")

    orchestrator.handle_ultimate_invocation(...)

    assert browser.thread_posts["1776911047.025189"][-1] == "_*codex Bob :white_check_mark::*_ Final answer"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest -q tests/test_orchestrator.py -k message_update_fails`
Expected: FAIL because the orchestrator does not yet fall back from same-message append to thread reply delivery.

- [ ] **Step 3: Write the minimal fallback implementation**

```python
try:
    self._append_to_message(...)
except Exception:
    self._deliver_thread_message(
        workspace_name=workspace_name,
        channel_name=channel_name,
        thread_ts=thread_ts,
        intent_key="ultimate-fallback-{0}".format(message_ts),
        text=line,
    )
```

- [ ] **Step 4: Run the targeted regressions**

Run: `.venv/bin/python -m pytest -q tests/test_config.py tests/test_state.py tests/test_orchestrator.py tests/test_playwright_adapter.py`
Expected: PASS

Run: `.venv/bin/python -m pytest -q tests/test_slack_watcher.py`
Expected: PASS after fixing the currently stale watcher test fixtures as part of the ultimate-mode watcher changes.

- [ ] **Step 5: Commit**

```bash
git add tests/test_orchestrator.py src/personal_slack_agent/orchestrator.py tests/test_config.py tests/test_state.py tests/test_playwright_adapter.py tests/test_slack_watcher.py
git commit -m "feat: finish bob ultimate mode and regressions"
```

## Self-Review

- Spec coverage:
  - feature flag: Task 1
  - Slack message updates: Task 2
  - workspace-wide discovery and runtime channels: Task 3
  - inline one-shot invocation and thread hydration: Task 4
  - explicit bob-only reentry: Task 5
  - fallback and regressions: Task 6
- Placeholder scan: no TBD/TODO placeholders remain; each task names exact files and commands.
- Type consistency:
  - `bob_ultimate_mode` is consistently a watcher bool
  - runtime channels use `slack:<conversation-id>`
  - inline delivery mode is consistently `message_append`
