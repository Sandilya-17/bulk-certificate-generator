import os
import re
import tempfile
import zipfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .models import Certificate, CertificateStatus, Job, JobStatus
from .schemas import (
    CertificateOut,
    CertificatePage,
    InvalidRecipientOut,
    JobCreate,
    JobCreatedOut,
    JobOut,
)
from .services import build_progress, get_status_counts, validate_recipients

router = APIRouter(prefix="/api/v1")


# ---------- dependencies & helpers ----------

def get_db(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, detail=f"Job {job_id} not found")
    return job


def _get_cert_or_404(db: Session, certificate_id: str) -> Certificate:
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(404, detail=f"Certificate {certificate_id} not found")
    return cert


def _cert_out(cert: Certificate, request: Request) -> CertificateOut:
    download_url = None
    if cert.status == CertificateStatus.GENERATED:
        download_url = str(request.url_for("download_certificate", certificate_id=cert.id))
    return CertificateOut(
        id=cert.id,
        job_id=cert.job_id,
        row_index=cert.row_index,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        grade=cert.grade,
        status=cert.status,
        error=cert.error,
        attempts=cert.attempts,
        verification_code=cert.verification_code,
        generated_at=cert.generated_at,
        download_url=download_url,
    )


def _job_out(db: Session, job: Job, request: Request) -> JobOut:
    return JobOut(
        id=job.id,
        status=job.status,
        title=job.title,
        course_name=job.course_name,
        issuer_name=job.issuer_name,
        issue_date=job.issue_date,
        progress=build_progress(get_status_counts(db, job.id)),
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        certificates_url=str(request.url_for("list_job_certificates", job_id=job.id)),
        download_all_url=str(request.url_for("download_job_certificates", job_id=job.id)),
    )


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60] or "certificate"


def _file_on_disk(request: Request, cert: Certificate) -> Path:
    path = request.app.state.settings.storage_dir / cert.file_path
    if not path.is_file():
        raise HTTPException(410, detail="Certificate file is missing from storage")
    return path


# ---------- jobs ----------

