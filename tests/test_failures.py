"""A failure for one certificate must not stop the others."""
from app.generator import render_certificate
from tests.conftest import make_payload


def fail_for(*names):
    def renderer(path, data):
        if data.recipient_name in names:
            raise RuntimeError(f"simulated render failure for {data.recipient_name}")
        render_certificate(path, data)
    return renderer


def test_single_failure_does_not_stop_other_certificates(client, app, payload):
    app.state.processor.renderer = fail_for("Bob Smith")
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "completed_with_errors"
    assert job["progress"]["generated"] == 2
    assert job["progress"]["failed"] == 1

    failed = client.get(f"/api/v1/jobs/{job_id}/certificates?status=failed").json()["items"]
    assert len(failed) == 1
    assert failed[0]["recipient_name"] == "Bob Smith"
    assert failed[0]["row_index"] == 1
    assert "simulated render failure" in failed[0]["error"]
    assert failed[0]["download_url"] is None

    ok = client.get(f"/api/v1/jobs/{job_id}/certificates?status=generated").json()["items"]
    assert [c["recipient_name"] for c in ok] == ["Asha Rao", "Chitra Iyer"]
    assert all(c["download_url"] for c in ok)


def test_job_failed_when_every_certificate_fails(client, app):
    app.state.processor.renderer = fail_for("Asha Rao", "Bob Smith", "Chitra Iyer")
    job_id = client.post("/api/v1/jobs", json=make_payload()).json()["job_id"]
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["progress"]["failed"] == 3


def test_retry_regenerates_only_failed_certificates(client, app, payload):
    app.state.processor.renderer = fail_for("Bob Smith")
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]

    # The transient problem goes away; retry.
    app.state.processor.renderer = render_certificate
    r = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert r.status_code == 200

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "completed"
    certs = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"]
    attempts = {c["recipient_name"]: c["attempts"] for c in certs}
    assert attempts == {"Asha Rao": 1, "Bob Smith": 2, "Chitra Iyer": 1}


def test_retry_with_nothing_failed_is_conflict(client, payload):
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    assert client.post(f"/api/v1/jobs/{job_id}/retry").status_code == 409


def test_retry_while_running_is_conflict(client, no_auto_processing, payload):
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]  # stays pending
    assert client.post(f"/api/v1/jobs/{job_id}/retry").status_code == 409


def test_unexpected_crash_marks_job_failed(client, app, no_auto_processing, payload, monkeypatch):
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    processor = no_auto_processing

    def boom(db, job):
        raise OSError("disk unavailable")

    monkeypatch.setattr(processor, "_finalize", boom)
    processor.process_job(job_id)

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert "disk unavailable" in job["error"]
