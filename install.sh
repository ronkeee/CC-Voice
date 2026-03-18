#!/usr/bin/env bash
# CC-Voice installer
# Installs the CC-Voice daemon and registers it with Claude Code.
#
# Usage:
#   ./install.sh
#
# Requirements:
#   - macOS 10.15+
#   - Python 3.10+
#   - Claude Code installed with ~/.claude/settings.json present

set -euo pipefail

INSTALL_DIR="$HOME/.local/share/cc-voice"
PLIST_LABEL="com.ccvoice.daemon"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Colour

info()    { echo -e "${BLUE}[cc-voice]${NC} $*"; }
success() { echo -e "${GREEN}[cc-voice]${NC} $*"; }
warn()    { echo -e "${YELLOW}[cc-voice]${NC} $*"; }
error()   { echo -e "${RED}[cc-voice]${NC} $*" >&2; }

# ── Step 1: Platform check ──────────────────────────────────────────────────
info "Checking platform..."
if [[ "$(uname -s)" != "Darwin" ]]; then
    error "CC-Voice requires macOS. Detected: $(uname -s)"
    exit 1
fi
success "macOS detected"

# ── Step 2: Python version check ────────────────────────────────────────────
info "Checking Python version..."
if ! command -v python3 &>/dev/null; then
    error "python3 not found. Install Python 3.10+ from https://python.org"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [[ "$PYTHON_MAJOR" -lt 3 ]] || { [[ "$PYTHON_MAJOR" -eq 3 ]] && [[ "$PYTHON_MINOR" -lt 10 ]]; }; then
    error "Python 3.10+ required. Found: $PYTHON_VERSION"
    exit 1
fi
success "Python $PYTHON_VERSION detected"

# ── Step 3: Check Claude settings exist ─────────────────────────────────────
info "Checking Claude Code settings..."
if [[ ! -f "$CLAUDE_SETTINGS" ]]; then
    error "Claude Code settings not found at $CLAUDE_SETTINGS"
    error "Is Claude Code installed? Run 'claude' at least once to initialise it."
    exit 1
fi
success "Found $CLAUDE_SETTINGS"

# ── Step 4: Create install directory ────────────────────────────────────────
info "Creating install directory at $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
success "Install directory ready"

# ── Step 5: Create virtualenv ───────────────────────────────────────────────
info "Creating Python virtualenv..."
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    python3 -m venv "$INSTALL_DIR/venv"
    success "Virtualenv created"
else
    success "Virtualenv already exists, reusing"
fi

# ── Step 6: Install Python dependencies ─────────────────────────────────────
info "Installing Python dependencies (this may take a few minutes)..."
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
"$INSTALL_DIR/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" --quiet
success "Python dependencies installed"

# ── Step 7: Pre-download Whisper tiny model ──────────────────────────────────
info "Pre-downloading Whisper 'tiny' model (~75MB, runs offline after this)..."
"$INSTALL_DIR/venv/bin/python" -c "import whisper; whisper.load_model('tiny'); print('  Model ready')"
success "Whisper model downloaded"

# ── Step 8: Copy daemon and hook files ──────────────────────────────────────
info "Installing daemon and hook..."
cp "$SCRIPT_DIR/daemon.py" "$INSTALL_DIR/daemon.py"
cp "$SCRIPT_DIR/hook.sh"   "$INSTALL_DIR/hook.sh"
chmod +x "$INSTALL_DIR/hook.sh"
success "Files installed"

# ── Step 9: Generate chime.wav ──────────────────────────────────────────────
info "Generating chime sound..."
"$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/daemon.py" --generate-chime
success "Chime generated"

# ── Step 10: Install launchd plist ──────────────────────────────────────────
info "Installing launchd agent..."
mkdir -p "$HOME/Library/LaunchAgents"

# Substitute $USER and $HOME paths into the plist template
sed \
    -e "s|__VENV_PYTHON__|${INSTALL_DIR}/venv/bin/python|g" \
    -e "s|__DAEMON_PY__|${INSTALL_DIR}/daemon.py|g" \
    -e "s|__STDOUT_LOG__|${INSTALL_DIR}/daemon.log|g" \
    -e "s|__STDERR_LOG__|${INSTALL_DIR}/daemon.err|g" \
    "$SCRIPT_DIR/com.ccvoice.daemon.plist" > "$PLIST_PATH"

success "Plist installed at $PLIST_PATH"

# ── Step 11: Merge PermissionRequest hook into ~/.claude/settings.json ───────
info "Registering PermissionRequest hook with Claude Code..."

python3 - <<PYEOF
import json, os, sys

settings_path = os.path.expanduser("$CLAUDE_SETTINGS")
hook_path     = "$INSTALL_DIR/hook.sh"

with open(settings_path) as f:
    settings = json.load(f)

hook_entry = {
    "matcher": "",
    "hooks": [{"type": "command", "command": hook_path}],
}

hooks = settings.setdefault("hooks", {})
existing = hooks.get("PermissionRequest", [])

# Remove any prior CC-Voice entry (idempotent reinstall)
existing = [
    e for e in existing
    if not any(
        h.get("command", "").endswith("cc-voice/hook.sh")
        for h in e.get("hooks", [])
    )
]

existing.append(hook_entry)
hooks["PermissionRequest"] = existing

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=4)
    f.write("\n")

print("  Settings updated")
PYEOF

success "Claude Code hook registered"

# ── Step 12: Load and start daemon via launchctl ────────────────────────────
info "Starting daemon via launchd..."

# Unload first if already loaded (clean reinstall)
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load   "$PLIST_PATH"
launchctl start  "$PLIST_LABEL" 2>/dev/null || true

success "Daemon started"

# ── Step 13: Health check ────────────────────────────────────────────────────
info "Health check (waiting 4s for daemon to start)..."
sleep 4

HEALTH=$(python3 - <<'PYEOF' 2>&1
import socket, sys
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2.0)
    s.connect("/tmp/cc-voice.sock")
    s.close()
    print("ok")
except Exception as e:
    print(f"fail: {e}")
PYEOF
)

if [[ "$HEALTH" == "ok" ]]; then
    success "Daemon is running and accepting connections"
else
    warn "Daemon health check failed: $HEALTH"
    warn "Check logs at $INSTALL_DIR/daemon.log"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo
success "CC-Voice installed successfully!"
echo
echo "  How it works:"
echo "    When Claude Code asks for approval, CC-Voice will:"
echo "      1. Play a chime sound"
echo "      2. Record 5 seconds of audio"
echo "      3. Transcribe with Whisper (offline)"
echo "      4. Map your voice to: allow / deny / ask"
echo
echo "  Voice commands:"
echo "    Allow:  yes, yeah, approve, go, proceed, one, 1 ..."
echo "    Deny:   no, nope, deny, reject, stop, cancel, two, 2 ..."
echo "    Unsure: anything else → Claude Code shows its normal prompt"
echo
echo "  Management:"
echo "    Logs:   $INSTALL_DIR/daemon.log"
echo "    Stop:   launchctl stop $PLIST_LABEL"
echo "    Start:  launchctl start $PLIST_LABEL"
echo "    Remove: ./uninstall.sh"
echo
