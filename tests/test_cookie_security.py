import pytest
from fastapi.testclient import TestClient
from bridge_app.main import app


@pytest.fixture(autouse=True)
def setup_unlock_secret(monkeypatch):
    monkeypatch.setenv("UNLOCK_SECRET", "test_secret")
    # Need to mock UNLOCK_SECRET inside bridge_app.main where it was read
    # Setting it via monkeypatch might not work if it's already loaded
    # Let's import the main module and patch it directly
    from bridge_app import main

    monkeypatch.setattr(main, "UNLOCK_SECRET", "test_secret")


def test_cookie_secure_flag_http():
    # TestClient uses http://testserver by default
    client = TestClient(app)
    response = client.post("/s", data={"s": "test_secret"})
    assert response.status_code == 200

    # Check the set-cookie header
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "Secure" not in set_cookie_header
    assert "Secure;" not in set_cookie_header


def test_cookie_secure_flag_https():
    # Force https protocol
    client = TestClient(app, base_url="https://testserver")
    response = client.post("/s", data={"s": "test_secret"})
    assert response.status_code == 200

    # Check the set-cookie header
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "Secure" in set_cookie_header


def test_security_blocked_when_secret_not_configured(monkeypatch):
    from bridge_app import main

    # Set UNLOCK_SECRET to None or empty to simulate security not configured
    monkeypatch.setattr(main, "UNLOCK_SECRET", None)

    client = TestClient(app)

    # 1. Protected endpoint /api/categories should return 401
    response = client.get("/api/categories")
    assert response.status_code == 401
    assert "Unauthorized" in response.text

    # 2. Public /fire endpoint should still be allowed
    response_fire = client.get("/fire")
    assert response_fire.status_code == 200

    # 3. Activation endpoint /s should return 500 when not configured
    response_s = client.post("/s", data={"s": "some_secret"})
    assert response_s.status_code == 500
    assert "Security not configured" in response_s.text
