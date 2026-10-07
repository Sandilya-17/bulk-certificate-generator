import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "storage",
        max_recipients_per_job=50,
        resume_unfinished_jobs=False,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    # Starlette's TestClient runs BackgroundTasks before returning the response,
    # so by the time client.post() returns, the job has been processed.
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_auto_processing(app):
    """Disable the background scheduler so tests can inspect the PENDING state
    and drive the processor by hand."""
    app.state.schedule_job = lambda job_id, background_tasks: None
    return app.state.processor


def make_payload(recipients=None, **overrides):
    payload = {
        "course_name": "Advanced Python Programming",
        "issuer_name": "Acme Academy",
        "title": "Certificate of Completion",
        "issue_date": "2026-10-01",
        "recipients": recipients if recipients is not None else [
            {"name": "Asha Rao", "email": "asha@example.com", "grade": "A"},
            {"name": "Bob Smith", "email": "bob@example.com"},
            {"name": "Chitra Iyer", "email": "chitra@example.com"},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def payload():
    return make_payload()
