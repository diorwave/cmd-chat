import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import base64
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock, PropertyMock

import requests
import websockets
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


class TestClientProperties:
    def test_base_url_different_ports(self):
        client = Client("example.com", 8080, "user", "pass")
        assert client.base_url == "https://example.com:8080"

    def test_ws_url_different_ports(self):
        client = Client("example.com", 8080, "user", "pass")
        assert client.ws_url == "wss://example.com:8080"

    def test_base_url_localhost(self):
        client = Client("localhost", 443, "user", "pass")
        assert client.base_url == "https://localhost:443"

    def test_no_tls_urls(self):
        client = Client("example.com", 8080, "user", "pass", no_tls=True)
        assert client.base_url == "http://example.com:8080"
        assert client.ws_url == "ws://example.com:8080"

    def test_password_encoding_unicode(self):
        client = Client("localhost", 3000, "user", "пароль123")
        assert client.password == "пароль123".encode()

    def test_password_encoding_special_chars(self):
        client = Client("localhost", 3000, "user", "p@$$w0rd!#%")
        assert client.password == b"p@$$w0rd!#%"


class TestSRPAuthentication:
    @patch("cmd_chat.client.client.requests.post")
    def test_srp_authenticate_success(self, mock_post, client, room_salt):
        import srp

        init_response = MagicMock()
        init_response.json.return_value = {
            "user_id": "test-user-id-12345",
            "B": base64.b64encode(os.urandom(256)).decode(),
            "salt": base64.b64encode(os.urandom(16)).decode(),
            "room_salt": base64.b64encode(room_salt).decode(),
        }
        init_response.raise_for_status = MagicMock()

        verify_response = MagicMock()
        verify_response.json.return_value = {
            "H_AMK": base64.b64encode(os.urandom(32)).decode(),
            "ws_token": "test-ws-token-hex",
        }
        verify_response.raise_for_status = MagicMock()

        mock_post.side_effect = [init_response, verify_response]

        with patch("cmd_chat.client.client.srp.User") as mock_srp_user:
            mock_usr = MagicMock()
            mock_usr.start_authentication.return_value = (None, os.urandom(256))
            mock_usr.process_challenge.return_value = os.urandom(32)
            mock_usr.verify_session.return_value = None
            mock_usr.authenticated.return_value = True
            mock_srp_user.return_value = mock_usr

            client.srp_authenticate()

        assert client.user_id == "test-user-id-12345"
        assert client.room_fernet is not None
        assert client.ws_token == "test-ws-token-hex"

    @patch("cmd_chat.client.client.requests.post")
    def test_srp_authenticate_init_fails(self, mock_post, client):
        mock_post.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=500, text="Server error")
        )

        with pytest.raises(requests.exceptions.HTTPError):
            client.srp_authenticate()

    @patch("cmd_chat.client.client.requests.post")
    def test_srp_authenticate_verify_fails(self, mock_post, client, room_salt):
        init_response = MagicMock()
        init_response.json.return_value = {
            "user_id": "test-user-id",
            "B": base64.b64encode(os.urandom(256)).decode(),
            "salt": base64.b64encode(os.urandom(16)).decode(),
            "room_salt": base64.b64encode(room_salt).decode(),
        }
        init_response.raise_for_status = MagicMock()

        verify_response = MagicMock()
        verify_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=MagicMock(status_code=401, text="Invalid proof")
        )

        mock_post.side_effect = [init_response, verify_response]

        with patch("cmd_chat.client.client.srp.User") as mock_srp_user:
            mock_usr = MagicMock()
            mock_usr.start_authentication.return_value = (None, os.urandom(256))
            mock_usr.process_challenge.return_value = os.urandom(32)
            mock_srp_user.return_value = mock_usr

            with pytest.raises(requests.exceptions.HTTPError):
                client.srp_authenticate()

    @patch("cmd_chat.client.client.requests.post")
    def test_srp_authenticate_challenge_none(self, mock_post, client, room_salt):
        init_response = MagicMock()
        init_response.json.return_value = {
            "user_id": "test-user-id",
            "B": base64.b64encode(os.urandom(256)).decode(),
            "salt": base64.b64encode(os.urandom(16)).decode(),
            "room_salt": base64.b64encode(room_salt).decode(),
        }
        init_response.raise_for_status = MagicMock()

        mock_post.return_value = init_response

        with patch("cmd_chat.client.client.srp.User") as mock_srp_user:
            mock_usr = MagicMock()
            mock_usr.start_authentication.return_value = (None, os.urandom(256))
            mock_usr.process_challenge.return_value = None
            mock_srp_user.return_value = mock_usr

            with pytest.raises(ValueError, match="SRP challenge processing failed"):
                client.srp_authenticate()

    @patch("cmd_chat.client.client.requests.post")
    def test_srp_authenticate_server_not_authenticated(
        self, mock_post, client, room_salt
    ):
        init_response = MagicMock()
        init_response.json.return_value = {
            "user_id": "test-user-id",
            "B": base64.b64encode(os.urandom(256)).decode(),
            "salt": base64.b64encode(os.urandom(16)).decode(),
            "room_salt": base64.b64encode(room_salt).decode(),
        }
        init_response.raise_for_status = MagicMock()

        verify_response = MagicMock()
        verify_response.json.return_value = {
            "H_AMK": base64.b64encode(os.urandom(32)).decode(),
            "ws_token": "test-ws-token-hex",
        }
        verify_response.raise_for_status = MagicMock()

        mock_post.side_effect = [init_response, verify_response]

        with patch("cmd_chat.client.client.srp.User") as mock_srp_user:
            mock_usr = MagicMock()
            mock_usr.start_authentication.return_value = (None, os.urandom(256))
            mock_usr.process_challenge.return_value = os.urandom(32)
            mock_usr.verify_session.return_value = None
            mock_usr.authenticated.return_value = False
            mock_srp_user.return_value = mock_usr

            with pytest.raises(ValueError, match="Server authentication failed"):
                client.srp_authenticate()

    @patch("cmd_chat.client.client.requests.post")
    def test_srp_authenticate_connection_timeout(self, mock_post, client):
        mock_post.side_effect = requests.exceptions.Timeout()

        with pytest.raises(requests.exceptions.Timeout):
            client.srp_authenticate()


