"""Background processing of generation jobs.

Each certificate is generated and committed independently:
  * one failure is recorded on that certificate and the loop continues,
  * progress is visible to the status endpoint while the job runs,
  * a job interrupted by a restart can resume - only PENDING rows are processed.
"""
import logging
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .generator import CertificateData, render_certificate
from .models import Certificate, CertificateStatus, Job, JobStatus, new_verification_code, utcnow
from .services import get_status_counts

logger = logging.getLogger(__name__)

Renderer = Callable[[Path, CertificateData], None]


class CertificateProcessor:
    def __init__(self, session_factory: sessionmaker, storage_dir: Path, renderer: Renderer = render_certificate):
        self.session_factory = session_factory
        self.storage_dir = Path(storage_dir)
        self.renderer = renderer  # injectable, so tests can simulate failures

    # ---- public API ----

    def process_job(self, job_id: str) -> None:
        with self.session_factory() as db:
            job = db.get(Job, job_id)
            if job is None:
                logger.warning("Job %s not found", job_id)
                return
            try:
                self._run(db, job)
            except Exception as exc:  # unexpected, e.g. DB/storage outage
                logger.exception("Job %s crashed", job_id)
                db.rollback()
                job = db.get(Job, job_id)
                job.status = JobStatus.FAILED
                job.error = f"{type(exc).__name__}: {exc}"[:1000]
                job.completed_at = utcnow()
                db.commit()

    def resume_unfinished_jobs(self) -> None:
        """Called at startup: finish jobs that were pending/processing when the server stopped."""
        with self.session_factory() as db:
            job_ids = db.scalars(
                select(Job.id).where(Job.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]))
            ).all()
        for job_id in job_ids:
            logger.info("Resuming job %s", job_id)
            self.process_job(job_id)

    # ---- internals ----

    def _run(self, db: Session, job: Job) -> None:
        job.status = JobStatus.PROCESSING
        job.started_at = job.started_at or utcnow()
        job.error = None
        db.commit()

        pending_ids = db.scalars(
            select(Certificate.id)
            .where(Certificate.job_id == job.id, Certificate.status == CertificateStatus.PENDING)
            .order_by(Certificate.row_index)
        ).all()

        for cert_id in pending_ids:
            cert = db.get(Certificate, cert_id)
            self._generate_one(db, job, cert)

        self._finalize(db, job)

    def _generate_one(self, db: Session, job: Job, cert: Certificate) -> None:
        cert.attempts += 1
        if not cert.verification_code:
            cert.verification_code = new_verification_code()
        relative_path = Path(job.id) / f"{cert.id}.pdf"

        try:
            self.renderer(
                self.storage_dir / relative_path,
                CertificateData(
                    recipient_name=cert.recipient_name,
                    course_name=job.course_name,
                    title=job.title,
                    issuer_name=job.issuer_name,
                    issue_date=job.issue_date,
                    verification_code=cert.verification_code,
                    grade=cert.grade,
                ),
            )
        except Exception as exc:
            logger.exception("Certificate %s (job %s) failed", cert.id, job.id)
            cert.status = CertificateStatus.FAILED
            cert.error = f"{type(exc).__name__}: {exc}"[:500]
        else:
            cert.status = CertificateStatus.GENERATED
            cert.file_path = relative_path.as_posix()
            cert.generated_at = utcnow()
            cert.error = None

        db.commit()  # per-certificate commit -> live progress + resumability

    def _finalize(self, db: Session, job: Job) -> None:
        counts = get_status_counts(db, job.id)
        generated = counts[CertificateStatus.GENERATED]
        problems = counts[CertificateStatus.FAILED] + counts[CertificateStatus.INVALID]

        if generated == 0:
            job.status = JobStatus.FAILED
        elif problems:
            job.status = JobStatus.COMPLETED_WITH_ERRORS
        else:
            job.status = JobStatus.COMPLETED
        job.completed_at = utcnow()
        db.commit()
