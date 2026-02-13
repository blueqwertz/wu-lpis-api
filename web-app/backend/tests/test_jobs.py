from fastapi.testclient import TestClient

from app.main import app


def test_create_job_and_fetch_details():
    client = TestClient(app)

    payload = {
        "username": "h1234",
        "primary_course": "1000",
        "fallback_course": "2000",
        "offset_seconds": 0.1,
    }
    create_resp = client.post("/api/jobs", json=payload)
    assert create_resp.status_code == 200

    job_id = create_resp.json()["job_id"]

    details_resp = client.get(f"/api/jobs/{job_id}")
    assert details_resp.status_code == 200
    assert details_resp.json()["job_id"] == job_id

    logs_resp = client.get(f"/api/jobs/{job_id}/logs")
    assert logs_resp.status_code == 200
