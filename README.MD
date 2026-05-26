<div align="center">

# CMD-CHAT

### end-to-end encrypted terminal chat with file transfer

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

</div>

---

Encrypted chat that runs in your terminal. You host the server, you control the room. Close the window — everything's gone. Messages and files are encrypted client-side before the server ever sees them.

## Features

- **End-to-end encrypted** — messages encrypted with Fernet (AES-128-CBC + HMAC) before leaving your machine
- **TLS by default** — auto-generated self-signed certs, or bring your own
- **SRP authentication** — password never sent over the network (zero-knowledge proof)
- **Encrypted file transfer** — `/send`, `/accept`, `/reject` with SHA-256 verification
- **RAM only** — nothing written to disk on the server
- **Rate limiting** — brute-force protection on auth endpoints
- **No IP leaks** — client IPs never broadcast to other users
- **Password hidden** — prompted securely via `getpass`, never visible in `ps` or shell history

## Install

```bash
git clone https://github.com/diorwave/cmd-chat.git
cd cmd-chat
pip install -r requirements.txt
```

## Quick Start

**Host a chat room:**

```bash
python3 cmd_chat.py serve 0.0.0.0 3000
```

You'll be prompted for a room password (hidden input). An admin token and TLS cert path will print to the console.

**Connect to a chat room:**

```bash
python3 cmd_chat.py connect SERVER_IP 3000 yourname --insecure
```

`--insecure` is needed for self-signed certs. You'll be prompted for the room password.

## Securing Your Connection

### Tailscale (recommended)

Both parties install [Tailscale](https://tailscale.com). Traffic goes through an encrypted WireGuard tunnel. No port forwarding, works across NATs.

```bash
# Host
python3 cmd_chat.py serve 0.0.0.0 3000

# Friend connects using your Tailscale IP
python3 cmd_chat.py connect 100.x.x.x 3000 theirname --insecure
```

Find your Tailscale IP: `tailscale ip -4`

### LAN (same network)

Use your local IP. Both devices must be on the same WiFi/network.

```bash
python3 cmd_chat.py connect 192.168.1.x 3000 theirname --insecure
```

### Public Internet

Requires port forwarding on your router (TCP port 3000 to your machine). Use `--cert` and `--key` with a real certificate for production use.

```bash
python3 cmd_chat.py connect PUBLIC_IP 3000 theirname --insecure
```

Find your public IP: `curl ifconfig.me`

## Sharing the Room Password

The password must be shared outside the chat. Never send it over an unencrypted channel.

1. **In person** — tell them verbally
2. **Signal** — disappearing message set to 30 seconds
3. **One-time link** — [onetimesecret.com](https://onetimesecret.com) (self-destructs after one view)
4. **Split it** — send half via Telegram, half via SMS

## Chat Commands

| Command | Action |
|---------|--------|
| `/send <filepath>` | Propose a file transfer to the room |
| `/accept` | Accept a pending file offer |
| `/reject` | Decline a pending file offer |
| `q` | Disconnect |

### File Transfer

Files are chunked (64KB), encrypted with the room key, and relayed through the server as opaque ciphertext. The server never sees file names, contents, or metadata.

```
alice> /send report.pdf
bob>   "alice wants to send report.pdf (1.2 MB) — /accept or /reject"
bob>   /accept
       Receiving: 100% (1.2 MB/1.2 MB)
       File saved: ./downloads/report.pdf — SHA-256 verified
```

- Max file size: 50 MB
- Files saved to `./downloads/` relative to where the client was launched
- SHA-256 integrity check on every transfer

## CLI Reference

### Server

```bash
python3 cmd_chat.py serve <bind_ip> <port> [options]
```

| Flag | Purpose |
|------|---------|
| `--password`, `-p` | Room password (prompted if omitted) |
| `--cert` | Path to TLS certificate |
| `--key` | Path to TLS private key |
| `--no-tls` | Disable TLS (local dev only) |

### Client

```bash
python3 cmd_chat.py connect <server_ip> <port> <username> [options]
```

| Flag | Purpose |
|------|---------|
| `--password`, `-p` | Room password (prompted if omitted) |
| `--insecure`, `-k` | Skip TLS cert verification (self-signed certs) |
| `--no-tls` | Connect without TLS |

### Environment Variable

Set `CMD_CHAT_PASSWORD` to skip the password prompt for both server and client.

## Helper Scripts

### `./lab/host-chat.sh`

One-command server setup. Detects your IPs (Tailscale, LAN, public), prints the exact connect command your friend needs, then starts the server.

```bash
./lab/host-chat.sh              # TLS on port 4000
./lab/host-chat.sh --port 5000  # custom port
./lab/host-chat.sh --no-tls     # disable TLS
```

### `./lab/setup-lab.sh`

Spins up a tmux session with the server and two chat clients side-by-side for local testing.

```bash
./lab/setup-lab.sh                          # default lab
./lab/setup-lab.sh --no-tls --port 4001     # plain HTTP
./lab/setup-lab.sh --user1 alice --user2 bob
./lab/setup-lab.sh --teardown               # clean up
```

Attach with `tmux attach -t cmd-chat-lab`. Switch panes with `Ctrl+B` then arrow keys.

## How It Works

```
CLIENT                              SERVER                         CLIENT
  │                                   │                               │
  │── POST /srp/init {A} ──────────► │                               │
  │◄── {B, salt, room_salt} ──────── │                               │
  │                                   │                               │
  │  derive room_key = HKDF(password, room_salt)                      │
  │                                   │                               │
  │── POST /srp/verify {M} ────────► │                               │
  │◄── {H_AMK, ws_token} ─────────── │                               │
  │                                   │                               │
  │══ WSS /ws/chat?ws_token ════════► │ ◄══════════════════════════════│
  │                                   │                               │
  │  encrypt(msg, room_key) ────────► │ ──── ciphertext ────────────► │
  │                                   │           decrypt(ciphertext, room_key)
  │                                   │                               │
  │  server stores ONLY ciphertext    │                               │
  │  server CANNOT read messages      │                               │
```

**SRP (Secure Remote Password)** — both sides prove they know the password without transmitting it. A network observer learns nothing.

**Room Key** — derived independently by each client via `HKDF(password, room_salt)`. All clients with the same password get the same key. The server never has the key.

**WebSocket Auth** — HMAC-SHA256 token issued after SRP verification. Prevents session hijacking.

## Admin

The server prints an admin token at startup. Use it to clear message history:

```bash
curl -k -X DELETE https://SERVER:3000/clear \
  -H "Authorization: Bearer <admin-token>"
```

## License

MIT
