import os
import urllib.parse
import ssl
import importlib
import pytest
from unittest.mock import patch, MagicMock

@pytest.fixture(autouse=True)
def restore_database_module():
    # Keep track of original env
    orig_env = os.environ.copy()
    yield
    # Restore environment
    os.environ.clear()
    os.environ.update(orig_env)
    # Reload database module without any patches to restore original engine and session local
    import bridge_app.database
    importlib.reload(bridge_app.database)

def test_database_ssl_config_require():
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@host/db?sslmode=require"}):
        with patch("sqlalchemy.ext.asyncio.create_async_engine") as mock_create_engine:
            import bridge_app.database
            importlib.reload(bridge_app.database)

            assert mock_create_engine.call_count >= 1
            called_args, called_kwargs = mock_create_engine.call_args
            connect_args = called_kwargs.get("connect_args", {})
            ssl_ctx = connect_args.get("ssl")

            assert isinstance(ssl_ctx, ssl.SSLContext)
            assert ssl_ctx.check_hostname is True
            assert ssl_ctx.verify_mode == ssl.CERT_REQUIRED

def test_database_ssl_config_disable():
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@host/db?sslmode=disable"}):
        with patch("sqlalchemy.ext.asyncio.create_async_engine") as mock_create_engine:
            import bridge_app.database
            importlib.reload(bridge_app.database)

            assert mock_create_engine.call_count >= 1
            called_args, called_kwargs = mock_create_engine.call_args
            connect_args = called_kwargs.get("connect_args", {})
            ssl_ctx = connect_args.get("ssl")

            assert ssl_ctx is False
