"""MediScan OCR Connect API — application entrypoint."""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import models  # noqa: F401  (populate SQLAlchemy metadata)
from app.api import agent, auth, connectors, documents, inventory
from app.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
)

app = FastAPI(
    title=settings.app_name,
    description="API for processing and pushing medical documents.",
    version="2.0.0",
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


@app.get("/health")
def health_check():
    return {"status": "ok"}
