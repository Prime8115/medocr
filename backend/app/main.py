"""MediScan OCR Connect API — application entrypoint."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import RedirectResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import models  # noqa: F401  (populate SQLAlchemy metadata)
from app.api import agent, auth, connectors, documents, inventory, org, platform_orgs, problem_reports
from app.config import settings
from app.database import SessionLocal
from app.services.recovery import recover_stuck_documents
from app.services.worker import build_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Resume any scans a previous restart, crash or deploy interrupted.
    # Never let a recovery hiccup prevent the app from starting.
    try:
        db = SessionLocal()
        try:
            n = recover_stuck_documents(db)
            if n:
                print(f"[startup] resuming {n} interrupted document(s)")
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] recovery skipped: {exc}")

    worker = None
    if settings.ocr_worker_enabled:
        worker = build_worker()
        worker.start()
    yield
    if worker:
        worker.stop()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
)

app = FastAPI(
    title=settings.app_name,
    description="API for processing and pushing medical documents.",
    version="2.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — restricted to configured origins (never "*" in production).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_request_body(request: Request, call_next):
    """Reject oversized requests early based on Content-Length."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.max_upload_mb * 1024 * 1024:
                return JSONResponse(status_code=413, content={"detail": "Request body too large."})
        except ValueError:
            pass
    return await call_next(request)


app.include_router(auth.router, prefix="/v1/auth", tags=["Auth"])
app.include_router(documents.router, prefix="/v1/documents", tags=["Documents"])
app.include_router(connectors.router, prefix="/v1/connectors", tags=["Connectors"])
app.include_router(inventory.router, prefix="/v1/inventory", tags=["Inventory"])
app.include_router(agent.router, prefix="/v1/agent", tags=["Desktop Agent"])
app.include_router(problem_reports.router, prefix="/v1/admin", tags=["Platform reports"])
app.include_router(org.router, prefix="/v1/org", tags=["Organisation"])
app.include_router(platform_orgs.router, prefix="/v1/platform/orgs", tags=["Platform organisations"])

# The source production deploy packages the web console here. Partner Compose
# serves the same web app separately, so this mount stays optional in dev.
_admin_ui = Path(__file__).resolve().parent.parent / "admin_ui"
if (_admin_ui / "index.html").is_file():
    class _AdminSPA(StaticFiles):
        async def get_response(self, path, scope):
            try:
                response = await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code != 404 or "." in path.rsplit("/", 1)[-1]:
                    raise
                return await super().get_response("index.html", scope)
            if response.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                return await super().get_response("index.html", scope)
            return response

    @app.get("/admin", include_in_schema=False)
    def admin_redirect():
        return RedirectResponse("/admin/")

    app.mount("/admin", _AdminSPA(directory=str(_admin_ui), html=True), name="admin-ui")


@app.get("/health")
def health_check():
    # How many Gemini keys the server loaded (a count only, never the keys) -
    # so a key added to .env can be seen to have taken effect.
    return {"status": "ok", "ai_keys": len(settings.gemini_keys_list)}
