# Bulk Certificate Generator

A FastAPI backend that accepts a list of recipients in **one request**, generates a PDF certificate
for each valid recipient from a single predefined template, tracks progress, and serves the results
individually or as a ZIP.

**Stack:** Python 3.11+ · FastAPI · SQLAlchemy 2 · SQLite (any SQLAlchemy-supported RDBMS via `DATABASE_URL`) · ReportLab (PDF) · pytest

---

## 1. Setup

```bash
cd bulk-certificate-generator
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Configuration (all optional, via environment variables):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./certificates.db` | Any SQLAlchemy URL, e.g. `postgresql+psycopg://user:pass@localhost/certs` (install the driver) |
| `STORAGE_DIR` | `./generated_certificates` | Where PDFs are written (`<STORAGE_DIR>/<job_id>/<certificate_id>.pdf`) |
| `MAX_RECIPIENTS_PER_JOB` | `5000` | Upper bound per request |
| `RESUME_UNFINISHED_JOBS` | `true` | On startup, finish jobs interrupted by a restart |

Tables are created automatically on startup.

## 2. Run the application

```bash
uvicorn app.main:create_app --factory --reload
```

- API: http://127.0.0.1:8000
- Interactive docs (Swagger): http://127.0.0.1:8000/docs

## 3. Run the tests

```bash
pytest -q
```

45 tests, each using its own temporary database and storage directory:

| File | Covers |
|---|---|
| `tests/test_jobs.py` | creating a job, status/progress (pending → processing → completed), live progress mid-job, resume after restart |
| `tests/test_validation.py` | request-level validation (422), per-recipient validation, duplicates, all-invalid requests |
| `tests/test_generation.py` | PDF template content (text extracted with pypdf), one PDF per valid recipient, unique verification codes |
| `tests/test_failures.py` | one certificate failing while others succeed, all failing, retry, crash handling |
| `tests/test_retrieval.py` | single download, ZIP download, listing/filtering/pagination, 404/409/410 cases, verification |

## 4. Submit a certificate generation request

`POST /api/v1/jobs` — see `sample_request.json`:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

```json
{
  "course_name": "Advanced Python Programming",
  "issuer_name": "Acme Academy",
  "title": "Certificate of Completion",
  "issue_date": "2026-10-01",
  "recipients": [
    {"name": "Asha Rao", "email": "asha@example.com", "grade": "A"},
    {"name": "Bob Smith", "email": "bob@example.com"},
    {"name": "Broken Row", "email": "not-an-email"}
  ]
}
```

| Field | Required | Rules |
|---|---|---|
| `course_name` | yes | 1–200 chars |
| `issuer_name` | yes | 1–200 chars |
| `title` | no | default `"Certificate of Completion"`, max 100 |
| `issue_date` | no | `YYYY-MM-DD`, default today |
| `recipients[].name` | yes | 1–100 chars, must contain a letter, whitespace normalised |
| `recipients[].email` | yes | valid email, unique within the request (case-insensitive) |
| `recipients[].grade` | no | max 50 chars, printed on the certificate if given |

Response — **202 Accepted** (generation continues in the background):

```json
{
  "job_id": "a6c87190-...",
  "status": "pending",
  "total_recipients": 3,
  "accepted": 2,
  "rejected": 1,
  "invalid_recipients": [
    {"index": 2, "errors": ["email: value is not a valid email address: ..."],
     "data": {"name": "Broken Row", "email": "not-an-email"}}
  ],
  "status_url": "http://127.0.0.1:8000/api/v1/jobs/a6c87190-...",
  "certificates_url": "http://127.0.0.1:8000/api/v1/jobs/a6c87190-.../certificates"
}
```

### Check progress

```bash
curl http://127.0.0.1:8000/api/v1/jobs/<job_id>
```

```json
{
  "id": "a6c87190-...",
  "status": "completed_with_errors",
  "progress": {"total": 3, "valid": 2, "pending": 0, "generated": 2, "failed": 0,
               "invalid": 1, "processed": 2, "percent_complete": 100.0},
  "started_at": "...", "completed_at": "...",
  "certificates_url": "...", "download_all_url": "..."
}
```

Job statuses: `pending` → `processing` → `completed` | `completed_with_errors` | `failed`.
Certificate statuses: `pending`, `generated`, `failed` (generation error, retryable), `invalid` (rejected input).

## 5. Retrieve generated certificates

```bash
# Per-recipient results, with errors and download links (paginated, filterable)
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?status=generated&limit=100&offset=0"
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?status=failed"

# One PDF
curl -OJ http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download

# Everything generated so far, as a ZIP (files named <row_index>_<name>.pdf)
curl -OJ http://127.0.0.1:8000/api/v1/jobs/<job_id>/download
```

### All endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/jobs` | Submit a bulk request (202) |
| GET | `/api/v1/jobs/{job_id}` | Status + progress |
| GET | `/api/v1/jobs/{job_id}/certificates` | Per-recipient results (`status`, `limit`, `offset`) |
| GET | `/api/v1/jobs/{job_id}/download` | ZIP of generated PDFs |
| POST | `/api/v1/jobs/{job_id}/retry` | Re-run only the `failed` certificates |
| GET | `/api/v1/certificates/{id}` | One certificate's metadata |
| GET | `/api/v1/certificates/{id}/download` | One PDF |
| GET | `/api/v1/verify/{code}` | Look up a certificate by the ID printed on it |
| GET | `/health` | Liveness check |

