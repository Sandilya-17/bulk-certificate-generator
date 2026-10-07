"""Business logic shared by the API and the worker (kept out of the route handlers)."""
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Certificate, CertificateStatus
from .schemas import ProgressOut, RecipientIn


@dataclass
class RowResult:
    index: int
    raw: Any
    recipient: RecipientIn | None = None
    errors: list[str] | None = None

    @property
    def is_valid(self) -> bool:
        return self.recipient is not None


def _format_errors(exc: ValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "recipient"
        out.append(f"{field}: {err['msg']}")
    return out


def validate_recipients(raw_recipients: list[Any]) -> list[RowResult]:
    """Validate each recipient independently.

    Returns one RowResult per input row (same order). Invalid rows carry
    human-readable errors; duplicates (same email, case-insensitive) within the
    request are rejected after the first occurrence.
    """
    results: list[RowResult] = []
    first_seen: dict[str, int] = {}

    for index, item in enumerate(raw_recipients):
        if not isinstance(item, dict):
            results.append(RowResult(index, item, errors=["recipient: must be a JSON object"]))
            continue
        try:
            recipient = RecipientIn.model_validate(item)
        except ValidationError as exc:
            results.append(RowResult(index, item, errors=_format_errors(exc)))
            continue

        key = recipient.email.lower()
        if key in first_seen:
            results.append(RowResult(
                index, item,
                errors=[f"email: duplicate of recipient at index {first_seen[key]}"],
            ))
            continue

        first_seen[key] = index
        results.append(RowResult(index, item, recipient=recipient))

    return results


def get_status_counts(db: Session, job_id: str) -> dict[CertificateStatus, int]:
    """Counts are computed from the certificate rows (single GROUP BY) instead of
    being stored on the job, so they can never drift out of sync."""
    rows = db.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {s: 0 for s in CertificateStatus}
    for status, n in rows:
        counts[CertificateStatus(status)] = n
    return counts


def build_progress(counts: dict[CertificateStatus, int]) -> ProgressOut:
    generated = counts[CertificateStatus.GENERATED]
    failed = counts[CertificateStatus.FAILED]
    pending = counts[CertificateStatus.PENDING]
    invalid = counts[CertificateStatus.INVALID]
    valid = generated + failed + pending
    processed = generated + failed
    percent = 100.0 if valid == 0 else round(processed * 100 / valid, 2)
    return ProgressOut(
        total=valid + invalid,
        valid=valid,
        pending=pending,
        generated=generated,
        failed=failed,
        invalid=invalid,
        processed=processed,
        percent_complete=percent,
    )
