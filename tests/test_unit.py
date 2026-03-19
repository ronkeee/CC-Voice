"""Unit tests for pure functions in daemon.py."""

import json
import os
import sys

import pytest

# Add repo root to path so we can import daemon directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from daemon import build_response


# ---------------------------------------------------------------------------
# build_response
# ---------------------------------------------------------------------------

def _parse(raw: str) -> dict:
    return json.loads(raw)


def test_build_response_valid_json():
    raw = build_response("allow", "test reason")
    _parse(raw)  # must not raise


def test_build_response_hook_event_name():
    data = _parse(build_response("allow", "r"))
    assert data["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"


@pytest.mark.parametrize("decision", ["allow", "deny", "ask"])
def test_build_response_decision_field(decision):
    data = _parse(build_response(decision, "r"))
    assert data["hookSpecificOutput"]["permissionDecision"] == decision


def test_build_response_reason_field():
    data = _parse(build_response("allow", "my reason"))
    assert data["hookSpecificOutput"]["permissionDecisionReason"] == "my reason"


def test_build_response_structure():
    data = _parse(build_response("ask", "r"))
    assert "hookSpecificOutput" in data
    out = data["hookSpecificOutput"]
    assert {"hookEventName", "permissionDecision", "permissionDecisionReason"} <= out.keys()
