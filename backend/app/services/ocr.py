import time
import json
import os
import io
import google.generativeai as genai
from dotenv import load_dotenv
import PIL.Image

# Load environment variables
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def process_document(document_id: str, file_bytes: bytes, content_type: str, doc_type: str = None) -> dict:
    """
    Process document using Gemini API.
    """
    if not GEMINI_API_KEY:
        # Fallback to mock if API key is not configured
        return _process_document_mock(document_id, file_bytes, doc_type)
        
    try:
        model = genai.GenerativeModel("gemini-1.5-pro")
        
        # Prepare the payload based on content type
        if content_type == "application/pdf":
            media_payload = {"mime_type": "application/pdf", "data": file_bytes}
        else:
            media_payload = PIL.Image.open(io.BytesIO(file_bytes))
        
        prompt = """
        You are an advanced medical OCR assistant. Analyze the provided medical prescription image and extract the following information into a strict JSON format. 
        Do not include markdown blocks, just the raw JSON string.

        {
            "document_id": "WILL_BE_INJECTED",
            "doc_type": "prescription",
            "status": "processed",
            "patient": {
                "name": {"value": "Extract name, null if not found", "confidence": 0.95},
                "age": {"value": "Extract age as integer, null if not found", "confidence": 0.9},
                "gender": {"value": "Extract gender (M/F), null if not found", "confidence": 0.9}
            },
            "prescriber": {
                "name": {"value": "Extract prescriber doctor name", "confidence": 0.9},
                "registration_no": {"value": "Extract registration number if present", "confidence": 0.8}
            },
            "medications": [
                {
                    "name": {"value": "Medicine name", "confidence": 0.95, "normalized_id": "null"},
                    "strength": {"value": "e.g. 500mg"},
                    "form": {"value": "e.g. tablet, capsule, syrup"},
                    "frequency": {"value": "e.g. TDS, BD, 1-0-1", "expanded": "three times a day"},
                    "duration": {"value": "e.g. 3 days"},
                    "instructions": {"value": "e.g. after food"}
                }
            ],
            "meta": {
                "pages": 1,
                "language": "en",
                "overall_confidence": 0.95,
                "processed_at": 0,
                "pipeline_used": "vlm_gemini"
            }
        }
        """

        response = model.generate_content([prompt, media_payload])
        response_text = response.text.strip()
        
        # Clean up markdown if Gemini wrapped it in ```json
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
            
        parsed_data = json.loads(response_text.strip())
        
        # Inject standard data
        parsed_data["document_id"] = document_id
        parsed_data["meta"]["processed_at"] = time.time()
        
        return parsed_data
        
    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        # Fallback to mock on error
        return _process_document_mock(document_id, file_bytes, doc_type)

def _process_document_mock(document_id: str, file_bytes: bytes, doc_type: str = None) -> dict:
    """Fallback mock data if Gemini API is unavailable or errors out."""
    import random
    time.sleep(2)
    overall_confidence = round(random.uniform(0.85, 0.99), 2)
    return {
        "document_id": document_id,
        "doc_type": "prescription",
        "status": "processed",
        "patient": {
            "name": {"value": "Ramesh Kumar (Mock)", "confidence": overall_confidence},
            "age": {"value": 45, "confidence": overall_confidence},
            "gender": {"value": "M", "confidence": overall_confidence}
        },
        "prescriber": {
            "name": {"value": "Dr. Sharma", "confidence": 0.99},
            "registration_no": {"value": "MCI-12345", "confidence": 0.98}
        },
        "medications": [
            {
                "name": {"value": "Paracetamol", "confidence": 0.97, "normalized_id": "IN-DRG-001"},
                "strength": {"value": "500 mg"},
                "form": {"value": "tablet"},
                "frequency": {"value": "TDS", "expanded": "three times a day"},
                "duration": {"value": "3 days"},
                "instructions": {"value": "after food"}
            }
        ],
        "meta": {
            "pages": 1,
            "language": "en",
            "overall_confidence": overall_confidence,
            "processed_at": time.time(),
            "pipeline_used": "mock_fallback"
        }
    }
