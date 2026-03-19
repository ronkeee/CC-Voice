"""Integration tests — start the real daemon subprocess and talk to it over the socket."""

import json
import os
import signal
import socket
import subprocess
import sys
import time

import pytest

SOCKET_PATH = "/tmp/cc-voice-test.sock"
DAEMON_PY = os.path.join(os.path.dirname(__file__), "..", "daemon.py")
HOOK_SH = os.path.join(os.path.dirname(__file__), "..", "hook.sh")

PYTHON = sys.executable

VALID_REQUEST = json.dumps({
    "session_id": "test-session",
    "tool_name": "Bash",
    "tool_input": {"command": "echo hello"},
    "hook_event_name": "PermissionRequest",
})

VALID_DECISIONS = {"allow", "deny", "ask"}


# ---------------------------------------------------------------------------
# Fixture: daemon subprocess on a private test socket
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def daemon():
    """Start the daemon on a private socket path and yield; kill on teardown."""
    env = os.environ.copy()
    env["CC_VOICE_SOCKET"] = SOCKET_PATH

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    proc = subprocess.Popen(
        [PYTHON, DAEMON_PY],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait up to 5s for the socket to appear
    deadline = time.time() + 5
    while time.time() < deadline:
        if os.path.exists(SOCKET_PATH):
            break
        time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("Daemon did not create socket within 5s")

    yield proc

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=5)
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)


def _send(payload: str, *, timeout: int = 5) -> dict:
    """Send a raw JSON string to the daemon and return the parsed response."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCKET_PATH)
    s.sendall(payload.encode())
    s.shutdown(socket.SHUT_WR)

    chunks = []
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
    s.close()
    return json.loads(b"".join(chunks).decode())


# ---------------------------------------------------------------------------
# Socket protocol tests
# ---------------------------------------------------------------------------

def test_daemon_responds_to_permission_request(daemon):
    data = _send(VALID_REQUEST)
    assert "hookSpecificOutput" in data


def test_daemon_response_has_hook_event_name(daemon):
    data = _send(VALID_REQUEST)
    assert data["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"


def test_daemon_response_decision_is_valid(daemon):
    data = _send(VALID_REQUEST)
    decision = data["hookSpecificOutput"]["permissionDecision"]
    assert decision in VALID_DECISIONS, f"Unexpected decision: {decision!r}"


def test_daemon_response_has_reason(daemon):
    data = _send(VALID_REQUEST)
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert isinstance(reason, str)
    assert len(reason) > 0


def test_daemon_handles_multiple_sequential_requests(daemon):
    """Daemon must stay alive and respond correctly across multiple requests."""
    for i in range(3):
        data = _send(VALID_REQUEST)
        assert data["hookSpecificOutput"]["permissionDecision"] in VALID_DECISIONS, \
            f"Request {i} got bad decision"


def test_daemon_handles_different_tool_names(daemon):
    for tool in ("Bash", "Edit", "Write", "Glob"):
        payload = json.dumps({
            "session_id": "test",
            "tool_name": tool,
            "tool_input": {},
            "hook_event_name": "PermissionRequest",
        })
        data = _send(payload)
        assert data["hookSpecificOutput"]["permissionDecision"] in VALID_DECISIONS


def test_daemon_falls_back_on_malformed_json(daemon):
    """Malformed JSON must not crash the daemon; next valid request must work."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(SOCKET_PATH)
    s.sendall(b"this is not json")
    s.shutdown(socket.SHUT_WR)
    try:
        s.recv(4096)
    except Exception:
        pass
    s.close()

    data = _send(VALID_REQUEST)
    assert data["hookSpecificOutput"]["permissionDecision"] in VALID_DECISIONS


# ---------------------------------------------------------------------------
# hook.sh round-trip test (uses the production socket path)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def production_daemon():
    """Start daemon on the production socket path for hook.sh tests."""
    prod_socket = "/tmp/cc-voice.sock"

    if os.path.exists(prod_socket):
        yield None
        return

    proc = subprocess.Popen(
        [PYTHON, DAEMON_PY],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        if os.path.exists(prod_socket):
            break
        time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("Daemon did not start within 5s")

    yield proc

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=5)


def test_hook_sh_round_trip(production_daemon):
    """Pipe a valid PermissionRequest through hook.sh and verify the JSON output."""
    if not os.path.exists(HOOK_SH):
        pytest.skip("hook.sh not found")

    result = subprocess.run(
        ["/bin/bash", HOOK_SH],
        input=VALID_REQUEST.encode(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, f"hook.sh exited {result.returncode}"

    data = json.loads(result.stdout.decode())
    out = data["hookSpecificOutput"]
    assert out["hookEventName"] == "PermissionRequest"
    assert out["permissionDecision"] in VALID_DECISIONS
    assert isinstance(out["permissionDecisionReason"], str)
