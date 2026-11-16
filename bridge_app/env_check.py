import os
import sys
from typing import List, Optional, Tuple
from dotenv import load_dotenv

REQUIRED_ENV_VARS: Tuple[str, ...] = (
    "DATABASE_URL",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "FERNET_KEY",
    "UNLOCK_SECRET",
    "MM_EMAIL",
    "MM_ACCOUNT",
)

DEV_SQLITE_URL = "sqlite+aiosqlite:///./bridge.db"

_DEV_VALUES = {"dev", "development", "local"}
_PROD_VALUES = {"prod", "production"}
_PROD_PLATFORM_VARS = (
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_PROJECT_ID",
    "RAILWAY_SERVICE_ID",
)


def is_test_environment() -> bool:
    """Return True when running under pytest or explicit test mode."""
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING") == "1":
        return True
    if "pytest" in sys.modules:
        return True
    for arg in sys.argv:
        lower_arg = arg.lower()
        if "pytest" in lower_arg or "test_" in lower_arg or lower_arg.startswith("tests/"):
            return True
    return False


def is_dev_environment() -> bool:
    """Return True only when explicitly configured for local development and not in production."""
    if any(os.getenv(k) for k in _PROD_PLATFORM_VARS):
        return False
    for var in ("APP_ENV", "ENVIRONMENT", "ENV"):
        val = (os.getenv(var) or "").strip().lower()
        if val in _PROD_VALUES:
            return False
        if val in _DEV_VALUES:
            return True
    return (os.getenv("DEV") or "").strip() == "1"


def get_required_env(name: str) -> str:
    """Return a stripped required environment variable or raise RuntimeError if unset/blank."""
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is not set")
    return value


def find_missing_required_env(*, allow_dev_sqlite: Optional[bool] = None) -> List[str]:
    """Return the list of required environment variables that are unset or blank."""
    if allow_dev_sqlite is None:
        allow_dev_sqlite = is_dev_environment()

    missing: List[str] = []
    for var in REQUIRED_ENV_VARS:
        if var == "DATABASE_URL" and allow_dev_sqlite:
            continue
        if not (os.getenv(var) or "").strip():
            missing.append(var)
    return missing


def validate_startup_env() -> None:
    """
    Validate all required environment variables before any database engine or app setup runs.
    Exits immediately with status 1 if any required variable is missing.
    """
    if is_test_environment():
        return

    load_dotenv(override=True)
    missing = find_missing_required_env()
    if missing:
        missing_list = ", ".join(missing)
        hint = ""
        if "DATABASE_URL" in missing:
            hint = " (or set APP_ENV=dev to use local SQLite for DATABASE_URL in development)"
        print(
            f"❌ STARTUP ERROR: Missing required environment variable(s): {missing_list}. "
            f"Server startup aborted{hint}.",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
