#!/usr/bin/env bash
#
# cmd-chat Lab — Interactive 2-user encrypted chat test environment
#
# Usage:
#   ./lab/setup-lab.sh                    # TLS mode (default, auto-generates self-signed cert)
#   ./lab/setup-lab.sh --no-tls           # Plain HTTP mode (local testing only)
#   ./lab/setup-lab.sh --password secret  # Set room password (default: prompted or "labtest")
#   ./lab/setup-lab.sh --port 5000        # Custom port (default: 4000)
#   ./lab/setup-lab.sh --teardown         # Kill existing lab session
#
# Attach to the running lab:
#   tmux attach -t cmd-chat-lab
#
# Pane navigation:
#   Ctrl+B then arrow keys   — switch between panes
#   Ctrl+B then z            — zoom/unzoom current pane
#   Type 'q' in a chat pane  — disconnect that user
#   Ctrl+C in server pane    — stop server
#

set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────

SESSION="cmd-chat-lab"
PORT="${PORT:-4000}"
PASSWORD=""
NO_TLS=false
TEARDOWN=false
USER1="alice"
USER2="bob"

# ─── Parse Arguments ────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-tls)     NO_TLS=true; shift ;;
        --password)   PASSWORD="$2"; shift 2 ;;
        --port)       PORT="$2"; shift 2 ;;
        --user1)      USER1="$2"; shift 2 ;;
        --user2)      USER2="$2"; shift 2 ;;
        --teardown)   TEARDOWN=true; shift ;;
        -h|--help)
            head -20 "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# ─── Resolve project root ───────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ─── Teardown ────────────────────────────────────────────────────────────────

if $TEARDOWN; then
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        tmux kill-session -t "$SESSION"
        echo "Killed session: $SESSION"
    else
        echo "No session to kill."
    fi
    exit 0
fi

# ─── Pre-flight checks ──────────────────────────────────────────────────────

check_command() {
    if ! command -v "$1" &>/dev/null; then
        echo "ERROR: $1 not found. Install it first." >&2
        exit 1
    fi
}

check_command tmux
check_command python3

# Check dependencies
echo "Checking Python dependencies..."
MISSING=()
for pkg in sanic srp cryptography websockets rich requests; do
    if ! python3 -c "import $pkg" 2>/dev/null; then
        MISSING+=("$pkg")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    echo "Installing missing packages: ${MISSING[*]}"
    python3 -m pip install -r "$PROJECT_ROOT/requirements.txt" --quiet 2>/dev/null
    echo "Dependencies installed."
else
    echo "All dependencies OK."
fi

# Check if port is available
if ss -tlnp 2>/dev/null | grep -q ":${PORT} " || lsof -i ":${PORT}" &>/dev/null; then
    echo "ERROR: Port $PORT is already in use." >&2
    echo "  Try: $0 --port 4001" >&2
    exit 1
fi

# ─── Password resolution ────────────────────────────────────────────────────

if [[ -z "$PASSWORD" ]]; then
    if [[ -n "${CMD_CHAT_PASSWORD:-}" ]]; then
        PASSWORD="$CMD_CHAT_PASSWORD"
    else
        PASSWORD="labtest"
        echo "Using default password: $PASSWORD"
    fi
fi
export CMD_CHAT_PASSWORD="$PASSWORD"

# ─── Kill existing session if any ────────────────────────────────────────────

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Killing existing $SESSION session..."
    tmux kill-session -t "$SESSION"
    sleep 1
fi

# ─── Build TLS flags ────────────────────────────────────────────────────────

SERVER_FLAGS=""
CLIENT_FLAGS=""
if $NO_TLS; then
    SERVER_FLAGS="--no-tls"
    CLIENT_FLAGS="--no-tls"
    PROTO="http"
else
    CLIENT_FLAGS="--insecure"
    PROTO="https"
fi

# ─── Create tmux session ────────────────────────────────────────────────────

