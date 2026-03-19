#!/usr/bin/env python3
"""
CC-Voice daemon — background process that handles Claude Code approval prompts
via a Unix domain socket.

Architecture:
  hook.sh  →  Unix socket /tmp/cc-voice.sock  →  this daemon
  daemon receives PermissionRequest, returns decision JSON
"""

import asyncio
import json
import logging
import logging.handlers
import os
import signal
import sys

SOCKET_PATH = os.environ.get("CC_VOICE_SOCKET", "/tmp/cc-voice.sock")
INSTALL_DIR = os.path.expanduser("~/.local/share/cc-voice")
LOG_PATH = os.path.join(INSTALL_DIR, "daemon.log")

logger = logging.getLogger("cc-voice")


def build_response(decision: str, reason: str) -> str:
    """Build the JSON response expected by Claude Code's hook protocol."""
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    })


FALLBACK_RESPONSE = build_response("ask", "CC-Voice: error — deferring to Claude Code")


# ---------------------------------------------------------------------------
# Asyncio socket server
# ---------------------------------------------------------------------------

async def handle_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handle one incoming hook request."""
    try:
        data = await asyncio.wait_for(reader.readline(), timeout=5.0)
        payload_str = data.decode().strip()
        logger.info("Received request (%d bytes)", len(payload_str))

        try:
            payload = json.loads(payload_str)
            event = payload.get("hook_event_name", "")
            tool = payload.get("tool_name", "unknown")
            cmd_preview = str(payload.get("tool_input", {}).get("command", ""))[:80]
        except json.JSONDecodeError as exc:
            logger.warning("Invalid JSON from hook: %s", exc)
            writer.write((FALLBACK_RESPONSE + "\n").encode())
            await writer.drain()
            return

        if event != "PermissionRequest":
            logger.warning("Unexpected event: %s", event)
            writer.write((build_response("ask", f"CC-Voice: unexpected event '{event}'") + "\n").encode())
            await writer.drain()
            return

        logger.info("PermissionRequest for tool=%s cmd=%r — deferring to Claude Code", tool, cmd_preview)

        response = build_response("ask", "CC-Voice: deferring to Claude Code")
        writer.write((response + "\n").encode())
        await writer.drain()

    except asyncio.TimeoutError:
        logger.warning("Timed out waiting for hook data")
        writer.write((FALLBACK_RESPONSE + "\n").encode())
        await writer.drain()
    except Exception as exc:
        logger.error("handle_request error: %s", exc, exc_info=True)
        try:
            writer.write((FALLBACK_RESPONSE + "\n").encode())
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()


async def run_server() -> None:
    """Start the Unix domain socket server and serve forever."""
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    server = await asyncio.start_unix_server(
        handle_request,
        path=SOCKET_PATH,
        backlog=1,
    )

    logger.info("CC-Voice daemon listening on %s", SOCKET_PATH)

    async with server:
        await server.serve_forever()


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _handle_sigterm(signum, frame):
    """Clean up socket file on graceful shutdown."""
    logger.info("SIGTERM received — shutting down")
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    os.makedirs(INSTALL_DIR, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler(sys.stderr))
    logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()

    # Check for already-running daemon
    if os.path.exists(SOCKET_PATH):
        import socket as _socket
        try:
            sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(SOCKET_PATH)
            sock.close()
            logger.error("CC-Voice daemon already running at %s — exiting", SOCKET_PATH)
            sys.exit(0)
        except OSError:
            pass

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)


if __name__ == "__main__":
    main()
