import time
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import asdict
import json
from sanic import Request, Sanic, Websocket


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_client_ip(request: Request) -> str:
    if forwarded := request.headers.get("x-forwarded-for"):
        return forwarded.split(",")[0].strip()
    return request.ip


async def send_state(ws: Websocket, app: Sanic) -> None:
    messages = app.ctx.message_store.get_all()
    users = app.ctx.session_store.get_all()
    await ws.send(
        json.dumps(
            {
                "type": "init",
                "messages": [asdict(m) for m in messages],
                "users": [
                    {"user_id": u.user_id, "username": u.username} for u in users
                ],
            }
        )
    )


class RateLimiter:
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        timestamps = self._requests[key]
        timestamps[:] = [t for t in timestamps if now - t < self.window]
        if len(timestamps) >= self.max_requests:
            return False
        timestamps.append(now)
        return True
