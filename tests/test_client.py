import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import uuid
import base64
import json
import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from cmd_chat.client.client import Client


@pytest.fixture
def client():
    return Client(
        server="127.0.0.1",
        port=3000,
        username="testuser",
        password="testpassword",
    )


@pytest.fixture
def room_salt():
    return os.urandom(16)


@pytest.fixture
def room_fernet(room_salt):

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=room_salt,
        info=b"cmd-chat-room-key",
    )
    room_key = hkdf.derive(b"testpassword")
    return Fernet(base64.urlsafe_b64encode(room_key))


class TestClientInit:
    def test_client_creation(self, client):
        assert client.server == "127.0.0.1"
        assert client.port == 3000
        assert client.username == "testuser"
        assert client.password == b"testpassword"
        assert client.user_id is None
        assert client.ws_token is None
        assert client.room_fernet is None
        assert client.connected is False
        assert client.running is False

    def test_client_urls(self, client):
        assert client.base_url == "https://127.0.0.1:3000"
        assert client.ws_url == "wss://127.0.0.1:3000"

    def test_client_no_tls_urls(self):
        c = Client("127.0.0.1", 3000, "user", "pass", no_tls=True)
        assert c.base_url == "http://127.0.0.1:3000"
        assert c.ws_url == "ws://127.0.0.1:3000"

    def test_client_empty_password(self):
        client = Client("localhost", 8080, "user", None)
        assert client.password == b""


class TestEncryption:
    def test_decrypt_message_success(self, client, room_salt, room_fernet):

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        room_key = hkdf.derive(client.password)
        client.room_fernet = Fernet(base64.urlsafe_b64encode(room_key))

        original_text = "Hello, World!"
        encrypted = room_fernet.encrypt(original_text.encode()).decode()

        msg = {"text": encrypted, "username": "other"}
        decrypted_msg = client.decrypt_message(msg)

        assert decrypted_msg["text"] == original_text
        assert decrypted_msg["username"] == "other"

    def test_decrypt_message_failure(self, client):

        client.room_fernet = Fernet(Fernet.generate_key())

        msg = {"text": "not-valid-ciphertext", "username": "other"}
        decrypted_msg = client.decrypt_message(msg)

        assert decrypted_msg["text"] == "[decrypt failed]"

    def test_decrypt_message_empty_text(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())

        msg = {"text": "", "username": "other"}
        result = client.decrypt_message(msg)

        assert result["text"] == ""

    def test_decrypt_message_no_text_field(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())

        msg = {"username": "other"}
        result = client.decrypt_message(msg)

        assert "text" not in result

    def test_hkdf_deterministic(self, room_salt):

        password = b"testpassword"

        hkdf1 = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        key1 = hkdf1.derive(password)

        hkdf2 = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        key2 = hkdf2.derive(password)

        assert key1 == key2

    def test_hkdf_different_passwords(self, room_salt):

        hkdf1 = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        key1 = hkdf1.derive(b"password1")

        hkdf2 = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        key2 = hkdf2.derive(b"password2")

        assert key1 != key2

    def test_hkdf_different_salts(self):

        password = b"testpassword"

        hkdf1 = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=os.urandom(16),
            info=b"cmd-chat-room-key",
        )
        key1 = hkdf1.derive(password)

        hkdf2 = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=os.urandom(16),
            info=b"cmd-chat-room-key",
        )
        key2 = hkdf2.derive(password)

        assert key1 != key2


