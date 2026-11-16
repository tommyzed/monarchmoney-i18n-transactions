import os
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete

from bridge_app import env_check
from bridge_app.database import AsyncSessionLocal, Base, engine
from bridge_app.models import Credentials
from bridge_app.services.monarch import get_latest_credentials, push_transaction

ALL_REQUIRED_VARS = {
    "DATABASE_URL": "sqlite+aiosqlite:///file:testdb?mode=memory&cache=shared&uri=true",
    "GEMINI_API_KEY": "test-gemini-key",
    "GEMINI_MODEL": "gemini-test-model",
    "FERNET_KEY": Fernet.generate_key().decode(),
    "UNLOCK_SECRET": "test-unlock-secret",
    "MM_EMAIL": "test@test.com",
    "MM_ACCOUNT": "Test Account",
}


def _set_all_required(monkeypatch, exclude=None, blank=None):
    for k in env_check.REQUIRED_ENV_VARS:
        monkeypatch.delenv(k, raising=False)
    for k in ("APP_ENV", "ENVIRONMENT", "ENV", "DEV", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"):
        monkeypatch.delenv(k, raising=False)

    for k, v in ALL_REQUIRED_VARS.items():
        if k == exclude:
            continue
        if k == blank:
            monkeypatch.setenv(k, "   ")
        else:
            monkeypatch.setenv(k, v)


def test_required_env_vars_list():
    assert set(env_check.REQUIRED_ENV_VARS) == {
        "DATABASE_URL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "FERNET_KEY",
        "UNLOCK_SECRET",
        "MM_EMAIL",
        "MM_ACCOUNT",
    }


@pytest.mark.parametrize("missing_var", env_check.REQUIRED_ENV_VARS)
def test_validate_startup_env_exits_when_var_missing(monkeypatch, capsys, missing_var):
    _set_all_required(monkeypatch, exclude=missing_var)
    monkeypatch.setattr(env_check, "is_test_environment", lambda: False)
    monkeypatch.setattr(env_check, "load_dotenv", lambda **kwargs: False)

    with pytest.raises(SystemExit) as exc_info:
        env_check.validate_startup_env()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert missing_var in captured.err


@pytest.mark.parametrize("blank_var", env_check.REQUIRED_ENV_VARS)
def test_validate_startup_env_exits_when_var_blank(monkeypatch, capsys, blank_var):
    _set_all_required(monkeypatch, blank=blank_var)
    monkeypatch.setattr(env_check, "is_test_environment", lambda: False)
    monkeypatch.setattr(env_check, "load_dotenv", lambda **kwargs: False)

    with pytest.raises(SystemExit) as exc_info:
        env_check.validate_startup_env()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert blank_var in captured.err


def test_validate_startup_env_succeeds_when_all_set(monkeypatch):
    _set_all_required(monkeypatch)
    monkeypatch.setattr(env_check, "is_test_environment", lambda: False)
    monkeypatch.setattr(env_check, "load_dotenv", lambda **kwargs: False)

    env_check.validate_startup_env()


def test_validate_startup_env_allows_missing_database_url_in_dev(monkeypatch):
    _set_all_required(monkeypatch, exclude="DATABASE_URL")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setattr(env_check, "is_test_environment", lambda: False)
    monkeypatch.setattr(env_check, "load_dotenv", lambda **kwargs: False)

    env_check.validate_startup_env()


@pytest.mark.parametrize("missing_var", ["MM_ACCOUNT", "MM_EMAIL", "GEMINI_API_KEY", "FERNET_KEY", "UNLOCK_SECRET", "GEMINI_MODEL"])
def test_validate_startup_env_still_requires_other_vars_in_dev(monkeypatch, missing_var):
    _set_all_required(monkeypatch, exclude=missing_var)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setattr(env_check, "is_test_environment", lambda: False)
    monkeypatch.setattr(env_check, "load_dotenv", lambda **kwargs: False)

    with pytest.raises(SystemExit) as exc_info:
        env_check.validate_startup_env()
    assert exc_info.value.code == 1


def test_is_dev_environment_flags(monkeypatch):
    for k in ("APP_ENV", "ENVIRONMENT", "ENV", "DEV", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"):
        monkeypatch.delenv(k, raising=False)

    assert env_check.is_dev_environment() is False

    monkeypatch.setenv("APP_ENV", "dev")
    assert env_check.is_dev_environment() is True

    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    assert env_check.is_dev_environment() is False

    monkeypatch.delenv("RAILWAY_ENVIRONMENT")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEV", "1")
    assert env_check.is_dev_environment() is False


def test_startup_stops_before_database_import_in_subprocess(tmp_path):
    """Verify importing bridge_app.main stops immediately before database module or engine setup."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    clean_env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": repo_root,
        # Provide all except MM_ACCOUNT
        "DATABASE_URL": "postgresql://user:pass@localhost/db",
        "GEMINI_API_KEY": "test-key",
        "GEMINI_MODEL": "gemini-test",
        "FERNET_KEY": ALL_REQUIRED_VARS["FERNET_KEY"],
        "UNLOCK_SECRET": "test-secret",
        "MM_EMAIL": "test@test.com",
    }
    # Run from tmp_path (no .env file present) so local .env does not mask missing vars
    code = (
        "import sys; "
        "import bridge_app.main"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=clean_env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "MM_ACCOUNT" in proc.stderr
    assert "LIFESPAN: Connecting to" not in proc.stdout


def test_startup_allows_dev_sqlite_fallback_in_subprocess(tmp_path):
    """Verify APP_ENV=dev falls back to local SQLite when DATABASE_URL is omitted."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    clean_env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": repo_root,
        "APP_ENV": "dev",
        "GEMINI_API_KEY": "test-key",
        "GEMINI_MODEL": "gemini-test",
        "FERNET_KEY": ALL_REQUIRED_VARS["FERNET_KEY"],
        "UNLOCK_SECRET": "test-secret",
        "MM_EMAIL": "test@test.com",
        "MM_ACCOUNT": "My Account",
    }
    code = (
        "import bridge_app.database as db; "
        "assert db.DATABASE_URL == 'sqlite+aiosqlite:///./bridge.db', db.DATABASE_URL"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=clean_env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_startup_blocks_sqlite_fallback_on_railway_even_with_dev_flag(tmp_path):
    """Verify production platform markers forbid SQLite fallback even if APP_ENV=dev."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    clean_env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": repo_root,
        "APP_ENV": "dev",
        "RAILWAY_ENVIRONMENT": "production",
        "GEMINI_API_KEY": "test-key",
        "GEMINI_MODEL": "gemini-test",
        "FERNET_KEY": ALL_REQUIRED_VARS["FERNET_KEY"],
        "UNLOCK_SECRET": "test-secret",
        "MM_EMAIL": "test@test.com",
        "MM_ACCOUNT": "My Account",
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import bridge_app.main"],
        cwd=str(tmp_path),
        env=clean_env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "DATABASE_URL" in proc.stderr


@pytest.mark.asyncio
async def test_get_latest_credentials_requires_mm_email_and_has_no_fallback(monkeypatch):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        await session.execute(delete(Credentials))
        session.add(Credentials(email="other_user@example.com", encrypted_payload=b"secret"))
        await session.commit()

        try:
            monkeypatch.delenv("MM_EMAIL", raising=False)
            with pytest.raises(RuntimeError, match="MM_EMAIL"):
                await get_latest_credentials(session)

            monkeypatch.setenv("MM_EMAIL", "configured_user@example.com")
            creds = await get_latest_credentials(session)
            assert creds is None
        finally:
            await session.execute(delete(Credentials))
            await session.commit()


@pytest.mark.asyncio
async def test_push_transaction_requires_mm_account_with_no_fallback(monkeypatch):
    monkeypatch.delenv("MM_ACCOUNT", raising=False)
    mm = MagicMock()
    mm.get_accounts = AsyncMock(return_value={"accounts": [{"id": "acc1", "displayName": "Euro Transactions"}]})
    mm.get_transaction_categories = AsyncMock(return_value={"categories": []})

    with pytest.raises(ValueError, match="MM_ACCOUNT"):
        await push_transaction(
            mm,
            {"date": "2026-11-01", "amount": 10.0, "currency": "USD", "merchant": "Test", "is_cash": False},
        )
