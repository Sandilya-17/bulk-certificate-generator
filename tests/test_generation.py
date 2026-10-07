"""Certificate generation: the PDF template, and what the worker records."""
from datetime import date

from pypdf import PdfReader

from app.generator import CertificateData, render_certificate
from app.models import Certificate, Job


def _data(**overrides):
    base = dict(
        recipient_name="Asha Rao",
        course_name="Advanced Python Programming",
        title="Certificate of Completion",
        issuer_name="Acme Academy",
        issue_date=date(2026, 10, 1),
        verification_code="CERT-ABCDEF123456",
        grade="A",
    )
    base.update(overrides)
    return CertificateData(**base)


def _pdf_text(path):
    return PdfReader(str(path)).pages[0].extract_text()


def test_render_certificate_contains_recipient_info(tmp_path):
    out = tmp_path / "nested" / "cert.pdf"
    render_certificate(out, _data())

    assert out.read_bytes().startswith(b"%PDF")
    text = _pdf_text(out)
    for expected in ["Asha Rao", "Advanced Python Programming", "CERTIFICATE OF COMPLETION",
                     "Acme Academy", "01 October 2026", "CERT-ABCDEF123456", "grade: A"]:
        assert expected in text
    assert not list(tmp_path.rglob("*.tmp"))  # temp file was renamed away


def test_render_handles_very_long_values(tmp_path):
    out = tmp_path / "long.pdf"
    render_certificate(out, _data(recipient_name="Bartholomew " * 8, course_name="Course " * 25, grade=None))
    assert out.read_bytes().startswith(b"%PDF")
    assert "grade" not in _pdf_text(out)


def test_job_generates_one_pdf_per_valid_recipient(client, app, payload, settings):
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]

    with app.state.session_factory() as db:
        certs = db.get(Job, job_id).certificates
        assert all(c.status.value == "generated" for c in certs)
        assert all(c.attempts == 1 and c.generated_at for c in certs)
        codes = {c.verification_code for c in certs}
        assert len(codes) == 3  # unique per certificate
        for c in certs:
            path = settings.storage_dir / c.file_path
            assert path.is_file()
            assert c.recipient_name in _pdf_text(path)
            assert c.verification_code in _pdf_text(path)


def test_invalid_rows_are_never_rendered(client, app):
    from tests.conftest import make_payload
    payload = make_payload([{"name": "Ok Person", "email": "ok@example.com"}, {"name": "Bad", "email": "x"}])
    job_id = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    with app.state.session_factory() as db:
        bad = db.query(Certificate).filter_by(job_id=job_id, row_index=1).one()
        assert bad.status.value == "invalid"
        assert bad.file_path is None
        assert bad.attempts == 0
