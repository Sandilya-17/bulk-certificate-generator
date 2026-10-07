"""Input validation: request-level errors reject the request (422); recipient-level
errors reject only that row, which is reported back with its index."""
import pytest

from app.services import validate_recipients
from tests.conftest import make_payload


@pytest.mark.parametrize("field", ["course_name", "issuer_name", "recipients"])
def test_missing_required_job_fields(client, field):
    payload = make_payload()
    del payload[field]
    assert client.post("/api/v1/jobs", json=payload).status_code == 422


def test_empty_recipient_list_rejected(client):
    assert client.post("/api/v1/jobs", json=make_payload([])).status_code == 422


def test_blank_course_name_rejected(client):
    assert client.post("/api/v1/jobs", json=make_payload(course_name="   ")).status_code == 422


def test_bad_issue_date_rejected(client):
    assert client.post("/api/v1/jobs", json=make_payload(issue_date="31-31-2026")).status_code == 422


def test_unknown_job_field_rejected(client):
    assert client.post("/api/v1/jobs", json=make_payload(colour="red")).status_code == 422


def test_too_many_recipients_rejected(client, settings):
    recipients = [{"name": f"Person {i}", "email": f"p{i}@example.com"} for i in range(settings.max_recipients_per_job + 1)]
    r = client.post("/api/v1/jobs", json=make_payload(recipients))
    assert r.status_code == 422
    assert "Too many recipients" in r.json()["detail"]


def test_invalid_rows_are_reported_and_valid_rows_accepted(client):
    payload = make_payload([
        {"name": "Asha Rao", "email": "asha@example.com"},
        {"name": "No Email"},
        {"name": "Bad Email", "email": "nope"},
        {"name": "   ", "email": "blank@example.com"},
        "just a string",
        {"name": "Asha Again", "email": "ASHA@example.com"},
        {"name": "Extra", "email": "extra@example.com", "age": 30},
        {"name": "Chitra Iyer", "email": "chitra@example.com"},
    ])
    r = client.post("/api/v1/jobs", json=payload)
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 6
    errors = {i["index"]: " ".join(i["errors"]) for i in body["invalid_recipients"]}
    assert set(errors) == {1, 2, 3, 4, 5, 6}
    assert "email" in errors[1] and "required" in errors[1].lower()
    assert "email" in errors[2]
    assert "name" in errors[3]
    assert "object" in errors[4]
    assert "duplicate of recipient at index 0" in errors[5]
    assert "age" in errors[6]

    # Invalid rows are stored too, with their errors, so they show up in results.
    certs = client.get(f"/api/v1/jobs/{body['job_id']}/certificates?status=invalid").json()
    assert certs["total"] == 6
    assert all(c["error"] for c in certs["items"])
    assert all(c["download_url"] is None for c in certs["items"])


def test_all_invalid_rows_rejects_request_without_creating_job(client, app):
    payload = make_payload([{"name": "X", "email": "bad"}, {"email": "a@example.com"}])
    r = client.post("/api/v1/jobs", json=payload)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert len(detail["invalid_recipients"]) == 2

    from app.models import Job
    with app.state.session_factory() as db:
        assert db.query(Job).count() == 0


def test_validator_normalises_whitespace():
    [row] = validate_recipients([{"name": "  Asha    Rao ", "email": " asha@example.com "}])
    assert row.is_valid
    assert row.recipient.name == "Asha Rao"
    assert row.recipient.email == "asha@example.com"


@pytest.mark.parametrize("name", ["", "12345", "A" * 101, "Bad\x00Name"])
def test_validator_rejects_bad_names(name):
    [row] = validate_recipients([{"name": name, "email": "a@example.com"}])
    assert not row.is_valid
    assert any(e.startswith("name") for e in row.errors)


def test_validator_accepts_unicode_names():
    [row] = validate_recipients([{"name": "José Müller", "email": "jose@example.com"}])
    assert row.is_valid