class TestDecryptMessage:
    def test_decrypt_multiple_messages(self, client, room_fernet, room_salt):
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        room_key = hkdf.derive(client.password)
        client.room_fernet = Fernet(base64.urlsafe_b64encode(room_key))

        messages = ["Hello", "World", "Test123", "Привет мир"]
        for original in messages:
            encrypted = room_fernet.encrypt(original.encode()).decode()
            msg = {"text": encrypted, "username": "other"}
            decrypted = client.decrypt_message(msg)
            assert decrypted["text"] == original

    def test_decrypt_preserves_other_fields(self, client, room_fernet, room_salt):
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        room_key = hkdf.derive(client.password)
        client.room_fernet = Fernet(base64.urlsafe_b64encode(room_key))

        encrypted = room_fernet.encrypt(b"test").decode()
        msg = {
            "text": encrypted,
            "username": "sender",
            "timestamp": "2024-01-01T12:00:00",
            "id": "msg-123",
        }

        decrypted = client.decrypt_message(msg)

        assert decrypted["text"] == "test"
        assert decrypted["username"] == "sender"
        assert decrypted["timestamp"] == "2024-01-01T12:00:00"
        assert decrypted["id"] == "msg-123"

    def test_decrypt_wrong_key_marks_failed(self, client):

        fernet1 = Fernet(Fernet.generate_key())
        encrypted = fernet1.encrypt(b"secret").decode()

        client.room_fernet = Fernet(Fernet.generate_key())

        msg = {"text": encrypted, "username": "other"}
        decrypted = client.decrypt_message(msg)

        assert decrypted["text"] == "[decrypt failed]"

    def test_decrypt_corrupted_ciphertext(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())

        msg = {"text": "YWJjZGVmZ2hpamtsbW5vcA==", "username": "other"}
        decrypted = client.decrypt_message(msg)

        assert decrypted["text"] == "[decrypt failed]"

    def test_decrypt_none_text(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())

        msg = {"text": None, "username": "other"}
        result = client.decrypt_message(msg)

        assert result["text"] is None


