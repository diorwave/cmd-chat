import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import uuid
import pytest
from sanic_testing import TestManager
import os
from sanic import Sanic
from sanic_ext import Extend

from cmd_chat.server.managers import ConnectionManager
from cmd_chat.server.stores import MessageStore, UserSessionStore
from cmd_chat.server.srp_auth import SRPAuthManager
from cmd_chat.server.routes import register_routes


@pytest.fixture
def app():
    name = f"test-{uuid.uuid4().hex[:8]}"

    app = Sanic(name)
    Extend(app)

    app.ctx.message_store = MessageStore()
    app.ctx.session_store = UserSessionStore()
    app.ctx.connection_manager = ConnectionManager()
    app.ctx.srp_manager = SRPAuthManager("testpassword")
    app.ctx.room_salt = os.urandom(16)
    app.ctx.ws_secret = os.urandom(32)
    app.ctx.admin_token = "test-admin-token"
    from cmd_chat.server.helpers import RateLimiter
    app.ctx.rate_limiter = RateLimiter(max_requests=100, window_seconds=60)
    app.ctx.cleanup_task = None

    register_routes(app)
    TestManager(app)

    return app


@pytest.fixture
def test_client(app):
    return app.test_client
