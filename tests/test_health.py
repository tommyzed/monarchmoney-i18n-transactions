from fastapi.testclient import TestClient
from bridge_app.main import app

def test_healthcheck_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