class TestMessageHandling:
    def test_render_messages_empty(self, client, capsys):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.messages = []
        client.users = []

        with patch.object(client.console, "clear"):
            client.render_messages()

    def test_render_messages_with_data(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.messages = [
            {
                "username": "testuser",
                "text": "Hello",
                "timestamp": "2024-01-01T12:00:00",
            },
            {"username": "other", "text": "Hi", "timestamp": "2024-01-01T12:01:00"},
        ]
        client.users = [
            {"user_id": "1", "username": "testuser"},
            {"user_id": "2", "username": "other"},
        ]

        with patch.object(client.console, "clear"):
            client.render_messages()

    def test_messages_limit_15(self, client):

        client.room_fernet = Fernet(Fernet.generate_key())
        client.messages = [
            {"username": "user", "text": f"msg{i}", "timestamp": "2024-01-01T12:00:00"}
            for i in range(20)
        ]
        client.users = []

        with patch.object(client.console, "clear"):
            with patch.object(client.console, "print") as mock_print:
                client.render_messages()

                msg_calls = [
                    call
                    for call in mock_print.call_args_list
                    if any("msg" in str(arg) for arg in call[0])
                ]
                assert len(msg_calls) == 15


class TestReceiveLoop:
    @pytest.mark.asyncio
    async def test_receive_init_message(self, client, room_fernet):
        client.room_fernet = room_fernet
        client.running = True

        encrypted_text = room_fernet.encrypt(b"Hello").decode()
        init_data = json.dumps(
            {
                "type": "init",
                "messages": [{"text": encrypted_text, "username": "other"}],
                "users": [{"user_id": "123", "username": "other"}],
            }
        )

        mock_ws = AsyncMock()
        mock_ws.__aiter__.return_value = [init_data]

        with patch.object(client, "render_messages"):
            await client.receive_loop(mock_ws)

        assert client.connected is True
        assert len(client.messages) == 1
        assert client.messages[0]["text"] == "Hello"
        assert len(client.users) == 1

    @pytest.mark.asyncio
    async def test_receive_message(self, client, room_fernet):
        client.room_fernet = room_fernet
        client.running = True
        client.messages = []

        encrypted_text = room_fernet.encrypt(b"New message").decode()
        msg_data = json.dumps(
            {
                "type": "message",
                "data": {"text": encrypted_text, "username": "sender"},
            }
        )

        mock_ws = AsyncMock()
        mock_ws.__aiter__.return_value = [msg_data]

        with patch.object(client, "render_messages"):
            await client.receive_loop(mock_ws)

        assert len(client.messages) == 1
        assert client.messages[0]["text"] == "New message"

    @pytest.mark.asyncio
    async def test_receive_user_left(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.running = True
        client.users = [
            {"user_id": "123", "username": "user1"},
            {"user_id": "456", "username": "user2"},
        ]

        left_data = json.dumps(
            {
                "type": "user_left",
                "user_id": "123",
            }
        )

        mock_ws = AsyncMock()
        mock_ws.__aiter__.return_value = [left_data]

        with patch.object(client, "render_messages"):
            await client.receive_loop(mock_ws)

        assert len(client.users) == 1
        assert client.users[0]["user_id"] == "456"


class TestInputLoop:
    @pytest.mark.asyncio
    async def test_send_encrypted_message(self, client, room_fernet):
        client.room_fernet = room_fernet
        client.running = True

        mock_ws = AsyncMock()
        sent_messages = []
        mock_ws.send = AsyncMock(side_effect=lambda m: sent_messages.append(m))

        inputs = iter(["hello", "q"])

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock(side_effect=lambda _, __: next(inputs))
            mock_loop.return_value.run_in_executor = mock_executor

            await client.input_loop(mock_ws)

        assert len(sent_messages) == 1
        decrypted = room_fernet.decrypt(sent_messages[0].encode()).decode()
        assert decrypted == "hello"

    @pytest.mark.asyncio
    async def test_quit_command(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.running = True

        mock_ws = AsyncMock()

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock(return_value="quit")
            mock_loop.return_value.run_in_executor = mock_executor

            await client.input_loop(mock_ws)

        assert client.running is False

    @pytest.mark.asyncio
    async def test_empty_message_not_sent(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.running = True

        mock_ws = AsyncMock()
        inputs = iter(["", "   ", "q"])

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock(side_effect=lambda _, __: next(inputs))
            mock_loop.return_value.run_in_executor = mock_executor

            await client.input_loop(mock_ws)

        mock_ws.send.assert_not_called()


class TestConsoleOutput:
    def test_success_message(self, client, capsys):
        client.success("Test success")

    def test_error_message(self, client, capsys):
        client.error("Test error")

    def test_info_message(self, client, capsys):
        client.info("Test info")
