#!/usr/bin/env bash
# CC-Voice uninstaller
# Removes the daemon, hook registration, and launchd agent.
#
# Usage:
#   ./uninstall.sh

set -euo pipefail

INSTALL_DIR="$HOME/.local/share/cc-voice"
PLIST_LABEL="com.ccvoice.daemon"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[cc-voice]${NC} $*"; }
success() { echo -e "${GREEN}[cc-voice]${NC} $*"; }
warn()    { echo -e "${RED}[cc-voice]${NC} $*"; }

# ── Stop and unload daemon ───────────────────────────────────────────────────
info "Stopping daemon..."
launchctl stop "$PLIST_LABEL" 2>/dev/null || true
launchctl unload "$PLIST_PATH" 2>/dev/null || true
success "Daemon stopped"

# ── Remove launchd plist ─────────────────────────────────────────────────────
if [[ -f "$PLIST_PATH" ]]; then
    info "Removing launchd plist..."
    rm "$PLIST_PATH"
    success "Plist removed"
fi

# ── Remove stale socket ──────────────────────────────────────────────────────
if [[ -S "/tmp/cc-voice.sock" ]]; then
    rm -f "/tmp/cc-voice.sock"
fi

# ── Remove PermissionRequest hook from ~/.claude/settings.json ───────────────
if [[ -f "$CLAUDE_SETTINGS" ]]; then
    info "Removing Claude Code hook registration..."
    python3 - <<PYEOF
import json, os

settings_path = "$CLAUDE_SETTINGS"

with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.get("hooks", {})
existing = hooks.get("PermissionRequest", [])

# Remove CC-Voice entries
filtered = [
    e for e in existing
    if not any(
        h.get("command", "").endswith("cc-voice/hook.sh")
        for h in e.get("hooks", [])
    )
]

if filtered:
    hooks["PermissionRequest"] = filtered
elif "PermissionRequest" in hooks:
    del hooks["PermissionRequest"]

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=4)
    f.write("\n")

print("  Hook removed from settings.json")
PYEOF
    success "Claude Code hook removed"
fi

# ── Remove install directory ─────────────────────────────────────────────────
if [[ -d "$INSTALL_DIR" ]]; then
    info "Removing install directory ($INSTALL_DIR)..."
    rm -rf "$INSTALL_DIR"
    success "Install directory removed"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo
success "CC-Voice uninstalled."
echo
echo "  Note: The Whisper model cache (~/.cache/whisper/) was NOT removed."
echo "  To remove it manually: rm -rf ~/.cache/whisper"
echo
