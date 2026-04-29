# Channel-Scoped Memory Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Bob require explicit per-channel durable-memory policy, and inject that policy into every new or resumed Codex prompt so shared/test channels cannot update Bob owner's personal session notes.

**Architecture:** Extend `ChannelConfig` and config parsing to require a channel memory-policy declaration, then centralize prompt wrapping inside `BobOrchestrator` so all Codex traffic carries channel context plus the memory rule. Keep the policy narrow: full tool/skill/MCP/agent access remains available in all channels, while only durable personal preference updates are constrained.

**Tech Stack:** Python 3.9+, dataclasses, `tomli`/`tomllib`, `pytest`

---

## File Structure

Create or modify the following files:

- Modify: `src/personal_slack_agent/models.py`
- Modify: `src/personal_slack_agent/config.py`
- Modify: `src/personal_slack_agent/orchestrator.py`
- Modify: `config/bob.sample.toml`
- Modify: `README.md`
- Test: `tests/test_config.py`
- Test: `tests/test_orchestrator.py`

Responsibility split:

- `models.py`
  define the explicit channel memory-policy fields and allowed mode constants
- `config.py`
  require and validate those fields during config load/dump
- `orchestrator.py`
  build wrapped prompts for new-session and resume flows
- `tests/test_config.py`
  prove required config semantics and validation failures
- `tests/test_orchestrator.py`
  prove prompt wrapping for private vs shared/test channels

### Task 1: Require channel memory-policy fields in config

