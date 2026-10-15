"""FastAPI application factory."""

import logging

from fastapi import FastAPI, Response, status
from sqlalchemy import Engine

from fedtone import __version__
from fedtone.infrastructure.persistence.db import create_db_engine, database_is_ready
from fedtone.observability import configure_logging
from fedtone.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="FedTone API", version=__version__)
    app.state.engine = engine

    def get_engine() -> Engine:
        # Created on first use so importing the app has no filesystem side effects.
        if app.state.engine is None:
            app.state.engine = create_db_engine(settings.database.url)
        return app.state.engine

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict[str, str]:
        """Liveness: the process is up. Does not touch dependencies."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["system"])
    def ready(response: Response) -> dict[str, object]:
        """Readiness: dependencies needed to serve requests are reachable."""
        database_ok = database_is_ready(get_engine())
        if not database_ok:
            logger.warning("readiness check failed: database unavailable")
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ready" if database_ok else "unavailable",
            "checks": {"database": "ok" if database_ok else "unavailable"},
        }

    return app


def create_default_app() -> FastAPI:
    """Entry point for ``uvicorn fedtone.api.app:create_default_app --factory``."""
    settings = get_settings()
    configure_logging(settings)
    return create_app(settings)
