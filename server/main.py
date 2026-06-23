"""FastAPI application entry point for the AI Code Review Bot.

Mounts all routers, serves the admin dashboard, and manages the
application lifecycle (startup DB init, shutdown cleanup).
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from server.config import settings

# ----- Logging -----
logging.basicConfig(
    level=logging.DEBUG if settings.is_development else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Quiet down noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("chromadb").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

# ── Dedicated log files for key subsystems ──
_workdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# LangFuse observability log (guard against duplicate handlers on hot-reload)
_lf_logger = logging.getLogger("server.observability")
if not any(isinstance(h, logging.FileHandler) for h in _lf_logger.handlers):
    _lf_handler = logging.FileHandler(os.path.join(_workdir, "langfuse.log"), encoding="utf-8")
    _lf_handler.setLevel(logging.DEBUG)
    _lf_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _lf_logger.addHandler(_lf_handler)
    _lf_logger.setLevel(logging.DEBUG)

# Publish / GitHub comment log (guard against duplicate handlers on hot-reload)
_pub_logger = logging.getLogger("server.services.review_service")
if not any(isinstance(h, logging.FileHandler) for h in _pub_logger.handlers):
    _pub_handler = logging.FileHandler(os.path.join(_workdir, "publish.log"), encoding="utf-8")
    _pub_handler.setLevel(logging.DEBUG)
    _pub_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _pub_logger.addHandler(_pub_handler)
    logging.getLogger("server.services.github_service").addHandler(_pub_handler)


# ----- Static files directory -----
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)


# ----- Application Lifespan -----
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown hooks."""
    # ===== Startup =====
    logger.info("=" * 60)
    logger.info("AI Code Review Bot starting up...")
    logger.info("Environment: %s", settings.app_env)
    logger.info("LLM Model: %s / Small: %s", settings.llm_model, settings.llm_small_model)
    logger.info("Database: %s@%s:%s/%s",
                settings.mysql_user, settings.mysql_host,
                settings.mysql_port, settings.mysql_database)
    logger.info("=" * 60)

    # Initialize database tables (dev mode only)
    try:
        from server.database import init_db
        await init_db()
    except Exception as e:
        logger.warning("Database init skipped (DB may not be running): %s", e)

    # Warm-up LLM factory
    try:
        from server.dependencies import get_llm_factory
        get_llm_factory()
        logger.info("LLM factory initialized")
    except Exception as e:
        logger.warning("LLM factory warm-up failed: %s", e)

    # Log harness status
    try:
        from server.harness.manager import harness
        logger.info("Harness system ready (plugins: %d)", len(harness.pm.get_plugins()))
    except Exception as e:
        logger.warning("Harness init: %s", e)

    yield  # Application runs here

    # ===== Shutdown =====
    logger.info("AI Code Review Bot shutting down...")

    # Dispose DB engine
    try:
        from server.database import engine
        await engine.dispose()
        logger.info("Database connections closed")
    except Exception:
        pass


# ----- Create FastAPI App -----
app = FastAPI(
    title="AI Code Review Bot",
    description="Automated multi-agent code review for GitHub PRs",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
)

# ----- CORS (allow admin UI to call API from anywhere in dev) -----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_development else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- Global Exception Handler -----
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler with structured error response."""
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": str(exc) if settings.is_development else "Internal server error",
        },
    )


# ----- Mount Routers -----

# API v1
from server.routers.webhook import router as webhook_router
from server.routers.review import router as review_router
from server.routers.admin import router as admin_router

app.include_router(webhook_router, prefix="/api/v1")
app.include_router(review_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")

# Also mount the admin router at the root-level for /api/v1/health, etc.
# (admin router handles /health, /repos, /admin/*, /dashboard)


# ----- Admin Dashboard (SPA) -----
@app.get("/")
async def serve_dashboard():
    """Serve the admin dashboard HTML."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(
        content={
            "message": "AI Code Review Bot API",
            "docs": "/docs",
            "health": "/api/v1/health",
            "admin_ui": "Create static/index.html for the admin dashboard",
        },
    )


# ----- Dev convenience: run directly -----
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.is_development,
        log_level="info",
    )