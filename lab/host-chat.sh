#!/usr/bin/env bash
#
# host-chat.sh — Start cmd-chat server for remote friends
#
# Usage:
#   ./lab/host-chat.sh                  # TLS on port 4000, prompts for password
#   ./lab/host-chat.sh --port 5000      # Custom port
#   ./lab/host-chat.sh --no-tls         # Disable TLS (not recommended)
#
# After starting, give your friend:
#   python3 cmd_chat.py connect YOUR_IP PORT theirname --insecure
#

set -euo pipefail

PORT="${1:-4000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NO_TLS=false

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)   PORT="$2"; shift 2 ;;
        --no-tls) NO_TLS=true; shift ;;
        *)        shift ;;
    esac
done

cd "$PROJECT_ROOT"

# Check deps
echo "Checking dependencies..."
for pkg in sanic srp cryptography websockets rich requests; do
    if ! python3 -c "import $pkg" 2>/dev/null; then
        echo "Installing missing dependencies..."
        python3 -m pip install -r requirements.txt --quiet 2>/dev/null
        break
    fi
done

# Get all IPs for display
echo ""
echo "========================================="
echo "  cmd-chat Server Setup"
echo "========================================="
echo ""
echo "Your IP addresses:"

# Tailscale IP
TS_IP=$(tailscale ip -4 2>/dev/null || true)
if [[ -n "$TS_IP" ]]; then
    echo "  Tailscale:  $TS_IP  (recommended — encrypted tunnel)"
fi

# LAN IPs
for iface in $(ip -4 -o addr show scope global 2>/dev/null | awk '{print $2}' | sort -u); do
    IP=$(ip -4 -o addr show "$iface" 2>/dev/null | awk '{print $4}' | cut -d/ -f1)
    echo "  $iface:  $IP"
done

# Public IP
PUB_IP=$(curl -sf --max-time 3 ifconfig.me 2>/dev/null || true)
if [[ -n "$PUB_IP" ]]; then
    echo "  Public:     $PUB_IP  (requires port forwarding)"
fi

echo ""
echo "========================================="

# TLS flags
SERVER_FLAGS=""
PROTO="https"
CLIENT_FLAG="--insecure"
if $NO_TLS; then
    SERVER_FLAGS="--no-tls"
    PROTO="http"
    CLIENT_FLAG="--no-tls"
fi

# Pick best IP for friend instructions
BEST_IP="${TS_IP:-$(ip -4 -o addr show scope global 2>/dev/null | head -1 | awk '{print $4}' | cut -d/ -f1)}"

echo ""
echo "Tell your friend to run:"
echo ""
echo "  git clone https://github.com/leetcrypt/cmd-chat.git"
echo "  cd cmd-chat && pip install -r requirements.txt"
echo "  python3 cmd_chat.py connect $BEST_IP $PORT THEIR_NAME $CLIENT_FLAG"
echo ""
echo "They'll be prompted for the password (same one you're about to set)."
echo "========================================="
echo ""

# Start server — password will be prompted by cmd_chat
exec python3 cmd_chat.py serve 0.0.0.0 "$PORT" $SERVER_FLAGS