Error codes: `404` unknown job/certificate · `409` certificate not generated yet / job still running / nothing to retry · `410` DB says generated but file is missing · `422` invalid request.

---

## 6. Design decisions

### Project layout

```
app/
  main.py       app factory, wiring, startup resume, the schedule_job seam
  config.py     settings from env vars
  database.py   engine creation (SQLite pragmas)
  models.py     Job + Certificate tables, status enums
  schemas.py    Pydantic request/response models
  services.py   per-row validation, progress calculation
  worker.py     CertificateProcessor - runs a job
  generator.py  the certificate template (ReportLab -> PDF)
  api.py        HTTP routes
tests/
```

Routes are thin; logic lives in `services.py` / `worker.py`; the PDF template is isolated in
`generator.py` behind one function `render_certificate(path, data)`.

### Background processing (not synchronous)

A request can contain thousands of recipients. Generating them inside the request would hold the
HTTP connection open for minutes and risk client/proxy timeouts. So `POST /jobs` only validates and
stores rows, returns **202** with a `job_id`, and generation runs afterwards; the client polls
`GET /jobs/{id}`.

The runner is FastAPI's `BackgroundTasks` (runs in-process after the response is sent). It needs no
extra infrastructure (no Redis/broker), which keeps the project easy to run and review. Its limits:
jobs share the web process, and a crash loses the in-memory queue. Two mitigations are built in:

- **Resumable jobs.** Every certificate is committed individually and only `pending` rows are
  processed, so on startup `resume_unfinished_jobs()` simply re-runs any job still
  `pending`/`processing` and continues where it stopped, without regenerating finished PDFs.
- **One seam to swap.** All scheduling goes through `app.state.schedule_job(job_id, ...)` in
  `main.py`. Moving to Celery/RQ/Dramatiq is a one-line change there (`generate_job.delay(job_id)`),
  with `CertificateProcessor.process_job` as the task body — nothing else changes.

### Validation: strict for the job, per-row for recipients

- Job-level fields (`course_name`, `issuer_name`, dates, unknown fields, empty list, over the max
  size) are wrong for *every* certificate, so they reject the whole request with **422**.
- Recipient rows are validated **individually** (`services.validate_recipients`). One bad email in a
  list of 2,000 shouldn't force the client to resubmit everything. Invalid rows are returned
  immediately in `invalid_recipients` (with their index) **and** stored as `invalid` certificates so
  they also appear in the job's results.
- If **no** row is valid, the request is rejected with 422 and no job is created.
- Duplicate emails within one request are treated as invalid (first occurrence wins) to avoid
  issuing two certificates to one person.

*Changing to all-or-nothing* would just mean raising 422 in `create_job` when `invalid` is non-empty.

### Failure isolation

`CertificateProcessor._generate_one` wraps each render in `try/except`: on error the certificate is
marked `failed` with the exception message, committed, and the loop continues. The final job status
is derived from the counts:

| Outcome | Job status |
|---|---|
| all valid rows generated, no invalid rows | `completed` |
| some generated, some failed or invalid | `completed_with_errors` |
| none generated | `failed` |

An unexpected job-level error (e.g. DB outage) marks the job `failed` with `error` set.
`POST /jobs/{id}/retry` re-queues only `failed` rows (not `invalid` ones — those need new input);
`attempts` tracks how many times each was tried.

### Data model

- `jobs` — shared certificate info + status + timestamps.
- `certificates` — **one row per submitted recipient**, including invalid ones, with `row_index`
  (position in the input, so clients can map results back), `status`, `error`, `attempts`,
  `file_path`, and a unique `verification_code` printed on the PDF.
- Progress counts are computed with a single `GROUP BY status` query rather than stored counters, so
  they can't drift out of sync with the rows. Indexed on `job_id` and `status`.

### Storage

PDFs are written to the filesystem under `STORAGE_DIR/<job_id>/`; the DB stores the path *relative*
to `STORAGE_DIR` so storage can be moved. Files are written to a `.tmp` file and atomically renamed,
so a crash never leaves a half-written PDF. In production this would be object storage (S3/GCS) with
pre-signed download URLs — only `generator.py` (write) and the two download endpoints (read) touch
files.

The ZIP is built in a temp file (not in memory, since a job can have thousands of PDFs) and deleted
after it's sent.

### Certificate template

ReportLab, landscape A4: title, recipient name, course, optional grade, issue date, issuer, and the
verification code. Long names/course titles automatically shrink to fit one line.

### Database

SQLite by default for zero-setup; SQLAlchemy keeps it portable (enums stored as strings). WAL mode
is enabled so the status endpoint can read while the worker writes. For production use PostgreSQL via
`DATABASE_URL` and Alembic migrations instead of `create_all`.

### Known limitations / next steps

- No authentication/authorisation — anyone with a job ID can read it (IDs are random UUIDs).
- Built-in PDF fonts cover Latin script; for names in e.g. Telugu/Devanagari, register a TTF font
  (Noto) in `generator.py`.
- Generation within a job is sequential. For very large jobs, a real task queue with multiple workers
  (each processing chunks of certificate IDs) would parallelise it.
- No idempotency key on `POST /jobs` — a client retrying a timed-out request creates a second job.
- Per-certificate commits are simple and give live progress; for 100k-row jobs, committing in
  batches would reduce DB round-trips.
