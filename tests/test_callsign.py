import pytest

from personal_slack_agent.callsign import (
    assistant_label_from_text,
    is_manual_close_request,
    match_assistant_invocation,
    normalize_assistant_names,
    strip_assistant_prefix,
)


def test_match_assistant_invocation_returns_exact_alias_from_message():
    match = match_assistant_invocation("bObBy, run tests", ["Bob", "Bobby"])

    assert match is not None
    assert match.alias == "bObBy"
    assert match.remainder == "run tests"


def test_match_assistant_invocation_requires_name_boundary():
    assert match_assistant_invocation("bobcat run tests", ["Bob"]) is None


def test_strip_assistant_prefix_removes_only_configured_alias():
    assert strip_assistant_prefix("Copilot: summarize", ["Bob", "Copilot"]) == "summarize"
    assert strip_assistant_prefix("please summarize", ["Bob", "Copilot"]) == "please summarize"


def test_manual_close_request_accepts_each_alias_in_both_orders():
    assert is_manual_close_request("bobby close", ["Bob", "Bobby"])
    assert is_manual_close_request("close bObBy", ["Bob", "Bobby"])
    assert not is_manual_close_request("close bobcat", ["Bob"])


def test_assistant_label_from_text_prefers_exact_alias_then_fallback():
    assert assistant_label_from_text("bObBy run tests", ["Bob", "Bobby"], "Bob") == "bObBy"
    assert assistant_label_from_text("continue", ["Bob", "Bobby"], "Bobby") == "Bobby"


def test_normalize_assistant_names_defaults_empty_and_rejects_duplicates():
    assert normalize_assistant_names([]) == ["Bob"]
    assert normalize_assistant_names([" Bob ", "Bobby"]) == ["Bob", "Bobby"]
    with pytest.raises(ValueError, match="duplicates"):
        normalize_assistant_names(["Bob", "bob"])
