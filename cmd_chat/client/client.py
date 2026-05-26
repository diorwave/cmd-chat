import asyncio
import json
import ssl
import base64
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


class Client:
    def __init__(
        self,
        server: str,
        port: int,
        username: str,
        password: Optional[str] = None,
        insecure: bool = False,
        no_tls: bool = False,
    ):
        self.server = server
        self.port = port
        self.username = username
        self.password = (password or "").encode()
        self.insecure = insecure
        self.no_tls = no_tls
        self.user_id: Optional[str] = None
        self.ws_token: Optional[str] = None
        self.room_fernet: Optional[Fernet] = None

        self.console = Console()
        self.messages: list[dict] = []
        self.users: list[dict] = []
        self.connected = False
        self.running = False

    @property
    def base_url(self) -> str:
        scheme = "http" if self.no_tls else "https"
        return f"{scheme}://{self.server}:{self.port}"

    @property
    def ws_url(self) -> str:
        scheme = "ws" if self.no_tls else "wss"
        return f"{scheme}://{self.server}:{self.port}"

    def _request_kwargs(self) -> dict:
        kwargs: dict = {"timeout": 30}
        if self.insecure and not self.no_tls:
            kwargs["verify"] = False
        return kwargs

    def _ws_ssl_context(self) -> ssl.SSLContext | None:
        if self.no_tls:
            return None
        if self.insecure:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        return True  # default verification

    def success(self, message: str) -> None:
        self.console.print(f"[green]{message}[/]")

    def error(self, message: str) -> None:
        self.console.print(f"[red]{message}[/]")

    def info(self, message: str) -> None:
        self.console.print(f"[cyan]{message}[/]")

    def srp_authenticate(self) -> None:
        with self.console.status("[cyan]Starting SRP handshake...[/]", spinner="dots"):
            req_kwargs = self._request_kwargs()

            usr = srp.User(b"chat", self.password, hash_alg=srp.SHA256)
            _, A = usr.start_authentication()

            resp = requests.post(
                f"{self.base_url}/srp/init",
                json={
                    "username": self.username,
                    "A": base64.b64encode(A).decode(),
                },
                **req_kwargs,
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
                **req_kwargs,
            )
            resp.raise_for_status()
            verify_data = resp.json()

            H_AMK = base64.b64decode(verify_data["H_AMK"])
            usr.verify_session(H_AMK)

            if not usr.authenticated():
                raise ValueError("Server authentication failed")

            self.ws_token = verify_data["ws_token"]

        self.success(f"SRP authenticated (session: {self.user_id[:8]}...)")

    def decrypt_message(self, msg: dict) -> dict:
        if "text" in msg and msg["text"]:
            try:
                decrypted = self.room_fernet.decrypt(msg["text"].encode()).decode()
                msg["text"] = decrypted
            except Exception:
                msg["text"] = "[decrypt failed]"
        return msg

    @staticmethod
    def _safe_username(username: str) -> str:
        return username.replace("[", "\\[")

    def render_messages(self) -> None:
        self.console.clear()

        users_online = (
            ", ".join(self._safe_username(u.get("username", "?")) for u in self.users)
            or "none"
        )
        self.console.print(f"[dim]Online: {users_online}[/]")
        self.console.print("-" * 60)

        display_messages = (
            self.messages[-15:] if len(self.messages) > 15 else self.messages
        )

        for msg in display_messages:
            username = self._safe_username(msg.get("username", "unknown"))
            text = msg.get("text", "")
            timestamp = str(msg.get("timestamp", ""))[:19].replace("T", " ")

            style = "green" if msg.get("username") == self.username else "cyan"
            self.console.print(f"[dim]{timestamp}[/] [{style}]{username}[/]: {text}")

        if not display_messages:
            self.console.print("[dim italic]No messages yet...[/]")

        self.console.print("-" * 60)
        self.console.print("[dim]Type message and press Enter. 'q' to quit.[/]")

    async def receive_loop(self, ws) -> None:
        try:
            async for raw in ws:
                if not self.running:
                    break

                data = json.loads(raw)
                msg_type = data.get("type", "")

                if msg_type == "init":
                    messages = [
                        self.decrypt_message(m) for m in data.get("messages", [])
                    ]
                    self.messages = messages
                    self.users = data.get("users", [])
                    self.connected = True
                    self.render_messages()
                elif msg_type == "message":
                    msg_data = self.decrypt_message(data.get("data", {}))
                    self.messages.append(msg_data)
                    self.render_messages()
                elif msg_type == "user_left":
                    left_id = data.get("user_id")
                    self.users = [u for u in self.users if u.get("user_id") != left_id]
                    self.render_messages()

        except websockets.ConnectionClosed:
            self.connected = False

    async def input_loop(self, ws) -> None:
        loop = asyncio.get_event_loop()
        while self.running:
            try:
                text = await loop.run_in_executor(None, input)
                if text.lower() in ("q", "quit", "exit"):
                    self.running = False
                    break
                if text.strip():
                    encrypted = self.room_fernet.encrypt(text.encode()).decode()
                    await ws.send(encrypted)
            except (EOFError, KeyboardInterrupt):
                self.running = False
                break

    async def run_async(self) -> None:
        self.console.clear()
        self.console.print(Panel("[bold cyan]CMD Chat Client[/]", expand=False))
        self.console.print()

        try:
            self.srp_authenticate()

            self.info("Connecting to chat...")
            url = f"{self.ws_url}/ws/chat?user_id={self.user_id}&ws_token={self.ws_token}"

            ws_ssl = self._ws_ssl_context()
            async with websockets.connect(url, ssl=ws_ssl) as ws:
                self.success("Connected to chat server")
                self.running = True

                receive_task = asyncio.create_task(self.receive_loop(ws))
                input_task = asyncio.create_task(self.input_loop(ws))

                done, pending = await asyncio.wait(
                    [receive_task, input_task], return_when=asyncio.FIRST_COMPLETED
                )

                for task in pending:
                    task.cancel()

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
        asyncio.run(self.run_async())
