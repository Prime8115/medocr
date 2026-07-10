from fastapi import APIRouter, File, UploadFile, Form, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uuid
import time
from typing import Optional
from app.services.ocr import process_document_mock

router = APIRouter()

# In-memory store for MVP
DOCUMENTS_DB = {}

class DocumentResponse(BaseModel):
    document_id: str
    status: str

@router.post("/", response_model=DocumentResponse)
async def submit_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    doc_type: Optional[str] = Form(None)
):
    """
    Submit a document for OCR processing.
    """
    if not file.content_type.startswith("image/") and file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Invalid file type. Only images and PDFs are supported.")
    
    document_id = f"doc_{uuid.uuid4().hex[:8]}"
    
    # Store initial state
    DOCUMENTS_DB[document_id] = {
        "document_id": document_id,
        "status": "processing"
    }
    
    # Background processing
    def process_and_update():
        result = process_document_mock(document_id, file.filename, doc_type)
        DOCUMENTS_DB[document_id] = result

    background_tasks.add_task(process_and_update)
    
    return DocumentResponse(document_id=document_id, status="processing")

@router.get("/{document_id}")
async def get_document(document_id: str):
    """
    Fetch document status and extracted payload.
    """
    doc = DOCUMENTS_DB.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc

@router.post("/{document_id}/approve")
async def approve_document(document_id: str):
    """
    Programmatic approval for a document.
    """
    doc = DOCUMENTS_DB.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    doc["status"] = "approved"
    # Trigger webhook here in a real app
    return {"status": "success", "message": f"Document {document_id} approved.", "document": doc}
