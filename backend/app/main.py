"""
RepoMind FastAPI application factory and lifespan manager.
Configures CORS, lifespan initialization, database migrations, and API routers.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.conversations import router as conversations_router
from app.api.routes import router as api_router
from app.config import (
    CORS_ORIGINS,
    DB_PATH,
    GITHUB_TOKEN,
    REPO_FULL_NAME,
    REPO_ROOT,
    get_masked_key,
)
from app.database import init_db
from app.tools.github_sync import sync_github_ci_failures, sync_github_issues

# Configure standard logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("repomind")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: database setup, repository verification, and warmup."""
    logger.info("Initializing RepoMind backend...")
    if REPO_FULL_NAME:
        logger.info("Configured repository: %s (path: %s)", REPO_FULL_NAME, REPO_ROOT)
    logger.info("Database: %s", DB_PATH)

    # 1. Initialize SQLite schema and FTS5 tables
    init_db()
    logger.info("Database schema initialized and ready.")

    # 2. Verify repository path if active repo is specified
    if REPO_FULL_NAME:
        logger.info("Active repository: %s (path: %s)", REPO_FULL_NAME, REPO_ROOT)
        if not REPO_ROOT.exists():
            logger.info("Creating directory for repository at '%s'...", REPO_ROOT)
            REPO_ROOT.mkdir(parents=True, exist_ok=True)

        # 3. Controlled GitHub metadata sync if token is available
        if GITHUB_TOKEN and os.getenv("REPOMIND_SYNC_ON_STARTUP", "false").lower() in ("true", "1"):
            try:
                logger.info("Syncing initial GitHub metadata for '%s'...", REPO_FULL_NAME)
                sync_github_issues(repo_full_name=REPO_FULL_NAME, max_issues=10)
                sync_github_ci_failures(repo_full_name=REPO_FULL_NAME, max_runs=3)
            except Exception as e:
                logger.warning("Optional GitHub sync on startup failed: %s", e)
    else:
        logger.info("No fixed repository configured on startup. System ready for dynamic repository linking.")

    logger.info("RepoMind is ready to process queries.")
    yield
    logger.info("RepoMind backend shutting down.")


def create_app() -> FastAPI:
    """FastAPI application factory."""
    app = FastAPI(
        title="RepoMind API",
        description="Multi-agent codebase intelligence and agentic RAG platform",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Global unhandled exception handler to prevent leaking internal traces
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled server exception: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal error occurred while processing the request."},
        )

    # Include routes at root and /api prefixes for flexibility
    app.include_router(api_router)
    app.include_router(api_router, prefix="/api")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