class TestReceiveLoopExtended:
    @pytest.mark.asyncio
    async def test_receive_multiple_messages_sequence(self, client, room_fernet):
        client.room_fernet = room_fernet
        client.running = True
        client.messages = []

        msg1 = room_fernet.encrypt(b"First").decode()
        msg2 = room_fernet.encrypt(b"Second").decode()
        msg3 = room_fernet.encrypt(b"Third").decode()

        messages = [
            json.dumps(
                {"type": "message", "data": {"text": msg1, "username": "user1"}}
            ),
            json.dumps(
                {"type": "message", "data": {"text": msg2, "username": "user2"}}
            ),
            json.dumps(
                {"type": "message", "data": {"text": msg3, "username": "user1"}}
            ),
        ]

        mock_ws = AsyncMock()
        mock_ws.__aiter__.return_value = messages

        with patch.object(client, "render_messages"):
            await client.receive_loop(mock_ws)

        assert len(client.messages) == 3
        assert client.messages[0]["text"] == "First"
        assert client.messages[1]["text"] == "Second"
        assert client.messages[2]["text"] == "Third"

    @pytest.mark.asyncio
    async def test_receive_stops_when_not_running(self, client, room_fernet):
        client.room_fernet = room_fernet
        client.running = False

        mock_ws = AsyncMock()
        mock_ws.__aiter__.return_value = [
            json.dumps({"type": "message", "data": {"text": "test", "username": "u"}})
        ]

        with patch.object(client, "render_messages") as mock_render:
            await client.receive_loop(mock_ws)
            mock_render.assert_not_called()

    @pytest.mark.asyncio
    async def test_receive_handles_connection_closed(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.running = True
        client.connected = True

        mock_ws = AsyncMock()
        mock_ws.__aiter__.side_effect = websockets.ConnectionClosed(None, None)

        await client.receive_loop(mock_ws)

        assert client.connected is False

    @pytest.mark.asyncio
    async def test_receive_unknown_message_type(self, client, room_fernet):
        client.room_fernet = room_fernet
        client.running = True
        client.messages = []

        unknown_msg = json.dumps({"type": "unknown_type", "data": {}})

        mock_ws = AsyncMock()
        mock_ws.__aiter__.return_value = [unknown_msg]

        with patch.object(client, "render_messages") as mock_render:
            await client.receive_loop(mock_ws)

            mock_render.assert_not_called()

    @pytest.mark.asyncio
    async def test_receive_user_joined_updates_list(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.running = True
        client.users = []

        init_msg = json.dumps(
            {
                "type": "init",
                "messages": [],
                "users": [
                    {"user_id": "1", "username": "alice"},
                    {"user_id": "2", "username": "bob"},
                ],
            }
        )

        mock_ws = AsyncMock()
        mock_ws.__aiter__.return_value = [init_msg]

        with patch.object(client, "render_messages"):
            await client.receive_loop(mock_ws)

        assert len(client.users) == 2
        assert client.users[0]["username"] == "alice"
        assert client.users[1]["username"] == "bob"

    @pytest.mark.asyncio
    async def test_receive_multiple_users_leave(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.running = True
        client.users = [
            {"user_id": "1", "username": "alice"},
            {"user_id": "2", "username": "bob"},
            {"user_id": "3", "username": "charlie"},
        ]

        leave_msgs = [
            json.dumps({"type": "user_left", "user_id": "1"}),
            json.dumps({"type": "user_left", "user_id": "3"}),
        ]

        mock_ws = AsyncMock()
        mock_ws.__aiter__.return_value = leave_msgs

        with patch.object(client, "render_messages"):
            await client.receive_loop(mock_ws)

        assert len(client.users) == 1
        assert client.users[0]["username"] == "bob"


class TestInputLoopExtended:
    @pytest.mark.asyncio
    async def test_input_keyboard_interrupt(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.running = True

        mock_ws = AsyncMock()

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock(side_effect=KeyboardInterrupt())
            mock_loop.return_value.run_in_executor = mock_executor

            await client.input_loop(mock_ws)

        assert client.running is False

    @pytest.mark.asyncio
    async def test_input_eof_error(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.running = True

        mock_ws = AsyncMock()

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock(side_effect=EOFError())
            mock_loop.return_value.run_in_executor = mock_executor

            await client.input_loop(mock_ws)

        assert client.running is False

    @pytest.mark.asyncio
    async def test_input_exit_command(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.running = True

        mock_ws = AsyncMock()

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock(return_value="exit")
            mock_loop.return_value.run_in_executor = mock_executor

            await client.input_loop(mock_ws)

        assert client.running is False

    @pytest.mark.asyncio
    async def test_input_case_insensitive_quit(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.running = True

        mock_ws = AsyncMock()
        inputs = iter(["QUIT"])

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock(side_effect=lambda _, __: next(inputs))
            mock_loop.return_value.run_in_executor = mock_executor

            await client.input_loop(mock_ws)

        assert client.running is False

    @pytest.mark.asyncio
    async def test_input_multiple_messages_then_quit(self, client, room_fernet):
        client.room_fernet = room_fernet
        client.running = True

        mock_ws = AsyncMock()
        sent = []
        mock_ws.send = AsyncMock(side_effect=lambda m: sent.append(m))

        inputs = iter(["msg1", "msg2", "msg3", "q"])

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock(side_effect=lambda _, __: next(inputs))
            mock_loop.return_value.run_in_executor = mock_executor

            await client.input_loop(mock_ws)

        assert len(sent) == 3
        assert room_fernet.decrypt(sent[0].encode()).decode() == "msg1"
        assert room_fernet.decrypt(sent[1].encode()).decode() == "msg2"
        assert room_fernet.decrypt(sent[2].encode()).decode() == "msg3"

    @pytest.mark.asyncio
    async def test_input_whitespace_only_not_sent(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.running = True

        mock_ws = AsyncMock()
        inputs = iter(["\t", "\n", "  \t  ", "q"])

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_executor = AsyncMock(side_effect=lambda _, __: next(inputs))
            mock_loop.return_value.run_in_executor = mock_executor

            await client.input_loop(mock_ws)

        mock_ws.send.assert_not_called()


class TestRunAsync:
    @pytest.mark.asyncio
    async def test_run_connection_error(self, client):
        with patch.object(client, "srp_authenticate") as mock_auth:
            mock_auth.side_effect = requests.exceptions.ConnectionError()

            with patch.object(client.console, "clear"):
                with patch.object(client.console, "print"):
                    await client.run_async()

    @pytest.mark.asyncio
    async def test_run_http_error(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        with patch.object(client, "srp_authenticate") as mock_auth:
            mock_auth.side_effect = requests.exceptions.HTTPError(
                response=mock_response
            )

            with patch.object(client.console, "clear"):
                with patch.object(client.console, "print"):
                    await client.run_async()

    @pytest.mark.asyncio
    async def test_run_value_error(self, client):
        with patch.object(client, "srp_authenticate") as mock_auth:
            mock_auth.side_effect = ValueError("Auth failed")

            with patch.object(client.console, "clear"):
                with patch.object(client.console, "print"):
                    await client.run_async()

    @pytest.mark.asyncio
    async def test_run_generic_exception(self, client):
        with patch.object(client, "srp_authenticate") as mock_auth:
            mock_auth.side_effect = RuntimeError("Unexpected")

            with patch.object(client.console, "clear"):
                with patch.object(client.console, "print"):
                    await client.run_async()

    @pytest.mark.asyncio
    async def test_run_successful_connection_and_disconnect(self, client):
        client.user_id = "test-id-123"
        client.ws_token = "test-token"

        with patch.object(client, "srp_authenticate"):
            with patch("cmd_chat.client.client.websockets.connect") as mock_connect:
                mock_ws = AsyncMock()
                mock_connect.return_value.__aenter__.return_value = mock_ws

                with patch.object(
                    client, "receive_loop", new_callable=AsyncMock
                ) as mock_recv:
                    with patch.object(
                        client, "input_loop", new_callable=AsyncMock
                    ) as mock_input:

                        mock_input.return_value = None
                        mock_recv.return_value = None

                        with patch.object(client.console, "clear"):
                            with patch.object(client.console, "print"):
                                await client.run_async()


class TestRenderMessagesExtended:
    def test_render_own_message_green(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.username = "testuser"
        client.messages = [
            {
                "username": "testuser",
                "text": "my msg",
                "timestamp": "2024-01-01T12:00:00",
            }
        ]
        client.users = []

        printed = []
        with patch.object(client.console, "clear"):
            with patch.object(
                client.console, "print", side_effect=lambda x: printed.append(x)
            ):
                client.render_messages()

        msg_output = [p for p in printed if "my msg" in str(p)]
        assert len(msg_output) == 1
        assert "green" in str(msg_output[0])

    def test_render_other_message_cyan(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.username = "testuser"
        client.messages = [
            {
                "username": "other",
                "text": "their msg",
                "timestamp": "2024-01-01T12:00:00",
            }
        ]
        client.users = []

        printed = []
        with patch.object(client.console, "clear"):
            with patch.object(
                client.console, "print", side_effect=lambda x: printed.append(x)
            ):
                client.render_messages()

        msg_output = [p for p in printed if "their msg" in str(p)]
        assert len(msg_output) == 1
        assert "cyan" in str(msg_output[0])

    def test_render_timestamp_formatting(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.messages = [
            {
                "username": "user",
                "text": "test",
                "timestamp": "2024-01-15T14:30:45.123456",
            }
        ]
        client.users = []

        printed = []
        with patch.object(client.console, "clear"):
            with patch.object(
                client.console, "print", side_effect=lambda x: printed.append(x)
            ):
                client.render_messages()

        msg_output = [p for p in printed if "2024-01-15 14:30:45" in str(p)]
        assert len(msg_output) == 1

    def test_render_users_online_display(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.messages = []
        client.users = [
            {"user_id": "1", "username": "alice"},
            {"user_id": "2", "username": "bob"},
            {"user_id": "3", "username": "charlie"},
        ]

        printed = []
        with patch.object(client.console, "clear"):
            with patch.object(
                client.console, "print", side_effect=lambda x: printed.append(x)
            ):
                client.render_messages()

        online_line = [p for p in printed if "Online:" in str(p)]
        assert len(online_line) == 1
        assert "alice" in str(online_line[0])
        assert "bob" in str(online_line[0])
        assert "charlie" in str(online_line[0])

    def test_render_no_users_shows_none(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.messages = []
        client.users = []

        printed = []
        with patch.object(client.console, "clear"):
            with patch.object(
                client.console, "print", side_effect=lambda x: printed.append(x)
            ):
                client.render_messages()

        online_line = [p for p in printed if "Online:" in str(p)]
        assert "none" in str(online_line[0])

    def test_render_missing_username_shows_unknown(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.messages = [{"text": "test", "timestamp": "2024-01-01T12:00:00"}]
        client.users = []

        printed = []
        with patch.object(client.console, "clear"):
            with patch.object(
                client.console, "print", side_effect=lambda x: printed.append(x)
            ):
                client.render_messages()

        msg_output = [p for p in printed if "unknown" in str(p)]
        assert len(msg_output) >= 1

    def test_render_missing_timestamp(self, client):
        client.room_fernet = Fernet(Fernet.generate_key())
        client.messages = [{"username": "user", "text": "test"}]
        client.users = []

        with patch.object(client.console, "clear"):
            with patch.object(client.console, "print"):

                client.render_messages()


class TestE2EEncryptionFlow:

    def test_same_password_same_key(self, room_salt):

        password = b"shared_secret"

        hkdf1 = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        key1 = base64.urlsafe_b64encode(hkdf1.derive(password))

        hkdf2 = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        key2 = base64.urlsafe_b64encode(hkdf2.derive(password))

        fernet1 = Fernet(key1)
        fernet2 = Fernet(key2)

        ciphertext = fernet1.encrypt(b"Hello from client 1")

        plaintext = fernet2.decrypt(ciphertext)

        assert plaintext == b"Hello from client 1"

    def test_different_password_cannot_decrypt(self, room_salt):

        hkdf1 = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        key1 = base64.urlsafe_b64encode(hkdf1.derive(b"correct_password"))

        hkdf2 = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        key2 = base64.urlsafe_b64encode(hkdf2.derive(b"wrong_password"))

        fernet1 = Fernet(key1)
        fernet2 = Fernet(key2)

        ciphertext = fernet1.encrypt(b"Secret message")

        with pytest.raises(Exception):
            fernet2.decrypt(ciphertext)

    def test_server_cannot_read_without_password(self, room_salt):

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        client_key = base64.urlsafe_b64encode(hkdf.derive(b"client_password"))
        client_fernet = Fernet(client_key)

        ciphertext = client_fernet.encrypt(b"Private message")

        server_random_key = Fernet.generate_key()
        server_fernet = Fernet(server_random_key)

        with pytest.raises(Exception):
            server_fernet.decrypt(ciphertext)


class TestEdgeCases:
    def test_empty_username(self):
        client = Client("localhost", 3000, "", "password")
        assert client.username == ""

    def test_very_long_message(self, client, room_fernet, room_salt):
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        room_key = hkdf.derive(client.password)
        client.room_fernet = Fernet(base64.urlsafe_b64encode(room_key))

        long_message = "x" * 10000
        encrypted = room_fernet.encrypt(long_message.encode()).decode()

        msg = {"text": encrypted, "username": "other"}
        decrypted = client.decrypt_message(msg)

        assert decrypted["text"] == long_message

    def test_unicode_message(self, client, room_fernet, room_salt):
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        room_key = hkdf.derive(client.password)
        client.room_fernet = Fernet(base64.urlsafe_b64encode(room_key))

        unicode_msg = "Привет 世界 🎉 مرحبا"
        encrypted = room_fernet.encrypt(unicode_msg.encode()).decode()

        msg = {"text": encrypted, "username": "other"}
        decrypted = client.decrypt_message(msg)

        assert decrypted["text"] == unicode_msg

    def test_special_characters_in_message(self, client, room_fernet, room_salt):
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=room_salt,
            info=b"cmd-chat-room-key",
        )
        room_key = hkdf.derive(client.password)
        client.room_fernet = Fernet(base64.urlsafe_b64encode(room_key))

        special_msg = '<script>alert("xss")</script> & "quotes" \'single\' \n\t\r'
        encrypted = room_fernet.encrypt(special_msg.encode()).decode()

        msg = {"text": encrypted, "username": "other"}
        decrypted = client.decrypt_message(msg)

        assert decrypted["text"] == special_msg

    def test_port_zero(self):
        client = Client("localhost", 0, "user", "pass")
        assert client.port == 0
        assert client.base_url == "https://localhost:0"

    def test_ipv6_server(self):
        client = Client("::1", 3000, "user", "pass")
        assert client.base_url == "https://::1:3000"
