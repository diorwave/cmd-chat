import asyncio
import json
import base64
import sys
from datetime import date
from typing import Optional

import srp
import requests
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
import websockets
from rich.console import Console
from rich.panel import Panel

srp.rfc5054_enable()


def _fmt_join_time(iso: str) -> str:
    if len(iso) < 19:
        return iso
    day, time = iso[:10], iso[11:19]
    return time if day == str(date.today()) else f"{day} {time}"


class Client:
    def __init__(
        self, server: str, port: int, username: str, password: Optional[str] = None,
        is_host: bool = False,
    ):
        self.server = server
        self.port = port
        self.username = username
        self.password = (password or "").encode()
        self.is_host = is_host
        self.user_id: Optional[str] = None
        self.fernet: Optional[Fernet] = None
        self.room_fernet: Optional[Fernet] = None

        self.console = Console()
        self.messages: list[dict] = []
        self.users: list[dict] = []
        self.connected = False
        self.running = False
        self._session_completed = False

    @property
    def base_url(self) -> str:
        return f"http://{self.server}:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.server}:{self.port}"

    def success(self, message: str) -> None:
        self.console.print(f"[green]✓ {message}[/]")

    def error(self, message: str) -> None:
        self.console.print(f"[red]✗ {message}[/]")

    def info(self, message: str) -> None:
        self.console.print(f"[cyan]• {message}[/]")

    def srp_authenticate(self) -> None:
        with self.console.status("[cyan]Starting SRP handshake...[/]", spinner="dots"):

            usr = srp.User(b"chat", self.password, hash_alg=srp.SHA256)
            _, A = usr.start_authentication()

            resp = requests.post(
                f"{self.base_url}/srp/init",
                json={
                    "username": self.username,
                    "A": base64.b64encode(A).decode(),
                },
                timeout=30,
            )
            resp.raise_for_status()
            init_data = resp.json()

            self.user_id = init_data["user_id"]
            B = base64.b64decode(init_data["B"])
            salt = base64.b64decode(init_data["salt"])
            room_salt = base64.b64decode(init_data["room_salt"])

            hkdf = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=room_salt,
                info=b"cmd-chat-room-key",
            )
            room_key = hkdf.derive(self.password)
            self.room_fernet = Fernet(base64.urlsafe_b64encode(room_key))

            M = usr.process_challenge(salt, B)

            if M is None:
                raise ValueError("SRP challenge processing failed")

            resp = requests.post(
                f"{self.base_url}/srp/verify",
                json={
                    "user_id": self.user_id,
                    "username": self.username,
                    "M": base64.b64encode(M).decode(),
                },
                timeout=30,
            )
            resp.raise_for_status()
            verify_data = resp.json()

            H_AMK = base64.b64decode(verify_data["H_AMK"])
            usr.verify_session(H_AMK)

            if not usr.authenticated():
                raise ValueError("Server authentication failed")

            session_key = base64.b64decode(verify_data["session_key"])
            self.fernet = Fernet(session_key)

        self.success(f"SRP authenticated (session: {self.user_id[:8]}...)")

    def decrypt_message(self, msg: dict) -> dict:
        if "text" in msg and msg["text"]:
            try:
                decrypted = self.room_fernet.decrypt(msg["text"].encode()).decode()
                msg["text"] = decrypted
            except Exception:
                msg["text"] = "[decrypt failed]"
        return msg

    def render_messages(self) -> None:
        self.console.clear()

        height = self.console.size.height
        width = self.console.size.width
        # fixed rows: online + sep + sep + hint + input line = 5
        message_rows = max(15, height - 5)

        users_online = ", ".join(
            f"{u.get('username', '?')} ({u.get('joined_at', '')})"
            for u in self.users
        ) or "none"
        self.console.print(f"[dim]Online: {users_online}[/]")
        self.console.print("─" * width)

        display_messages = self.messages[-message_rows:]
        # 1 slot reserved when empty for the "no messages" line
        content_lines = len(display_messages) if display_messages else 1

        for _ in range(message_rows - content_lines):
            self.console.print()

        if display_messages:
            for msg in display_messages:
                username = msg.get("username", "unknown")
                text = msg.get("text", "")
                timestamp = str(msg.get("timestamp", ""))[:19].replace("T", " ")
                style = "green" if username == self.username else "cyan"
                self.console.print(f"[dim]{timestamp}[/] [{style}]{username}[/]: {text}")
        else:
            self.console.print("[dim italic]No messages yet...[/]")

        self.console.print("─" * width)
        self.console.print("[dim]Type message and press Enter. 'q' to quit.[/]")

    async def receive_loop(self, ws) -> None:
        try:
            async for raw in ws:
                if not self.running:
                    break

                data = json.loads(raw)
                msg_type = data.get("type", "")

                if msg_type == "init":
                    self.messages = [self.decrypt_message(m) for m in data.get("messages", [])]
                    self.users = [
                        {
                            "user_id": u.get("user_id"),
                            "username": u.get("username", "?"),
                            "joined_at": _fmt_join_time(u.get("joined_at", "")),
                        }
                        for u in data.get("users", [])
                    ]
                    self.connected = True
                    self.render_messages()
                elif msg_type == "message":
                    msg_data = self.decrypt_message(data.get("data", {}))
                    self.messages.append(msg_data)
                    self.render_messages()
                elif msg_type == "user_joined":
                    self.users.append({
                        "user_id": data.get("user_id"),
                        "username": data.get("username", "?"),
                        "joined_at": _fmt_join_time(data.get("joined_at", "")),
                    })
                    self.render_messages()
                elif msg_type == "user_left":
                    left_id = data.get("user_id")
                    self.users = [u for u in self.users if u.get("user_id") != left_id]
                    self.render_messages()

        except websockets.ConnectionClosed:
            if self.running:
                self.console.print("\n[yellow]Server has shut down.[/]")
                self.running = False
            self.connected = False

    async def input_loop(self, ws) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        try:
            while self.running:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n\r")
                if text.lower() in ("q", "quit", "exit"):
                    if self.is_host and self.room_fernet:
                        farewell = self.room_fernet.encrypt(
                            "Server shutting down — byeee! 👋".encode()
                        ).decode()
                        await ws.send(farewell)
                        await asyncio.sleep(0.3)
                    self.running = False
                    break
                if text.strip():
                    encrypted = self.room_fernet.encrypt(text.encode()).decode()
                    await ws.send(encrypted)
        finally:
            transport.close()

    async def run_async(self) -> None:
        self.console.clear()
        self.console.print(Panel("[bold cyan]CMD Chat Client[/]", expand=False))
        self.console.print()

        try:
            self.srp_authenticate()

            self.info("Connecting to chat...")
            url = f"{self.ws_url}/ws/chat?user_id={self.user_id}"

            async with websockets.connect(url, proxy=None) as ws:
                self.success("Connected to chat server")
                self.running = True

                receive_task = asyncio.create_task(self.receive_loop(ws))
                input_task = asyncio.create_task(self.input_loop(ws))

                done, pending = await asyncio.wait(
                    [receive_task, input_task], return_when=asyncio.FIRST_COMPLETED
                )

                for task in pending:
                    task.cancel()

            self._session_completed = True
            self.console.print("\n[yellow]Disconnected[/]")

        except requests.exceptions.ConnectionError:
            self.error(f"Cannot connect to {self.base_url}")
        except requests.exceptions.HTTPError as e:
            self.error(f"Server error: {e.response.status_code} - {e.response.text}")
        except ValueError as e:
            self.error(f"Authentication failed: {e}")
        except Exception:
            import traceback

            self.error("Error occurred")
            traceback.print_exc()

    def run(self) -> None:
        try:
            asyncio.run(self.run_async())
        finally:
            if self._session_completed:
                self.console.clear()
                self.console.print("[dim]Session ended.[/]")
