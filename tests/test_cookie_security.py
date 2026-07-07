import pytest
from fastapi.testclient import TestClient
from bridge_app.main import app
import os

@pytest.fixture(autouse=True)
def setup_unlock_secret(monkeypatch):
    monkeypatch.setenv("UNLOCK_SECRET", "test_secret")
    # Need to mock the UNLOCK_SECRET inside bridge_app.main where it was imported/read
    # Since main reads it at module level, setting it via monkeypatch might not work if it's already loaded
    # Let's import the main module and patch it directly
    from bridge_app import main
    monkeypatch.setattr(main, "UNLOCK_SECRET", "test_secret")

def test_cookie_secure_flag_http():
    # TestClient uses http://testserver by default
    client = TestClient(app)
    response = client.get("/s?s=test_secret")
    assert response.status_code == 200

    # Check the set-cookie header
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "Secure" not in set_cookie_header
    assert "Secure;" not in set_cookie_header

def test_cookie_secure_flag_https():
    # Force https protocol
    client = TestClient(app, base_url="https://testserver")
    response = client.get("/s?s=test_secret")
    assert response.status_code == 200

    # Check the set-cookie header
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "Secure" in set_cookie_header
