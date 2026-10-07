from datetime import date, datetime, timezone
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

from .models import CertificateStatus, JobStatus


# ---------- input ----------

class RecipientIn(BaseModel):
    """Validated one recipient at a time (see services.validate_recipients),
    so one bad row doesn't reject the whole request."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    grade: str | None = Field(default=None, max_length=50)

    @field_validator("name")
    @classmethod
    def name_must_be_printable(cls, v: str) -> str:
        if not any(ch.isalpha() for ch in v):
            raise ValueError("name must contain at least one letter")
        if any(not ch.isprintable() for ch in v):
            raise ValueError("name contains non-printable characters")
        return " ".join(v.split())  # collapse internal whitespace


class JobCreate(BaseModel):
    """Request body for POST /api/v1/jobs.

    Job-level fields are validated strictly (a bad course name rejects the request).
    `recipients` is accepted as raw objects and validated row by row.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    course_name: str = Field(min_length=1, max_length=200)
    issuer_name: str = Field(min_length=1, max_length=200)
    title: str = Field(default="Certificate of Completion", min_length=1, max_length=100)
    issue_date: date | None = None  # defaults to today
    recipients: list[Any] = Field(min_length=1)


# ---------- output ----------

# All timestamps are stored in UTC. SQLite returns them without tzinfo, so mark
# them as UTC explicitly to always serialise as "...+00:00".
UTCDatetime = Annotated[
    datetime, AfterValidator(lambda v: v if v.tzinfo else v.replace(tzinfo=timezone.utc))
]

class InvalidRecipientOut(BaseModel):
    index: int
    errors: list[str]
    data: Any = None


class JobCreatedOut(BaseModel):
    job_id: str
    status: JobStatus
    total_recipients: int
    accepted: int
    rejected: int
    invalid_recipients: list[InvalidRecipientOut]
    status_url: str
    certificates_url: str


class ProgressOut(BaseModel):
    total: int        # rows submitted
    valid: int        # rows that passed validation (= will be attempted)
    pending: int
    generated: int
    failed: int
    invalid: int
    processed: int    # generated + failed
    percent_complete: float  # processed / valid


class JobOut(BaseModel):
    id: str
    status: JobStatus
    title: str
    course_name: str
    issuer_name: str
    issue_date: date
    progress: ProgressOut
    error: str | None
    created_at: UTCDatetime
    started_at: UTCDatetime | None
    completed_at: UTCDatetime | None
    certificates_url: str
    download_all_url: str


class CertificateOut(BaseModel):
    id: str
    job_id: str
    row_index: int
    recipient_name: str | None
    recipient_email: str | None
    grade: str | None
    status: CertificateStatus
    error: str | None
    attempts: int
    verification_code: str | None
    generated_at: UTCDatetime | None
    download_url: str | None


class CertificatePage(BaseModel):
    items: list[CertificateOut]
    total: int
    limit: int
    offset: int
