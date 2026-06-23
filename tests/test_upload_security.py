import pytest
from fastapi.testclient import TestClient
from bridge_app.main import app

client = TestClient(app)

def test_upload_valid_file(mocker):
    # Mocking db dependency and process_transaction logic
    # Since we just want to test file validation, we can mock the process_transaction to return a mock result
    mocker.patch("bridge_app.main.process_transaction", return_value={"mock": "data"})

    # Create a dummy image file
    files = {"file": ("test.jpg", b"fake image content", "image/jpeg")}

    response = client.post("/upload", files=files)

    assert response.status_code == 200
    assert response.json()["status"] == "success"

def test_upload_invalid_mime_type():
    # Attempt to upload a text file which is not in ALLOWED_MIME_TYPES
    files = {"file": ("test.txt", b"some text content", "text/plain")}

    response = client.post("/upload", files=files)

    assert response.status_code == 400
    assert "Unsupported file type: text/plain" in response.json()["detail"]

def test_upload_file_too_large(mocker):
    # Mock the read to return a large byte string (e.g., 11MB)
    # Actually, we can test it by just uploading a large mock file if the testing framework allows it,
    # or by mocking `file.size` if the UploadFile exposes it.

    # To prevent actually reading a 11MB file into memory, let's just make the test slightly large
    # and adjust the file sizes, or mock the size.

    # Let's mock the size checking behavior since actually sending 11MB in test might be slow,
    # but 11MB is not that big, we can just send it.

    large_content = b"0" * (11 * 1024 * 1024)
    files = {"file": ("large.jpg", large_content, "image/jpeg")}

    response = client.post("/upload", files=files)

    assert response.status_code == 400
    assert "File too large (max 10MB)" in response.json()["detail"]
