from datetime import datetime, timezone
from typing import Optional
from dataclasses import asdict
import json
from sanic import Sanic, Request, response, Websocket


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def verify_password(password: Optional[str], expected: Optional[str]) -> bool:
    if not expected:
        return True
    return password == expected


def get_client_ip(request: Request) -> str:
    if forwarded := request.headers.get("x-forwarded-for"):
        return forwarded.split(",")[0].strip()
    return request.ip


def get_param(request: Request, name: str) -> Optional[str]:
    return request.args.get(name) or request.form.get(name)


def require_auth(request: Request, app: Sanic) -> Optional[response.HTTPResponse]:
    if not verify_password(get_param(request, "password"), app.ctx.admin_password):
        return response.text("Unauthorized", status=401)
    return None


async def send_state(ws: Websocket, app: Sanic) -> None:
    messages = app.ctx.message_store.get_all()
    users = app.ctx.session_store.get_all()
    await ws.send(
        json.dumps(
            {
                "type": "init",
                "messages": [asdict(m) for m in messages],
                "users": [
                    {"user_id": u.user_id, "username": u.username, "joined_at": u.created_at}
                    for u in users
                ],
            }
        )
    )


def extract_pubkey(request: Request) -> Optional[bytes]:
    if files := request.files.get("pubkey"):
        file = files[0] if isinstance(files, list) else files
        return file.body
    if raw := request.form.get("pubkey"):
        return raw.encode() if isinstance(raw, str) else raw
    if raw := request.args.get("pubkey"):
        return raw.encode() if isinstance(raw, str) else raw
    return None