**Files:**
- Modify: `src/personal_slack_agent/models.py`
- Modify: `src/personal_slack_agent/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing config test for an `owner_only` channel**

```python
def test_channel_memory_policy_owner_only_is_loaded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "owner-only.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "bob_company"

        [[workspaces.channels]]
        name = "bob_private_channel"
        persistent_memory_mode = "owner_only"
        persistent_memory_owner = "bob_owner_handle"
        """,
        encoding="utf-8",
    )

    config = load_config(config_path)
    channel = config.workspaces[0].channels[0]

    assert channel.persistent_memory_mode == "owner_only"
    assert channel.persistent_memory_owner == "bob_owner_handle"
```

- [ ] **Step 2: Run the targeted config test to verify it fails**

Run: `cd /Users/bob_owner_handle/Code/OHAI/ctdm/personal_slack_agent && .venv/bin/python -m pytest tests/test_config.py::test_channel_memory_policy_owner_only_is_loaded -q`

Expected: FAIL because `ChannelConfig` does not yet define the new fields.

- [ ] **Step 3: Add validation tests for required policy combinations**

```python
def test_channel_memory_policy_disabled_rejects_owner(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "disabled-with-owner.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "bob_company"

        [[workspaces.channels]]
        name = "bob_channel"
        persistent_memory_mode = "disabled"
        persistent_memory_owner = "bob_owner_handle"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="persistent_memory_owner"):
        load_config(config_path)


def test_channel_memory_policy_owner_only_requires_owner(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    config_path = tmp_path / "missing-owner.toml"
    config_path.write_text(
        f"""
        [defaults]
        default_cwd = "{root}"
        allowed_actor_ids = ["U123"]

        [[workspaces]]
        name = "bob_company"

        [[workspaces.channels]]
        name = "bob_private_channel"
        persistent_memory_mode = "owner_only"
        """,
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="persistent_memory_owner"):
        load_config(config_path)
```

- [ ] **Step 4: Implement the model fields and config parser**

```python
PERSISTENT_MEMORY_MODE_DISABLED = "disabled"
PERSISTENT_MEMORY_MODE_OWNER_ONLY = "owner_only"


@dataclass
class ChannelConfig:
    name: str
    default_cwd: Optional[str] = None
    accept_root_bob_requests: Optional[bool] = None
    post_terminal_threads_here: bool = False
    persistent_memory_mode: str = ""
    persistent_memory_owner: Optional[str] = None
    effective_default_cwd: str = ""
    effective_accept_root_bob_requests: bool = True
```

```python
def _persistent_memory_mode(value: Any, field_name: str) -> str:
    if value not in ("owner_only", "disabled"):
        raise ConfigError(f"{field_name} must be one of: owner_only, disabled.")
    return value


def _validate_channel_memory_policy(channel: ChannelConfig) -> ChannelConfig:
    if channel.persistent_memory_mode == "owner_only":
        if not channel.persistent_memory_owner:
            raise ConfigError("channel.persistent_memory_owner is required when persistent_memory_mode is owner_only.")
    elif channel.persistent_memory_owner is not None:
        raise ConfigError("channel.persistent_memory_owner is only allowed when persistent_memory_mode is owner_only.")
    return channel
```

- [ ] **Step 5: Run the config file to verify the new tests pass**

Run: `cd /Users/bob_owner_handle/Code/OHAI/ctdm/personal_slack_agent && .venv/bin/python -m pytest tests/test_config.py -q`

Expected: PASS for the new channel memory-policy tests and the existing config suite.

- [ ] **Step 6: Commit**

```bash
git add src/personal_slack_agent/models.py src/personal_slack_agent/config.py tests/test_config.py
git commit -m "feat: require channel memory policy config"
```

### Task 2: Wrap all Codex prompts with channel memory-policy context

**Files:**
- Modify: `src/personal_slack_agent/orchestrator.py`
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write the failing new-session prompt test for a private channel**

```python
def test_new_root_message_wraps_prompt_with_owner_only_memory_policy(fake_environment):
    orchestrator, _browser, _store, runner = fake_environment

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_private_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, remember that I prefer reviewer passes",
    )

    prompt = runner.new_session_calls[0]["prompt"]
    assert "channel: bob_private_channel" in prompt
    assert "persistent_memory_mode: owner_only" in prompt
    assert "persistent_memory_owner: bob_owner_handle" in prompt
    assert "may use all available tools, skills, MCP servers, and agents" in prompt
```

- [ ] **Step 2: Run the targeted orchestrator test to verify it fails**

Run: `cd /Users/bob_owner_handle/Code/OHAI/ctdm/personal_slack_agent && .venv/bin/python -m pytest tests/test_orchestrator.py::test_new_root_message_wraps_prompt_with_owner_only_memory_policy -q`

Expected: FAIL because the orchestrator currently forwards raw Slack text.

- [ ] **Step 3: Add failing tests for shared/test-channel behavior and resume behavior**

```python
def test_new_root_message_wraps_prompt_with_disabled_memory_policy_for_shared_channel(fake_environment):
    orchestrator, _browser, _store, runner = fake_environment
    shared = ChannelConfig(
        name="bob_channel",
        persistent_memory_mode="disabled",
        persistent_memory_owner=None,
        effective_default_cwd=orchestrator.config.defaults.default_cwd,
        effective_accept_root_bob_requests=True,
    )
    orchestrator.config.workspaces[0].channels.append(shared)

    orchestrator.handle_new_root_message(
        workspace_name="bob_company",
        channel_name="bob_channel",
        message_ts="1743461000.000001",
        author_actor_id="U123",
        text="Bob, help my coworker debug this test",
    )

    prompt = runner.new_session_calls[0]["prompt"]
    assert "channel: bob_channel" in prompt
    assert "persistent_memory_mode: disabled" in prompt
    assert "do not update personal session notes" in prompt.lower()
```

```python
def test_closed_idle_reply_resume_reasserts_disabled_memory_policy(fake_environment):
    orchestrator, _browser, store, runner = fake_environment
    shared = ChannelConfig(
        name="bob_test_channel",
        persistent_memory_mode="disabled",
        persistent_memory_owner=None,
        effective_default_cwd=orchestrator.config.defaults.default_cwd,
        effective_accept_root_bob_requests=True,
    )
    orchestrator.config.workspaces[0].channels.append(shared)
    store.upsert_session(
        workspace_name="bob_company",
        channel_name="bob_test_channel",
        thread_ts="1743461000.000001",
        root_ts="1743461000.000001",
        codex_session_id="session-123",
        cwd="/tmp/project",
        owner_actor_id="U123",
        status=SessionStatus.CLOSED_IDLE,
    )

    orchestrator.handle_thread_reply(
        workspace_name="bob_company",
        channel_name="bob_test_channel",
        thread_ts="1743461000.000001",
        message_ts="1743461010.000001",
        author_actor_id="U123",
        text="Keep investigating",
    )

    prompt = runner.resume_calls[0]["prompt"]
    assert "channel: bob_test_channel" in prompt
    assert "persistent_memory_mode: disabled" in prompt
    assert "do not update personal session notes" in prompt.lower()
```

- [ ] **Step 4: Implement prompt wrapping in the orchestrator**

```python
def _build_codex_prompt(self, workspace_name: str, channel_name: str, user_text: str) -> str:
    channel = self._find_channel(self._find_workspace(workspace_name), channel_name)
    if channel is None:
        return user_text

    owner = channel.persistent_memory_owner or "none"
    if channel.persistent_memory_mode == "owner_only":
        memory_rule = (
            "This Slack channel is allowed to update durable personal preference notes "
            "for owner `{0}` when the conversation reveals a durable preference or workflow rule."
        ).format(owner)
    else:
        memory_rule = (
            "This Slack channel does not grant permission to update Bob owner's "
            "personal durable preference files. Do not update personal session notes or similar "
            "durable preference files for Bob owner from this conversation."
        )

    return (
        "Bob execution context:\\n"
        "- workspace: {0}\\n"
        "- channel: {1}\\n"
        "- persistent_memory_mode: {2}\\n"
        "- persistent_memory_owner: {3}\\n\\n"
        "Rules:\\n"
        "- You may use all available tools, skills, MCP servers, and agents normally.\\n"
        "- {4}\\n\\n"
        "User request from Slack:\\n"
        "{5}"
    ).format(
        workspace_name,
        channel_name,
        channel.persistent_memory_mode,
        owner,
        memory_rule,
        user_text,
    )
```

- [ ] **Step 5: Route both new-session and resume flows through the prompt builder**

```python
wrapped_prompt = self._build_codex_prompt(workspace_name, channel_name, text)
run_result = self.codex_runner.run_new_session(
    prompt=wrapped_prompt,
    cwd=cwd,
    additional_roots=list(self.config.defaults.additional_roots),
)
```

```python
wrapped_prompt = self._build_codex_prompt(workspace_name, channel_name, prompt)
run_result = self.codex_runner.resume_session(session_id, wrapped_prompt, record.cwd)
```

- [ ] **Step 6: Run the orchestrator suite**

Run: `cd /Users/bob_owner_handle/Code/OHAI/ctdm/personal_slack_agent && .venv/bin/python -m pytest tests/test_orchestrator.py -q`

Expected: PASS for the new prompt-policy tests and the existing orchestrator behavior.

- [ ] **Step 7: Commit**

```bash
git add src/personal_slack_agent/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: inject channel memory policy into codex prompts"
```

### Task 3: Update user-facing configuration docs and verify end-to-end

**Files:**
- Modify: `config/bob.sample.toml`
- Modify: `README.md`

- [ ] **Step 1: Update the sample config with required policy fields**

```toml
[[workspaces.channels]]
name = "bob_private_channel"
default_cwd = "/Users/you/Code"
accept_root_bob_requests = true
persistent_memory_mode = "owner_only"
persistent_memory_owner = "bob_owner_handle"
post_terminal_threads_here = true

[[workspaces.channels]]
name = "bob_channel"
default_cwd = "/Users/you/Code"
accept_root_bob_requests = true
persistent_memory_mode = "disabled"

[[workspaces.channels]]
name = "bob_test_channel"
default_cwd = "/Users/you/Code"
accept_root_bob_requests = true
persistent_memory_mode = "disabled"
```

- [ ] **Step 2: Update the README config notes**

```markdown
- `persistent_memory_mode`
  Required per channel. Use `owner_only` for channels allowed to update a specific user's durable preference notes, or `disabled` for shared/test channels that must not update personal notes.

- `persistent_memory_owner`
  Required only when `persistent_memory_mode = "owner_only"`. This identifies whose durable preference notes the channel is allowed to update.
```

- [ ] **Step 3: Run targeted verification**

Run: `cd /Users/bob_owner_handle/Code/OHAI/ctdm/personal_slack_agent && .venv/bin/python -m pytest tests/test_config.py tests/test_orchestrator.py -q`

Expected: PASS

- [ ] **Step 4: Run the full test suite**

Run: `cd /Users/bob_owner_handle/Code/OHAI/ctdm/personal_slack_agent && .venv/bin/python -m pytest -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config/bob.sample.toml README.md
git commit -m "docs: document channel memory policy"
```
