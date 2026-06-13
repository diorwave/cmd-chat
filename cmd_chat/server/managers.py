import asyncio
import logging
from typing import Optional
from sanic import Websocket

log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, Websocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, websocket: Websocket) -> None:
        async with self._lock:
            self.active_connections[user_id] = websocket
        log.info(f"{user_id} connected")

    async def disconnect(self, user_id: str) -> None:
        async with self._lock:
            if user_id in self.active_connections:
                del self.active_connections[user_id]
        log.info(f"{user_id} disconnected")

    async def broadcast(self, message: str, exclude_user: Optional[str] = None) -> None:
        async with self._lock:
            snapshot = list(self.active_connections.items())
        
        dead = []
        for uid, ws in snapshot:
            if exclude_user and uid == exclude_user:
                continue
            try:
                await ws.send(message)
            except Exception as e:
                log.debug(f"send failed {uid}: {type(e).__name__}")
                dead.append(uid)

        if dead:
            async with self._lock:
                for uid in dead:
                    self.active_connections.pop(uid, None)

    async def send_personal(self, user_id: str, message: str) -> bool:
        async with self._lock:
            if ws := self.active_connections.get(user_id):
                try:
                    await ws.send(message)
                    return True
                except Exception as e:
                    log.debug(f"personal msg failed {user_id}: {type(e).__name__}")
                    return False
        return False
