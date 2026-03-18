#!/usr/bin/env bash
# CC-Voice: Claude Code PermissionRequest hook
#
# Registered in ~/.claude/settings.json under hooks.PermissionRequest.
# Reads the permission request JSON from stdin, forwards it to the CC-Voice
# daemon via Unix socket, and prints the daemon's JSON decision to stdout.
#
# Claude Code protocol:
#   stdin  → JSON payload (session_id, tool_name, tool_input, hook_event_name, ...)
#   stdout → JSON response with hookSpecificOutput.permissionDecision: allow|deny|ask
#   exit 0 → always (non-zero causes Claude Code error UI)

set -euo pipefail

SOCKET="/tmp/cc-voice.sock"
TIMEOUT=35  # seconds (covers chime + 5s recording + transcription + margin)

FALLBACK='{"hookSpecificOutput":{"hookEventName":"PermissionRequest","permissionDecision":"ask","permissionDecisionReason":"CC-Voice: daemon not running or timed out — deferring to Claude Code"}}'

# Read full stdin payload from Claude Code
input=$(cat)

# Export payload for the Python subprocess (avoids shell quoting issues with
# arbitrary JSON that can contain quotes, backslashes, etc.)
export CC_VOICE_PAYLOAD="$input"

# Use an embedded Python3 script as the Unix socket client.
# Python 3 ships with macOS 10.15+ so this has zero extra dependencies.
RESPONSE=$(python3 - <<'PYEOF' 2>/dev/null
import sys
import socket
import json
import os

SOCKET_PATH = "/tmp/cc-voice.sock"
TIMEOUT = int(os.environ.get("CC_VOICE_TIMEOUT", "35"))
FALLBACK = json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "permissionDecision": "ask",
        "permissionDecisionReason": "CC-Voice: daemon not running or timed out — deferring to Claude Code",
    }
})

payload = os.environ.get("CC_VOICE_PAYLOAD", "")

if not payload:
    print(FALLBACK)
    sys.exit(0)

try:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT)
    sock.connect(SOCKET_PATH)

    # Send the payload (newline-delimited)
    sock.sendall((payload + "\n").encode())

    # Read response (newline-delimited)
    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if b"\n" in data:
            break

    sock.close()

    response = data.decode().strip()
    if response:
        print(response)
    else:
        print(FALLBACK)

except FileNotFoundError:
    # Socket file doesn't exist — daemon not running
    sys.stderr.write("CC-Voice: daemon socket not found (is daemon running?)\n")
    print(FALLBACK)

except socket.timeout:
    sys.stderr.write("CC-Voice: timed out waiting for daemon response\n")
    print(FALLBACK)

except Exception as exc:
    sys.stderr.write(f"CC-Voice: socket error: {exc}\n")
    print(FALLBACK)
PYEOF
) || true  # never let this fail the hook

# Output the response to Claude Code (must be on stdout)
if [[ -n "$RESPONSE" ]]; then
    printf '%s\n' "$RESPONSE"
else
    printf '%s\n' "$FALLBACK"
fi

exit 0
