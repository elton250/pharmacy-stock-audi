"""Pharmacy Stock Auditing System — FastAPI application factory."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.errors import register_exception_handlers
from app.permissions import ensure_roles_and_permissions
from app.routers import (
    audit_logs,
    audits,
    auth,
    counting,
    counting_archive,
    inventory,
    medicines,
    meta,
    reports,
    users,
    vision,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotent role/permission seeding. Schema itself is managed by Alembic.
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        ensure_roles_and_permissions(db)
    except Exception as exc:  # pragma: no cover - startup path
        logger.error("Database unavailable during startup: %s", exc)
        raise
    finally:
        db.close()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Barcode-first pharmacy stock auditing with strict static counting.",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(meta.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(medicines.router)
    app.include_router(inventory.router)
    app.include_router(counting.router)
    app.include_router(counting_archive.router)
    app.include_router(audits.router)
    app.include_router(reports.router)
    app.include_router(audit_logs.router)
    app.include_router(vision.router)

    @app.get("/api/health")
    def health(response: Response):
        """Liveness + readiness. Returns 503 with a clear reason when the DB is unreachable."""
        from app.database import engine

        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning("Health check failed: %s", exc)
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {
                "status": "unavailable",
                "database": "down",
                "detail": f"Database connection failed: {exc.__class__.__name__}",
            }
        return {"status": "ok", "database": "up"}

    return app


app = create_app()
