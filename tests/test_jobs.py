"""Creating a job + job status/progress."""
from app.models import CertificateStatus, Job
from app.services import build_progress, get_status_counts
from tests.conftest import make_payload


def test_create_job_returns_202_with_links(client, payload):
    r = client.post("/api/v1/jobs", json=payload)
    assert r.status_code == 202
    body = r.json()
    assert body["total_recipients"] == 3
    assert body["accepted"] == 3
    assert body["rejected"] == 0
    assert body["invalid_recipients"] == []
    assert body["status_url"].endswith(f"/api/v1/jobs/{body['job_id']}")
    assert r.headers["location"] == body["status_url"]


def test_create_job_persists_job_and_rows(client, app, payload):
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    with app.state.session_factory() as db:
        job = db.get(Job, job_id)
        assert job.course_name == "Advanced Python Programming"
        assert job.total_count == 3
        assert [c.recipient_name for c in job.certificates] == ["Asha Rao", "Bob Smith", "Chitra Iyer"]


def test_issue_date_defaults_to_today(client):
    from datetime import date
    payload = make_payload()
    del payload["issue_date"]
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    assert client.get(f"/api/v1/jobs/{job_id}").json()["issue_date"] == date.today().isoformat()


def test_job_is_pending_before_processing(client, no_auto_processing, payload):
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["status"] == "pending"
    assert body["progress"]["pending"] == 3
    assert body["progress"]["percent_complete"] == 0
    assert body["started_at"] is None


def test_job_completes_with_full_progress(client, payload):
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["status"] == "completed"
    assert body["progress"] == {
        "total": 3, "valid": 3, "pending": 0, "generated": 3, "failed": 0,
        "invalid": 0, "processed": 3, "percent_complete": 100.0,
    }
    assert body["started_at"] and body["completed_at"]


def test_progress_is_visible_while_job_is_running(client, app, no_auto_processing, payload):
    """Each certificate is committed individually, so progress moves during the job."""
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    processor = no_auto_processing
    real_renderer = processor.renderer
    snapshots = []

    def spying_renderer(path, data):
        with app.state.session_factory() as db:
            snapshots.append((db.get(Job, job_id).status.value, build_progress(get_status_counts(db, job_id)).generated))
        real_renderer(path, data)

    processor.renderer = spying_renderer
    processor.process_job(job_id)

    assert snapshots == [("processing", 0), ("processing", 1), ("processing", 2)]
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "completed"


def test_job_with_invalid_rows_is_completed_with_errors(client):
    payload = make_payload([
        {"name": "Asha Rao", "email": "asha@example.com"},
        {"name": "Broken", "email": "not-an-email"},
    ])
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["status"] == "completed_with_errors"
    assert body["progress"]["generated"] == 1
    assert body["progress"]["invalid"] == 1
    assert body["progress"]["valid"] == 1
    assert body["progress"]["percent_complete"] == 100.0


def test_unknown_job_returns_404(client):
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404


def test_resume_only_processes_pending_rows(client, app, no_auto_processing, payload):
    """Simulates a restart mid-job: already generated rows are not regenerated."""
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    processor = no_auto_processing
    rendered = []
    real = processor.renderer

    def counting(path, data):
        rendered.append(data.recipient_name)
        real(path, data)

    processor.renderer = counting
    # Pretend the first row was finished before the "crash".
    with app.state.session_factory() as db:
        first = db.get(Job, job_id).certificates[0]
        first.status = CertificateStatus.GENERATED
        first.file_path = "x.pdf"
        db.commit()

    processor.resume_unfinished_jobs()
    assert rendered == ["Bob Smith", "Chitra Iyer"]
    assert client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "completed"
