import pytest
import hashlib
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from bridge_app.main import app, jobs

@pytest.fixture(autouse=True)
def setup_unlock_secret(monkeypatch):
    monkeypatch.setenv("UNLOCK_SECRET", "test_secret")
    from bridge_app import main
    monkeypatch.setattr(main, "UNLOCK_SECRET", "test_secret")
    # Also recalculate COOKIE_VALUE because it depends on UNLOCK_SECRET at import time
    monkeypatch.setattr(main, "COOKIE_VALUE", hashlib.sha256("test_secret".encode()).hexdigest())

@pytest.fixture
def auth_client():
    client = TestClient(app)
    # Get cookie value matching "test_secret"
    cookie_value = hashlib.sha256("test_secret".encode()).hexdigest()
    client.cookies.set("device_token", cookie_value)
    return client

def test_retry_job_invalid_id(auth_client):
    # Rationale: Can use FastAPI TestClient to hit the endpoint with an invalid job_id and check for 404.
    response = auth_client.post("/job/invalid-job-id/retry")
    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found"}

def test_retry_job_missing_inputs(auth_client):
    # Setup job with no inputs saved
    jobs["job-no-inputs"] = {
        "status": "failed",
        "progress": 0,
        "step": "Finished"
    }

    try:
        response = auth_client.post("/job/job-no-inputs/retry")
        assert response.status_code == 400
        assert response.json() == {"detail": "Cannot retry this job (inputs not saved)"}
    finally:
        # Clean up
        jobs.pop("job-no-inputs", None)

def test_retry_job_success(auth_client):
    # Setup job with valid inputs
    jobs["job-with-inputs"] = {
        "status": "completed",
        "progress": 100,
        "step": "Finished",
        "inputs": {
            "content": b"fake content",
            "user_currency": "USD",
            "manual_data": None
        }
    }

    try:
        with patch("bridge_app.main.process_background_job") as mock_background_job:
            response = auth_client.post("/job/job-with-inputs/retry", params={"force": True})
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

            # Verify job state was reset
            assert jobs["job-with-inputs"]["status"] == "processing"
            assert jobs["job-with-inputs"]["progress"] == 0
            assert jobs["job-with-inputs"]["step"] == "Retrying..."

            # Verify background task was added/called (TestClient executes background tasks synchronously if not mocked/patched out,
            # but since we patched process_background_job, we can check it)
            # Actually, FastAPI's BackgroundTasks are executed on TestClient exit, or if we mock the task execution.
            # But let's check that background tasks got executed/called.
            mock_background_job.assert_called_once_with(
                "job-with-inputs",
                b"fake content",
                "USD",
                None,
                force_override=True
            )
    finally:
        # Clean up
        jobs.pop("job-with-inputs", None)
