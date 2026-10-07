import enum
import secrets
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


def new_verification_code() -> str:
    # 12 hex chars, printed on the certificate, e.g. "CERT-3F9A1C0B7D2E"
    return "CERT-" + secrets.token_hex(6).upper()


class JobStatus(str, enum.Enum):
    PENDING = "pending"                              # accepted, waiting for the worker
    PROCESSING = "processing"                        # worker is generating certificates
    COMPLETED = "completed"                          # every recipient got a certificate
    COMPLETED_WITH_ERRORS = "completed_with_errors"  # some generated, some failed/invalid
    FAILED = "failed"                                # nothing could be generated


class CertificateStatus(str, enum.Enum):
    PENDING = "pending"      # valid, not generated yet
    GENERATED = "generated"  # PDF exists and can be downloaded
    FAILED = "failed"        # valid input, but generation raised an error (retryable)
    INVALID = "invalid"      # rejected at validation time (never generated)


def _enum(e):
    # Store enums as plain strings so the schema works on any database.
    return SAEnum(e, native_enum=False, length=32, values_callable=lambda x: [m.value for m in x])


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(100))
    course_name: Mapped[str] = mapped_column(String(200))
    issuer_name: Mapped[str] = mapped_column(String(200))
    issue_date: Mapped[date] = mapped_column(Date)

    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.PENDING, index=True)
    total_count: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # job-level crash, if any

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="Certificate.row_index",
    )


class Certificate(Base):
    """One row per recipient in the request - including invalid ones, so the
    client can see exactly which input rows were rejected and why."""

    __tablename__ = "certificates"
    __table_args__ = (UniqueConstraint("job_id", "row_index", name="uq_certificate_job_row"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    row_index: Mapped[int] = mapped_column(Integer)  # position in the submitted list

    recipient_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    grade: Mapped[str | None] = mapped_column(String(50), nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # original input for invalid rows

    status: Mapped[CertificateStatus] = mapped_column(
        _enum(CertificateStatus), default=CertificateStatus.PENDING, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # relative to storage_dir
    verification_code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="certificates")
