#!/usr/bin/env bash
# CC-Voice: post-command chime
#
# Registered in ~/.claude/settings.json under hooks.PostToolUse (matcher: Bash).
# Plays a short chime after each Bash tool call — no permission dialog needed.
#
# Reads (and ignores) the PostToolUse JSON payload from stdin so Claude Code
# doesn't see a broken pipe.  Always exits 0.

INSTALL_DIR="$HOME/.local/share/cc-voice"
CHIME_PATH="$INSTALL_DIR/chime.wav"

# Consume stdin so Claude Code doesn't get a broken-pipe error
cat > /dev/null

# Generate the WAV file on first run (pure Python stdlib — no dependencies)
if [[ ! -f "$CHIME_PATH" ]]; then
    mkdir -p "$INSTALL_DIR"
    python3 - "$CHIME_PATH" <<'PYEOF'
import array, math, os, sys, wave

path = sys.argv[1]
sample_rate = 44100
duration    = 0.5        # seconds
frequency   = 880.0      # Hz — A5, pleasant "ding"
amplitude   = 28000      # 16-bit PCM max is 32767

n = int(sample_rate * duration)
samples = array.array("h")
for i in range(n):
    t = i / sample_rate
    v = amplitude * math.sin(2 * math.pi * frequency * t) * math.exp(-6.0 * t / duration)
    samples.append(int(v))

os.makedirs(os.path.dirname(path), exist_ok=True)
with wave.open(path, "w") as f:
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(sample_rate)
    f.writeframes(samples.tobytes())
PYEOF
fi

# Try platform audio players in order; silently skip if none found
for cmd in \
    "afplay $CHIME_PATH" \
    "aplay -q $CHIME_PATH" \
    "paplay $CHIME_PATH" \
    "ffplay -nodisp -autoexit -loglevel quiet $CHIME_PATH" \
    "sox $CHIME_PATH -d"
do
    player="${cmd%% *}"
    if command -v "$player" &>/dev/null; then
        $cmd 2>/dev/null &   # background so Claude Code isn't blocked
        break
    fi
done

exit 0
