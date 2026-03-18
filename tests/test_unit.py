"""Unit tests for pure functions in daemon.py — no audio hardware required."""

import json
import os
import sys
import tempfile
import wave

import pytest

# Add repo root to path so we can import daemon directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from daemon import (
    ALLOW_PHRASES,
    DENY_PHRASES,
    build_response,
    generate_chime,
    parse_decision,
)


# ---------------------------------------------------------------------------
# parse_decision — every allow phrase
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", sorted(ALLOW_PHRASES))
def test_parse_decision_allow_exact(phrase):
    decision, reason = parse_decision(phrase)
    assert decision == "allow", f"Expected allow for '{phrase}', got {decision!r}"
    assert phrase in reason


@pytest.mark.parametrize("phrase", sorted(ALLOW_PHRASES))
def test_parse_decision_allow_in_sentence(phrase):
    """Phrase embedded in a longer utterance should still match."""
    decision, _ = parse_decision(f"um {phrase} I think")
    assert decision == "allow", f"Expected allow for phrase '{phrase}' in sentence"


@pytest.mark.parametrize("phrase", sorted(ALLOW_PHRASES))
def test_parse_decision_allow_uppercase(phrase):
    """Matching must be case-insensitive."""
    decision, _ = parse_decision(phrase.upper())
    assert decision == "allow"


# ---------------------------------------------------------------------------
# parse_decision — every deny phrase
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", sorted(DENY_PHRASES))
def test_parse_decision_deny_exact(phrase):
    decision, reason = parse_decision(phrase)
    assert decision == "deny", f"Expected deny for '{phrase}', got {decision!r}"
    assert phrase in reason


@pytest.mark.parametrize("phrase", sorted(DENY_PHRASES))
def test_parse_decision_deny_in_sentence(phrase):
    decision, _ = parse_decision(f"actually {phrase} please")
    assert decision == "deny", f"Expected deny for phrase '{phrase}' in sentence"


@pytest.mark.parametrize("phrase", sorted(DENY_PHRASES))
def test_parse_decision_deny_uppercase(phrase):
    decision, _ = parse_decision(phrase.upper())
    assert decision == "deny"


# ---------------------------------------------------------------------------
# parse_decision — fallback / edge cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "",
    "   ",
    "maybe",
    "later",
    "hmm",
    "what was that",
    "i don't know",   # contains "don't" → deny (correct behaviour)
])
def test_parse_decision_ask_or_deny(text):
    """Unrecognised words return 'ask'; 'don't' correctly returns 'deny'."""
    decision, _ = parse_decision(text)
    # "i don't know" contains "don't" so deny is correct
    if "don't" in text or "dont" in text:
        assert decision == "deny"
    else:
        assert decision == "ask"


def test_parse_decision_returns_tuple():
    result = parse_decision("yes")
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_parse_decision_reason_contains_heard():
    _, reason = parse_decision("yes")
    assert "yes" in reason


def test_parse_decision_whitespace_stripped():
    decision, _ = parse_decision("  yes  ")
    assert decision == "allow"


def test_parse_decision_confirmed_is_allow():
    """'confirmed' should hit ALLOW before 'confirm' causes any confusion."""
    decision, _ = parse_decision("confirmed")
    assert decision == "allow"


def test_parse_decision_denied_is_deny():
    decision, _ = parse_decision("denied")
    assert decision == "deny"


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


# ---------------------------------------------------------------------------
# generate_chime
# ---------------------------------------------------------------------------

def test_generate_chime_creates_file():
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        generate_chime(path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0
    finally:
        os.unlink(path)


def test_generate_chime_valid_wav():
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        generate_chime(path)
        with wave.open(path, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2  # 16-bit
            assert wf.getframerate() == 44100
    finally:
        os.unlink(path)


def test_generate_chime_duration():
    """Chime should be ~0.5 seconds (within ±0.05s)."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        generate_chime(path)
        with wave.open(path, "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()
        assert 0.45 <= duration <= 0.55, f"Unexpected duration: {duration:.2f}s"
    finally:
        os.unlink(path)


def test_generate_chime_idempotent(tmp_path):
    """Calling generate_chime twice on the same path should not raise."""
    path = str(tmp_path / "chime.wav")
    generate_chime(path)
    generate_chime(path)
    assert os.path.exists(path)