echo ""
echo "Setting up cmd-chat lab..."
echo "  Server:  $PROTO://127.0.0.1:$PORT"
echo "  Users:   $USER1, $USER2"
echo "  TLS:     $( $NO_TLS && echo 'disabled' || echo 'enabled (self-signed)' )"
echo ""

# Pane 1: Server (top)
tmux new-session -d -s "$SESSION" -n chat -c "$PROJECT_ROOT" -x 200 -y 50

# Pane 2: Client alice (bottom-left)
tmux split-window -v -t "$SESSION:chat" -p 75 -c "$PROJECT_ROOT" 2>/dev/null || \
    tmux split-window -v -t "$SESSION:chat" -c "$PROJECT_ROOT"

# Pane 3: Client bob (bottom-right)
tmux split-window -h -t "$SESSION:chat" -c "$PROJECT_ROOT" 2>/dev/null || true

# Get pane IDs
PANES=($(tmux list-panes -t "$SESSION:chat" -F '#{pane_id}'))
SERVER_PANE="${PANES[0]}"
ALICE_PANE="${PANES[1]}"
BOB_PANE="${PANES[2]}"

# Label panes
tmux select-pane -t "$SERVER_PANE" -T "SERVER ($PROTO://127.0.0.1:$PORT)"
tmux select-pane -t "$ALICE_PANE" -T "$USER1"
tmux select-pane -t "$BOB_PANE" -T "$USER2"
tmux set -t "$SESSION" pane-border-format " #{pane_title} "
tmux set -t "$SESSION" pane-border-status top

# Resize server pane smaller
tmux resize-pane -t "$SERVER_PANE" -y 10

# ─── Start server ───────────────────────────────────────────────────────────

tmux send-keys -t "$SERVER_PANE" \
    "cd '$PROJECT_ROOT' && CMD_CHAT_PASSWORD='$PASSWORD' python3 cmd_chat.py serve 127.0.0.1 $PORT $SERVER_FLAGS" Enter

# Wait for server to be ready
echo -n "Waiting for server"
for i in $(seq 1 15); do
    if $NO_TLS; then
        curl -sf "http://127.0.0.1:$PORT/health" &>/dev/null && break
    else
        curl -sfk "https://127.0.0.1:$PORT/health" &>/dev/null && break
    fi
    echo -n "."
    sleep 1
done
echo " ready!"

# ─── Connect clients ────────────────────────────────────────────────────────

tmux send-keys -t "$ALICE_PANE" \
    "cd '$PROJECT_ROOT' && CMD_CHAT_PASSWORD='$PASSWORD' python3 cmd_chat.py connect 127.0.0.1 $PORT $USER1 $CLIENT_FLAGS" Enter

sleep 2

tmux send-keys -t "$BOB_PANE" \
    "cd '$PROJECT_ROOT' && CMD_CHAT_PASSWORD='$PASSWORD' python3 cmd_chat.py connect 127.0.0.1 $PORT $USER2 $CLIENT_FLAGS" Enter

sleep 2

# ─── Verify ─────────────────────────────────────────────────────────────────

ALICE_OUT=$(tmux capture-pane -t "$ALICE_PANE" -p 2>/dev/null)
BOB_OUT=$(tmux capture-pane -t "$BOB_PANE" -p 2>/dev/null)

if echo "$ALICE_OUT" | grep -q "Online:" && echo "$BOB_OUT" | grep -q "Online:"; then
    echo ""
    echo "Lab is running! Both users connected."
    echo ""
    echo "  Attach:    tmux attach -t $SESSION"
    echo "  Teardown:  $0 --teardown"
    echo ""
    echo "  Switch panes:  Ctrl+B then arrow keys"
    echo "  Zoom pane:     Ctrl+B then z"
    echo "  Quit chat:     type 'q' in a chat pane"
    echo ""
else
    echo ""
    echo "WARNING: One or both clients may not have connected."
    echo "Attach and check: tmux attach -t $SESSION"
fi
