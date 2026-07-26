#!/bin/bash
# ── Orb Launcher ─────────────────────────────────────────────────────
# Starts llama-server + mpv desktop orb. Safe to call repeatedly
# (kills previous instances). Drop this into your i3 config:
#   exec_always --no-startup-id ~/.config/orb/start_orb.sh

export ORB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Config ───────────────────────────────────────────────────────────

# Model — set ONE of these (MODEL takes priority):
#   MODEL        : full path to a .gguf file
#   MODEL_REPO   : huggingface repo id  (e.g. ggml-org/gemma-4-26B-A4B-it-GGUF)
#   MODEL_FILE   : filename glob inside the repo snapshot (e.g. *Q4_K_M.gguf)
MODEL="${MODEL:-}"
MODEL_REPO="${MODEL_REPO:-ggml-org/gemma-4-26B-A4B-it-GGUF}"
MODEL_FILE="${MODEL_FILE:-*Q4_K_M.gguf}"

LLAMA_DIR="${LLAMA_DIR:-$HOME/.local/llama}"
LLAMA_PORT="${LLAMA_PORT:-8080}"
LLAMA_CTX="${LLAMA_CTX:-32000}"
LLAMA_THREADS="${LLAMA_THREADS:-4}"
LLAMA_BATCH_THREADS="${LLAMA_BATCH_THREADS:-4}"
LLAMA_LOG="/tmp/llama-server.log"

# ── Logging (chat client) ────────────────────────────────────────────
# ORB_LOG       – verbosity: error | warn | debug | trace   (unset = errors only)
# ORB_LOG_FILE  – where client.py writes its log           (default: /tmp/orb.log)
ORB_LOG="${ORB_LOG:-}"
ORB_LOG_FILE="${ORB_LOG_FILE:-/tmp/orb.log}"
export ORB_LOG ORB_LOG_FILE

ORB_GEOMETRY="${ORB_GEOMETRY:-100x100-10+12}"

# ── Resolve model path ──────────────────────────────────────────────
if [[ -z "$MODEL" ]]; then
    HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}/hub"
    REPO_DIR="$HF_CACHE/models--$(echo "$MODEL_REPO" | sed 's|/|--|g')"
    if [[ -d "$REPO_DIR/snapshots" ]]; then
        MODEL=$(find "$REPO_DIR/snapshots" -name "$MODEL_FILE" | head -1)
    fi
    if [[ -z "$MODEL" ]]; then
        echo "ERROR: Could not find model matching '$MODEL_FILE' in $REPO_DIR" >&2
        echo "Set MODEL=/path/to/model.gguf or MODEL_REPO + MODEL_FILE" >&2
        exit 1
    fi
fi

# ── Kill previous instances ─────────────────────────────────────────
killall -q llama-server 2>/dev/null
killall -q mpv 2>/dev/null  # TODO: this kills ALL mpv — use --x11-name pid tracking if needed

# Wait for llama-server port to be released (up to 5s)
for _ in $(seq 1 20); do
    ss -tlnp 2>/dev/null | grep -q ":${LLAMA_PORT} " || break
    sleep 0.25
done
# Force kill if still hanging
killall -q -9 llama-server 2>/dev/null
sleep 0.2

# ── Start llama-server ──────────────────────────────────────────────
LD_LIBRARY_PATH="$LLAMA_DIR/lib" \
  "$LLAMA_DIR/bin/llama-server" \
    -c "$LLAMA_CTX" \
    -m "$MODEL" \
    -t "$LLAMA_THREADS" \
    -tb "$LLAMA_BATCH_THREADS" \
    --port "$LLAMA_PORT" \
    > "$LLAMA_LOG" 2>&1 &

# ── Generate input conf with resolved paths ─────────────────────────
# mpv's input.conf parser eats ${} as property expansion, so we can't
# use shell variables there. Generate it at launch with absolute paths.
RUNTIME_CONF="$ORB_DIR/.orb_input_runtime.conf"
cat > "$RUNTIME_CONF" << EOF
MOUSE_ENTER run "/bin/bash" "$ORB_DIR/orb_hover.sh"
MOUSE_LEAVE run "/bin/bash" "$ORB_DIR/orb_leave.sh"
MBTN_LEFT run "/bin/bash" "$ORB_DIR/orb_action.sh"
EOF

# ── Start mpv orb ───────────────────────────────────────────────────
mpv \
  --geometry="$ORB_GEOMETRY" \
  --vo=gpu \
  --gpu-context=x11egl \
  --background=none \
  --x11-name=chaos_orb \
  --no-border \
  --loop-file=inf \
  --ontop \
  --force-window \
  --input-ipc-server=/tmp/orb-socket \
  --no-osc \
  --no-osd-bar \
  --input-conf="$RUNTIME_CONF" \
  "$ORB_DIR/animations/idle.gif" &

