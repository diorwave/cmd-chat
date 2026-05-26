# cmd-chat Lab

Interactive 2-user test environment using tmux. Spins up a server and two chat clients side-by-side so you can send messages between them.

## Quick Start

```bash
# Default (TLS, password: labtest)
./lab/setup-lab.sh

# Plain HTTP (local testing)
./lab/setup-lab.sh --no-tls

# Custom password and port
./lab/setup-lab.sh --password mysecret --port 5000

# Custom usernames
./lab/setup-lab.sh --user1 charlie --user2 dave
```

## Attach & Navigate

```bash
tmux attach -t cmd-chat-lab
```

| Key | Action |
|-----|--------|
| `Ctrl+B` then arrow keys | Switch between panes |
| `Ctrl+B` then `z` | Zoom/unzoom current pane |
| `q` | Disconnect from chat |
| `Ctrl+C` in server pane | Stop server |

## Teardown

```bash
./lab/setup-lab.sh --teardown
```

## Layout

```
┌──────────── SERVER (127.0.0.1:4000) ─────────────┐
│ Sanic running, admin token displayed here        │
├──────── alice ───────┬──────── bob ──────────────┤
│ Type messages here   │ Type messages here        │
│ and hit Enter        │ and hit Enter             │
└──────────────────────┴───────────────────────────┘
```

## Requirements

- Python 3.10+
- tmux 3.0+
- All Python deps from `requirements.txt` (auto-installed if missing)
