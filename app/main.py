"""App factory. Run with:  uvicorn app.main:create_app --factory --reload"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI
from sqlalchemy.orm import sessionmaker

from .api import router
from .config import Settings
from .database import Base, make_engine
from .worker import CertificateProcessor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)  # simple schema bootstrap; use Alembic for real migrations
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    processor = CertificateProcessor(session_factory, settings.storage_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.resume_unfinished_jobs:
            threading.Thread(target=processor.resume_unfinished_jobs, daemon=True).start()
        yield
        engine.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        version="1.0.0",
        description="Submit a list of recipients, generate a PDF certificate for each, track progress, download results.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.processor = processor

    def schedule_job(job_id: str, background_tasks: BackgroundTasks) -> None:
        # Single seam for "how jobs get executed". Swap this for a Celery/RQ
        # `.delay(job_id)` call to move generation to separate worker processes.
        background_tasks.add_task(processor.process_job, job_id)

    app.state.schedule_job = schedule_job

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    app.include_router(router, tags=["certificates"])
    return app
