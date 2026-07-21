import ssl
from bridge_app.database import parse_database_url

def test_sqlite_url_is_untouched():
    url = "sqlite+aiosqlite:///./bridge.db"
    res_url, connect_args = parse_database_url(url)
    assert res_url == url
    assert connect_args == {}

def test_postgres_driver_correction():
    # Test correction of 'postgres://'
    url = "postgres://user:pass@localhost:5432/db"
    res_url, _ = parse_database_url(url)
    assert res_url == "postgresql+asyncpg://user:pass@localhost:5432/db"

    # Test correction of 'postgresql://'
    url2 = "postgresql://user:pass@localhost:5432/db"
    res_url2, _ = parse_database_url(url2)
    assert res_url2 == "postgresql+asyncpg://user:pass@localhost:5432/db"

    # Test 'postgresql+asyncpg://' remains unchanged
    url3 = "postgresql+asyncpg://user:pass@localhost:5432/db"
    res_url3, _ = parse_database_url(url3)
    assert res_url3 == "postgresql+asyncpg://user:pass@localhost:5432/db"

def test_sslmode_is_stripped_and_parsed():
    url = "postgres://user:pass@localhost:5432/db?sslmode=require&some_other_param=1"
    res_url, connect_args = parse_database_url(url)

    # Check that sslmode is stripped, but other parameters remain
    assert "sslmode" not in res_url
    assert "some_other_param=1" in res_url
    assert res_url.startswith("postgresql+asyncpg://")

    # Check connect_args structure and timeouts
    assert connect_args["timeout"] == 300
    assert connect_args["command_timeout"] == 300

    # Check SSL Context
    ssl_ctx = connect_args["ssl"]
    assert isinstance(ssl_ctx, ssl.SSLContext)
    assert ssl_ctx.check_hostname is False
    assert ssl_ctx.verify_mode == ssl.CERT_NONE

def test_sslmode_disable():
    url = "postgresql://user:pass@localhost:5432/db?sslmode=disable"
    res_url, connect_args = parse_database_url(url)

    assert "sslmode" not in res_url
    assert connect_args["ssl"] is False
    assert connect_args["timeout"] == 300
    assert connect_args["command_timeout"] == 300

def test_sslmode_verify_ca_and_full():
    # verify-ca uses the fallback default context (with default verification and hostname matching settings)
    url = "postgresql://user:pass@localhost:5432/db?sslmode=verify-ca"
    res_url, connect_args = parse_database_url(url)

    assert "sslmode" not in res_url
    ssl_ctx = connect_args["ssl"]
    assert isinstance(ssl_ctx, ssl.SSLContext)
    # By default, default context might check hostnames depending on Python/OpenSSL configuration,
    # but verify_mode is usually CERT_REQUIRED (which is different from CERT_NONE).
    assert ssl_ctx.verify_mode != ssl.CERT_NONE

def test_channel_binding_only():
    url = "postgresql://user:pass@localhost:5432/db?channel_binding=require"
    res_url, connect_args = parse_database_url(url)

    assert "channel_binding" not in res_url
    # Because sslmode defaults to "", it goes to the default ssl_ctx
    ssl_ctx = connect_args["ssl"]
    assert isinstance(ssl_ctx, ssl.SSLContext)
    assert connect_args["timeout"] == 300
    assert connect_args["command_timeout"] == 300
