import asyncio
import hashlib
import json
import ssl
import base64
from pathlib import Path
from typing import Optional
from uuid import uuid4

import srp
import requests
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
import websockets
from rich.console import Console
from rich.panel import Panel

srp.rfc5054_enable()

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
CHUNK_SIZE = 64 * 1024  # 64 KB


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


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

        # File transfer state
        self.pending_offer: Optional[dict] = None
        self.active_send: Optional[dict] = None  # {id, path} for outgoing
        self.received_chunks: dict[str, list[bytes]] = {}
        self.transfer_meta: dict[str, dict] = {}  # id -> offer metadata
        self.download_dir = Path("./downloads")
        self._ws_ref: Optional[object] = None  # WebSocket reference for sending from input_loop

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

        if self.pending_offer:
            name = self.pending_offer.get("name", "?")
            size = _human_size(self.pending_offer.get("size", 0))
            sender = self._safe_username(self.pending_offer.get("from", "?"))
            self.console.print(
                f"[yellow bold]{sender}[/] wants to send [bold]{name}[/] ({size})"
                f" — [green]/accept[/] or [red]/reject[/]"
            )
            self.console.print("-" * 60)

        self.console.print(
            "[dim]/send <file> | /accept | /reject | q to quit[/]"
        )

    # ── File Transfer: Sending ───────────────────────────────────────────

    async def _send_offer(self, ws, filepath: str) -> None:
        path = Path(filepath).expanduser().resolve()
        if not path.is_file():
            self.error(f"File not found: {filepath}")
            return

        size = path.stat().st_size
        if size > MAX_FILE_SIZE:
            self.error(f"File too large ({_human_size(size)}). Max is {_human_size(MAX_FILE_SIZE)}")
            return

        sha = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                sha.update(chunk)

        transfer_id = str(uuid4())
        self.active_send = {"id": transfer_id, "path": str(path)}

        offer = json.dumps({
            "_ft": "offer",
            "id": transfer_id,
            "name": path.name,
            "size": size,
            "sha256": sha.hexdigest(),
        })
        encrypted = self.room_fernet.encrypt(offer.encode()).decode()
        await ws.send(encrypted)
        self.info(f"Offered {path.name} ({_human_size(size)}) — waiting for accept...")

    async def _send_file_chunks(self, ws, transfer_id: str, filepath: str) -> None:
        path = Path(filepath)
        total = path.stat().st_size
        sent = 0
        seq = 0

        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                msg = json.dumps({
                    "_ft": "chunk",
                    "id": transfer_id,
                    "seq": seq,
                    "data": base64.b64encode(chunk).decode(),
                })
                encrypted = self.room_fernet.encrypt(msg.encode()).decode()
                await ws.send(encrypted)
                sent += len(chunk)
                seq += 1
                pct = int(sent * 100 / total) if total else 100
                self.console.print(f"\r[cyan]Sending: {pct}% ({_human_size(sent)}/{_human_size(total)})[/]", end="")
                await asyncio.sleep(0.01)  # yield to event loop

        done_msg = json.dumps({"_ft": "done", "id": transfer_id})
        encrypted = self.room_fernet.encrypt(done_msg.encode()).decode()
        await ws.send(encrypted)
        self.console.print()
        self.success(f"File sent: {path.name}")
        self.active_send = None

    # ── File Transfer: Receiving ─────────────────────────────────────────

    def _handle_file_protocol(self, ft_data: dict, sender: str) -> bool:
        """Handle a file transfer protocol message. Returns True if handled."""
        ft_type = ft_data.get("_ft")
        transfer_id = ft_data.get("id")

        if ft_type == "offer":
            if sender == self.username:
                return True  # ignore our own offer echo
            self.pending_offer = {
                "id": transfer_id,
                "name": ft_data.get("name"),
                "size": ft_data.get("size"),
                "sha256": ft_data.get("sha256"),
                "from": sender,
            }
            self.transfer_meta[transfer_id] = self.pending_offer
            self.received_chunks[transfer_id] = []
            self.render_messages()
            return True

        elif ft_type == "accept":
            if self.active_send and self.active_send["id"] == transfer_id:
                # Someone accepted our offer — start sending chunks
                asyncio.create_task(
                    self._send_file_chunks(
                        self._ws_ref,
                        self.active_send["id"],
                        self.active_send["path"],
                    )
                )
            return True

        elif ft_type == "reject":
            if self.active_send and self.active_send["id"] == transfer_id:
                self.error(f"{sender} rejected the file transfer.")
                self.active_send = None
            if self.pending_offer and self.pending_offer["id"] == transfer_id:
                self.pending_offer = None
                self.render_messages()
            return True

        elif ft_type == "chunk":
            if transfer_id in self.received_chunks:
                chunk_data = base64.b64decode(ft_data.get("data", ""))
                self.received_chunks[transfer_id].append(chunk_data)
                meta = self.transfer_meta.get(transfer_id, {})
                total = meta.get("size", 0)
                received = sum(len(c) for c in self.received_chunks[transfer_id])
                pct = int(received * 100 / total) if total else 0
                self.console.print(
                    f"\r[cyan]Receiving: {pct}% ({_human_size(received)}/{_human_size(total)})[/]",
                    end="",
                )
            return True

        elif ft_type == "done":
            if transfer_id in self.received_chunks:
                self._finalize_receive(transfer_id)
            return True

        return False

    def _finalize_receive(self, transfer_id: str) -> None:
        meta = self.transfer_meta.get(transfer_id, {})
        chunks = self.received_chunks.pop(transfer_id, [])
        file_data = b"".join(chunks)

        # Verify integrity
        actual_sha = hashlib.sha256(file_data).hexdigest()
        expected_sha = meta.get("sha256", "")
        if actual_sha != expected_sha:
            self.console.print()
            self.error(f"SHA-256 mismatch! File corrupted. Expected {expected_sha[:16]}..., got {actual_sha[:16]}...")
            return

        # Save file
        self.download_dir.mkdir(parents=True, exist_ok=True)
        filename = meta.get("name", f"file_{transfer_id[:8]}")
        save_path = self.download_dir / filename

        # Avoid overwriting — append number if exists
        if save_path.exists():
            stem = save_path.stem
            suffix = save_path.suffix
            i = 1
            while save_path.exists():
                save_path = self.download_dir / f"{stem}_{i}{suffix}"
                i += 1

        save_path.write_bytes(file_data)
        self.console.print()
        self.success(f"File saved: {save_path} ({_human_size(len(file_data))}) — SHA-256 verified")
        self.pending_offer = None
        self.transfer_meta.pop(transfer_id, None)
        self.render_messages()

    # ── Core Loops ───────────────────────────────────────────────────────

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
                    text = msg_data.get("text", "")
                    sender = msg_data.get("username", "unknown")

                    # Check if this is a file transfer protocol message
                    if text.startswith('{"_ft":'):
                        try:
                            ft_data = json.loads(text)
                            if self._handle_file_protocol(ft_data, sender):
                                continue
                        except json.JSONDecodeError:
                            pass

                    self.messages.append(msg_data)
                    self.render_messages()
                elif msg_type == "user_left":
                    left_id = data.get("user_id")
                    self.users = [u for u in self.users if u.get("user_id") != left_id]
                    self.render_messages()

        except websockets.ConnectionClosed:
            self.connected = False

    async def input_loop(self, ws) -> None:
        self._ws_ref = ws
        loop = asyncio.get_event_loop()
        while self.running:
            try:
                text = await loop.run_in_executor(None, input)
                if text.lower() in ("q", "quit", "exit"):
                    self.running = False
                    break

                # File transfer commands
                if text.startswith("/send "):
                    filepath = text[6:].strip()
                    if filepath:
                        await self._send_offer(ws, filepath)
                    else:
                        self.error("Usage: /send <filepath>")
                    continue

                if text.strip() == "/accept":
                    if self.pending_offer:
                        accept_msg = json.dumps({
                            "_ft": "accept",
                            "id": self.pending_offer["id"],
                        })
                        encrypted = self.room_fernet.encrypt(accept_msg.encode()).decode()
                        await ws.send(encrypted)
                        self.info(f"Accepted transfer: {self.pending_offer['name']}")
                        self.pending_offer = None
                        self.render_messages()
                    else:
                        self.error("No pending file offer to accept.")
                    continue

                if text.strip() == "/reject":
                    if self.pending_offer:
                        reject_msg = json.dumps({
                            "_ft": "reject",
                            "id": self.pending_offer["id"],
                        })
                        encrypted = self.room_fernet.encrypt(reject_msg.encode()).decode()
                        await ws.send(encrypted)
                        self.info("Rejected file transfer.")
                        self.received_chunks.pop(self.pending_offer["id"], None)
                        self.transfer_meta.pop(self.pending_offer["id"], None)
                        self.pending_offer = None
                        self.render_messages()
                    else:
                        self.error("No pending file offer to reject.")
                    continue

                if text.strip().startswith("/"):
                    self.error(f"Unknown command: {text.strip().split()[0]}")
                    continue

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
