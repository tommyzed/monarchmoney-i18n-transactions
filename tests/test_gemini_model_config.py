from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from bridge_app.main import app, lifespan
from bridge_app.services.gemini import extract_transaction_data, get_gemini_model


@pytest.mark.parametrize("env_value", [None, "", "   "])
def test_get_gemini_model_raises_when_unset_or_blank(monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
    else:
        monkeypatch.setenv("GEMINI_MODEL", env_value)

    with pytest.raises(RuntimeError, match="GEMINI_MODEL"):
        get_gemini_model()


def test_get_gemini_model_returns_stripped_value(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "  gemini-2.5-flash  ")
    assert get_gemini_model() == "gemini-2.5-flash"


@pytest.mark.asyncio
@pytest.mark.parametrize("env_value", [None, "", "   "])
async def test_lifespan_fails_when_gemini_model_missing(monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
    else:
        monkeypatch.setenv("GEMINI_MODEL", env_value)

    with pytest.raises(RuntimeError, match="GEMINI_MODEL"):
        async with lifespan(app):
            pass


def test_server_startup_fails_when_gemini_model_missing(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="GEMINI_MODEL"):
        with TestClient(app):
            pass


@pytest.mark.asyncio
async def test_lifespan_succeeds_when_gemini_model_set(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")

    async with lifespan(app):
        pass


def test_extract_transaction_data_fails_without_gemini_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    with patch("bridge_app.services.gemini._get_genai_client") as mock_get_client:
        result = extract_transaction_data(b"not-an-image")

    assert "error" in result
    assert "GEMINI_MODEL" in result["error"]
    mock_get_client.assert_not_called()