@router.post("/jobs", status_code=202, response_model=JobCreatedOut)
def create_job(
    payload: JobCreate,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Submit a bulk generation request. Returns immediately (202); generation runs in the background."""
    limit = request.app.state.settings.max_recipients_per_job
    if len(payload.recipients) > limit:
        raise HTTPException(422, detail=f"Too many recipients: {len(payload.recipients)} (max {limit} per job)")

    rows = validate_recipients(payload.recipients)
    invalid = [
        InvalidRecipientOut(index=r.index, errors=r.errors, data=r.raw) for r in rows if not r.is_valid
    ]
    if len(invalid) == len(rows):
        raise HTTPException(422, detail={
            "message": "No valid recipients in request",
            "invalid_recipients": [i.model_dump() for i in invalid],
        })

    job = Job(
        title=payload.title,
        course_name=payload.course_name,
        issuer_name=payload.issuer_name,
        issue_date=payload.issue_date or date.today(),
        total_count=len(rows),
        status=JobStatus.PENDING,
    )
    for r in rows:
        if r.is_valid:
            cert = Certificate(
                row_index=r.index,
                recipient_name=r.recipient.name,
                recipient_email=str(r.recipient.email),
                grade=r.recipient.grade,
                status=CertificateStatus.PENDING,
            )
        else:
            raw = r.raw if isinstance(r.raw, dict) else {"value": r.raw}
            cert = Certificate(
                row_index=r.index,
                recipient_name=str(raw.get("name"))[:100] if raw.get("name") is not None else None,
                recipient_email=str(raw.get("email"))[:320] if raw.get("email") is not None else None,
                raw_data=raw,
                status=CertificateStatus.INVALID,
                error="; ".join(r.errors),
            )
        job.certificates.append(cert)

    db.add(job)
    db.commit()

    request.app.state.schedule_job(job.id, background_tasks)

    status_url = str(request.url_for("get_job", job_id=job.id))
    response.headers["Location"] = status_url
    return JobCreatedOut(
        job_id=job.id,
        status=job.status,
        total_recipients=len(rows),
        accepted=len(rows) - len(invalid),
        rejected=len(invalid),
        invalid_recipients=invalid,
        status_url=status_url,
        certificates_url=str(request.url_for("list_job_certificates", job_id=job.id)),
    )


@router.get("/jobs/{job_id}", response_model=JobOut, name="get_job")
def get_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    """Job status and progress counters."""
    return _job_out(db, _get_job_or_404(db, job_id), request)


@router.get("/jobs/{job_id}/certificates", response_model=CertificatePage, name="list_job_certificates")
def list_job_certificates(
    job_id: str,
    request: Request,
    status: CertificateStatus | None = Query(default=None, description="Filter, e.g. ?status=failed"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Per-recipient results (with errors and download links), paginated."""
    _get_job_or_404(db, job_id)
    conditions = [Certificate.job_id == job_id]
    if status is not None:
        conditions.append(Certificate.status == status)

    total = db.scalar(select(func.count()).select_from(Certificate).where(*conditions))
    certs = db.scalars(
        select(Certificate).where(*conditions).order_by(Certificate.row_index).limit(limit).offset(offset)
    ).all()
    return CertificatePage(items=[_cert_out(c, request) for c in certs], total=total, limit=limit, offset=offset)


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_failed(job_id: str, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Re-queue certificates that FAILED during generation (invalid rows are not retried)."""
    job = _get_job_or_404(db, job_id)
    if job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
        raise HTTPException(409, detail="Job is still running")

    failed = db.scalars(
        select(Certificate).where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.FAILED)
    ).all()
    if not failed:
        raise HTTPException(409, detail="Job has no failed certificates to retry")

    for cert in failed:
        cert.status = CertificateStatus.PENDING
        cert.error = None
    job.status = JobStatus.PENDING
    job.completed_at = None
    db.commit()

    request.app.state.schedule_job(job.id, background_tasks)
    return _job_out(db, job, request)


@router.get("/jobs/{job_id}/download", name="download_job_certificates")
def download_job_certificates(job_id: str, request: Request, db: Session = Depends(get_db)):
    """ZIP of every certificate generated so far for this job."""
    _get_job_or_404(db, job_id)
    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.GENERATED)
        .order_by(Certificate.row_index)
    ).all()
    if not certs:
        raise HTTPException(404, detail="No generated certificates for this job yet")

    storage_dir = request.app.state.settings.storage_dir
    # Build on disk rather than in memory: a job may hold thousands of PDFs.
    fd, zip_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for cert in certs:
            src = storage_dir / cert.file_path
            if src.is_file():
                zf.write(src, arcname=f"{cert.row_index:05d}_{_slug(cert.recipient_name)}.pdf")

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"certificates_{job_id}.zip",
        background=BackgroundTask(os.remove, zip_path),  # delete after sending
    )


# ---------- single certificates ----------

@router.get("/certificates/{certificate_id}", response_model=CertificateOut)
def get_certificate(certificate_id: str, request: Request, db: Session = Depends(get_db)):
    return _cert_out(_get_cert_or_404(db, certificate_id), request)


@router.get("/certificates/{certificate_id}/download", name="download_certificate")
def download_certificate(certificate_id: str, request: Request, db: Session = Depends(get_db)):
    cert = _get_cert_or_404(db, certificate_id)
    if cert.status != CertificateStatus.GENERATED:
        raise HTTPException(409, detail=f"Certificate is not available (status: {cert.status.value})")
    return FileResponse(
        _file_on_disk(request, cert),
        media_type="application/pdf",
        filename=f"{_slug(cert.recipient_name)}_certificate.pdf",
    )


@router.get("/verify/{verification_code}", response_model=CertificateOut)
def verify_certificate(verification_code: str, request: Request, db: Session = Depends(get_db)):
    """Look up a certificate by the ID printed on it (lets a third party check authenticity)."""
    cert = db.scalar(select(Certificate).where(
        Certificate.verification_code == verification_code.upper(),
        Certificate.status == CertificateStatus.GENERATED,
    ))
    if cert is None:
        raise HTTPException(404, detail="No certificate with that verification code")
    return _cert_out(cert, request)
