#!/usr/bin/env python3
"""
CC-Voice daemon — background process that handles audio alerts and voice recognition
for Claude Code approval prompts.

Architecture:
  hook.sh  →  Unix socket /tmp/cc-voice.sock  →  this daemon
  daemon plays chime, records voice, transcribes with Whisper, returns decision JSON
"""

import argparse
import asyncio
import json
import logging
import logging.handlers
import math
import os
import signal
import subprocess
import sys
import wave
import array

SOCKET_PATH = "/tmp/cc-voice.sock"
INSTALL_DIR = os.path.expanduser("~/.local/share/cc-voice")
CHIME_PATH = os.path.join(INSTALL_DIR, "chime.wav")
LOG_PATH = os.path.join(INSTALL_DIR, "daemon.log")

SAMPLE_RATE = 16000   # Hz — Whisper expects 16kHz
RECORD_SECONDS = 5    # seconds to record after chime

ALLOW_PHRASES = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay",
    "approve", "approved", "allow", "go", "do it", "proceed",
    "confirm", "confirmed", "correct", "right", "affirmative",
    "one", "1",
}

DENY_PHRASES = {
    "no", "nope", "nah", "deny", "denied", "reject", "rejected",
    "stop", "cancel", "abort", "negative", "don't", "dont",
    "two", "2",
}

logger = logging.getLogger("cc-voice")

# Whisper model — loaded once at startup
_model = None


# ---------------------------------------------------------------------------
# Chime generation
# ---------------------------------------------------------------------------

def generate_chime(path: str) -> None:
    """Generate a 880Hz sine wave chime with exponential fade-out, saved as WAV.

    Uses only stdlib modules — no external dependencies.
    """
    sample_rate = 44100
    duration = 0.5        # seconds
    frequency = 880.0     # Hz — A5, pleasant "ding" tone
    amplitude = 28000     # out of 32767 max for 16-bit PCM

    n_samples = int(sample_rate * duration)
    samples = array.array("h")  # signed 16-bit integers

    for i in range(n_samples):
        t = i / sample_rate
        sine = math.sin(2 * math.pi * frequency * t)
        envelope = math.exp(-6.0 * t / duration)
        samples.append(int(amplitude * sine * envelope))

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)           # 2 bytes = 16-bit
        f.setframerate(sample_rate)
        f.writeframes(samples.tobytes())

    logger.info("Generated chime at %s", path)


def play_chime() -> None:
    """Play the chime using macOS afplay (non-blocking from daemon's perspective
    but we wait for it to finish before recording)."""
    if not os.path.exists(CHIME_PATH):
        generate_chime(CHIME_PATH)
    subprocess.run(["afplay", CHIME_PATH], check=False)


# ---------------------------------------------------------------------------
# Audio recording + Whisper transcription
# ---------------------------------------------------------------------------

def record_and_transcribe() -> str:
    """Record RECORD_SECONDS of audio and return lowercased transcript string.

    Returns empty string on any error.
    """
    try:
        import sounddevice as sd
        import numpy as np

        logger.info("Recording %ds of audio...", RECORD_SECONDS)
        audio = sd.rec(
            int(RECORD_SECONDS * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
        )
        sd.wait()  # blocking — run in executor
        audio_flat = audio.flatten()

        logger.info("Transcribing...")
        result = _model.transcribe(
            audio_flat,
            language="en",
            fp16=False,         # CPU-only (no CUDA needed)
            temperature=0.0,    # deterministic — approval words are short/unambiguous
        )
        text = result.get("text", "").strip().lower()
        logger.info("Transcribed: %r", text)
        return text

    except Exception as exc:
        logger.error("record_and_transcribe error: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Decision parsing
# ---------------------------------------------------------------------------

def parse_decision(text: str) -> tuple[str, str]:
    """Map transcribed text to a Claude Code permission decision.

    Returns (decision, reason) where decision is 'allow', 'deny', or 'ask'.
    """
    normalized = text.lower().strip()

    # Check full phrases first (handles "do it", "don't", etc.)
    for phrase in sorted(ALLOW_PHRASES, key=len, reverse=True):
        if phrase in normalized:
            return "allow", f"Voice approval: heard '{normalized}'"

    for phrase in sorted(DENY_PHRASES, key=len, reverse=True):
        if phrase in normalized:
            return "deny", f"Voice denial: heard '{normalized}'"

    return "ask", f"Voice unrecognized: heard '{normalized}'"


def build_response(decision: str, reason: str) -> str:
    """Build the JSON response expected by Claude Code's hook protocol."""
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    })


FALLBACK_RESPONSE = build_response("ask", "CC-Voice: error or unrecognized — deferring to Claude Code")


# ---------------------------------------------------------------------------
# Asyncio socket server
# ---------------------------------------------------------------------------

async def handle_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handle one incoming hook request."""
    try:
        data = await asyncio.wait_for(reader.readline(), timeout=5.0)
        payload_str = data.decode().strip()
        logger.info("Received request (%d bytes)", len(payload_str))

        # Validate it's a PermissionRequest
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

        logger.info("PermissionRequest for tool=%s cmd=%r", tool, cmd_preview)

        # Play chime + record + transcribe (blocking I/O → run in thread executor)
        loop = asyncio.get_event_loop()

        await loop.run_in_executor(None, play_chime)

        text = await loop.run_in_executor(None, record_and_transcribe)

        decision, reason = parse_decision(text)
        logger.info("Decision: %s (%s)", decision, reason)

        response = build_response(decision, reason)
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
    # Remove stale socket file if present
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    server = await asyncio.start_unix_server(
        handle_request,
        path=SOCKET_PATH,
        backlog=1,  # one prompt at a time
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
    parser = argparse.ArgumentParser(description="CC-Voice daemon")
    parser.add_argument(
        "--generate-chime",
        action="store_true",
        help="Generate chime.wav and exit (used by install.sh)",
    )
    args = parser.parse_args()

    setup_logging()

    if args.generate_chime:
        generate_chime(CHIME_PATH)
        print(f"Chime written to {CHIME_PATH}")
        return

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
            # Stale socket — proceed
            pass

    # Load Whisper model
    logger.info("Loading Whisper 'tiny' model...")
    try:
        import whisper
        global _model
        _model = whisper.load_model("tiny")
        logger.info("Whisper model loaded")
    except ImportError:
        logger.error("openai-whisper not installed. Run: pip install openai-whisper")
        sys.exit(1)
    except Exception as exc:
        logger.error("Failed to load Whisper model: %s", exc)
        sys.exit(1)

    # Ensure chime exists
    if not os.path.exists(CHIME_PATH):
        generate_chime(CHIME_PATH)

    # Signal handling
    signal.signal(signal.SIGTERM, _handle_sigterm)

    # Run server
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)


if __name__ == "__main__":
    main()
