"""Application settings, read from environment variables.

Settings are passed explicitly into `create_app()` so tests can build an app
with a throwaway database and storage directory.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # Any SQLAlchemy URL works, e.g. postgresql+psycopg://user:pass@host/db
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./certificates.db")
    )
    # Where generated PDFs are written (one sub-folder per job).
    storage_dir: Path = field(
        default_factory=lambda: Path(os.getenv("STORAGE_DIR", "./generated_certificates"))
    )
    # Upper bound on recipients in a single request.
    max_recipients_per_job: int = field(
        default_factory=lambda: int(os.getenv("MAX_RECIPIENTS_PER_JOB", "5000"))
    )
    # On startup, pick up jobs that were interrupted (e.g. server restart).
    resume_unfinished_jobs: bool = field(
        default_factory=lambda: _env_bool("RESUME_UNFINISHED_JOBS", True)
    )
