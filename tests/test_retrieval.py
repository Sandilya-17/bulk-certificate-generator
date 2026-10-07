"""Retrieving generated certificates: list, single download, ZIP, verification."""
import io
import zipfile

from app.generator import render_certificate
from tests.conftest import make_payload


def _create(client, payload=None):
    return client.post("/api/v1/jobs", json=payload or make_payload()).json()["job_id"]


def test_download_single_certificate(client):
    job_id = _create(client)
    cert = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]
    r = client.get(cert["download_url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert 'filename="Asha_Rao_certificate.pdf"' in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF")


def test_get_certificate_metadata(client):
    job_id = _create(client)
    cert = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][1]
    r = client.get(f"/api/v1/certificates/{cert['id']}")
    assert r.status_code == 200
    assert r.json()["recipient_email"] == "bob@example.com"


def test_download_unknown_certificate_404(client):
    assert client.get("/api/v1/certificates/nope/download").status_code == 404


def test_download_not_yet_generated_is_conflict(client, no_auto_processing):
    job_id = _create(client)
    cert = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]
    r = client.get(f"/api/v1/certificates/{cert['id']}/download")
    assert r.status_code == 409
    assert "pending" in r.json()["detail"]


def test_download_failed_certificate_is_conflict(client, app):
    def broken(path, data):
        raise ValueError("nope")
    app.state.processor.renderer = broken
    job_id = _create(client)
    cert = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]
    assert client.get(f"/api/v1/certificates/{cert['id']}/download").status_code == 409


def test_missing_file_on_disk_returns_410(client, settings):
    job_id = _create(client)
    cert = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]
    for f in (settings.storage_dir / job_id).glob("*.pdf"):
        f.unlink()
    assert client.get(cert["download_url"]).status_code == 410


def test_list_certificates_pagination_and_filter(client):
    recipients = [{"name": f"Person {chr(65 + i)}", "email": f"p{i}@example.com"} for i in range(7)]
    job_id = _create(client, make_payload(recipients))

    page = client.get(f"/api/v1/jobs/{job_id}/certificates?limit=3&offset=3").json()
    assert page["total"] == 7
    assert [c["row_index"] for c in page["items"]] == [3, 4, 5]

    assert client.get(f"/api/v1/jobs/{job_id}/certificates?status=failed").json()["total"] == 0
    assert client.get(f"/api/v1/jobs/{job_id}/certificates?status=bogus").status_code == 422
    assert client.get(f"/api/v1/jobs/{job_id}/certificates?limit=0").status_code == 422
    assert client.get("/api/v1/jobs/nope/certificates").status_code == 404


def test_download_all_as_zip(client, app):
    def fail_bob(path, data):
        if data.recipient_name == "Bob Smith":
            raise RuntimeError("x")
        render_certificate(path, data)
    app.state.processor.renderer = fail_bob

    job_id = _create(client)
    r = client.get(f"/api/v1/jobs/{job_id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = sorted(zf.namelist())
        assert names == ["00000_Asha_Rao.pdf", "00002_Chitra_Iyer.pdf"]  # only generated ones
        assert zf.read(names[0]).startswith(b"%PDF")


def test_zip_with_nothing_generated_is_404(client, no_auto_processing):
    job_id = _create(client)
    assert client.get(f"/api/v1/jobs/{job_id}/download").status_code == 404


def test_verify_by_code(client):
    job_id = _create(client)
    cert = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]
    r = client.get(f"/api/v1/verify/{cert['verification_code'].lower()}")
    assert r.status_code == 200
    assert r.json()["id"] == cert["id"]
    assert client.get("/api/v1/verify/CERT-000000000000").status_code == 404
