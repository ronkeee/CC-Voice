#!/usr/bin/env bash
# CC-Voice reloader
# Copy changed source files to the install dir and restart the daemon.
# Run this from your local project folder after editing daemon.py or hook.sh.
#
# Usage:
#   ./reload.sh

set -euo pipefail

INSTALL_DIR="$HOME/.local/share/cc-voice"
PLIST_LABEL="com.ccvoice.daemon"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Colours (same as install.sh) ─────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[cc-voice]${NC} $*"; }
success() { echo -e "${GREEN}[cc-voice]${NC} $*"; }
warn()    { echo -e "${YELLOW}[cc-voice]${NC} $*"; }
error()   { echo -e "${RED}[cc-voice]${NC} $*" >&2; }

# ── Guard: macOS only ─────────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Darwin" ]]; then
    error "reload.sh requires macOS."
    exit 1
fi

# ── Guard: must be installed already ─────────────────────────────────────────
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    error "CC-Voice is not installed yet. Run ./install.sh first."
    exit 1
fi

# ── Copy source files ─────────────────────────────────────────────────────────
info "Copying daemon.py  →  $INSTALL_DIR/"
cp "$SCRIPT_DIR/daemon.py" "$INSTALL_DIR/daemon.py"

info "Copying hook.sh    →  $INSTALL_DIR/"
cp "$SCRIPT_DIR/hook.sh" "$INSTALL_DIR/hook.sh"
chmod +x "$INSTALL_DIR/hook.sh"

success "Files updated"

# ── Restart daemon ────────────────────────────────────────────────────────────
info "Restarting daemon..."
launchctl stop  "$PLIST_LABEL" 2>/dev/null || true
sleep 1
launchctl start "$PLIST_LABEL" 2>/dev/null || true

# ── Health check ──────────────────────────────────────────────────────────────
info "Waiting for daemon to start..."
for i in $(seq 1 10); do
    sleep 1
    HEALTH=$(python3 - <<'PYEOF' 2>&1
import socket
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1.0)
    s.connect("/tmp/cc-voice.sock")
    s.close()
    print("ok")
except Exception as e:
    print(f"fail: {e}")
PYEOF
)
    if [[ "$HEALTH" == "ok" ]]; then
        success "Daemon restarted and healthy"
        echo
        echo "  Logs:  tail -f $INSTALL_DIR/daemon.log"
        echo
        exit 0
    fi
done

error "Daemon did not start within 10s."
warn  "Check logs: tail -20 $INSTALL_DIR/daemon.log"
exit 1
