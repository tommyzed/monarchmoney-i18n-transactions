import pytest
import os
from fastapi.testclient import TestClient
from unittest.mock import patch
from bridge_app.main import app

@pytest.fixture(autouse=True)
def bypass_cookie_security(monkeypatch):
    # Disable UNLOCK_SECRET or patch state to bypass security check
    from bridge_app import main
    monkeypatch.setattr(main, "UNLOCK_SECRET", None)

def test_handle_manual_entry():
    # Arrange
    client = TestClient(app)
    form_data = {
        "amount": "123.45",
        "currency": "USD",
        "date": "2026-07-07",
        "merchant": "Target",
        "is_cash": "true",
        "is_credit": "false",
        "notes": "Weekly groceries"
    }

    # Mock process_background_job which is called inside handle_manual_entry
    with patch("bridge_app.main.process_background_job") as mock_process_job:
        # Act
        response = client.post("/manual", data=form_data)

        # Assert
        assert response.status_code == 200

        # Check that placeholder replacements happened correctly
        assert "__JOB_ID__" not in response.text
        assert "__MM_ACCOUNT__" not in response.text

        expected_account = os.environ.get("MM_ACCOUNT", "Default Account")
        assert expected_account in response.text
        assert "Our AI Elves are hard at work!" in response.text

        # Verify the background task was queued with the correct arguments
        mock_process_job.assert_called_once()
        call_args = mock_process_job.call_args

        # Args of process_background_job: (job_id, content, user_currency, manual_data, force_override)
        job_id, content, user_currency, manual_data = call_args[0][:4]

        assert job_id is not None
        assert content is None
        assert user_currency is None

        # Check elements of manual_data
        assert manual_data["amount"] == 123.45
        assert manual_data["currency"] == "USD"
        assert manual_data["date"] == "2026-07-07"
        assert manual_data["merchant"] == "Target"
        assert manual_data["is_cash"] is True
        assert manual_data["is_credit"] is False
        assert manual_data["notes"] == "Weekly groceries"

def test_handle_manual_entry_exception():
    client = TestClient(app)
    form_data = {
        "amount": "123.45",
        "currency": "USD",
        "date": "2026-07-07",
        "merchant": "Target",
        "is_cash": "true",
        "is_credit": "false",
        "notes": "Weekly groceries"
    }

    # Simulate an exception when process_background_job is called or background_tasks is used
    with patch("bridge_app.main.uuid.uuid4", side_effect=Exception("UUID generation failed")):
        response = client.post("/manual", data=form_data)
        assert response.status_code == 500
        assert "Error starting job" in response.text
