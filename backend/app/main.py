from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import documents, webhooks

app = FastAPI(
    title="MediScan OCR Connect API",
    description="API for processing and pushing medical documents.",
    version="1.0.0",
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router, prefix="/v1/documents", tags=["Documents"])
app.include_router(webhooks.router, prefix="/v1/webhooks", tags=["Webhooks"])

@app.get("/health")
def health_check():
    return {"status": "ok"}
