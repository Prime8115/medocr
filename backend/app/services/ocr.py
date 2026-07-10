import time
import random

def process_document_mock(document_id: str, file_path: str, doc_type: str = None) -> dict:
    """
    Mock function to simulate OCR/VLM processing of a document.
    """
    # Simulate processing time
    time.sleep(2)
    
    # Mock structured response matching MediScan Prescription Schema
    is_printed = random.choice([True, False])
    overall_confidence = round(random.uniform(0.85, 0.99), 2)
    
    return {
        "document_id": document_id,
        "doc_type": "prescription",
        "status": "processed",
        "patient": {
            "name": {"value": "Ramesh Kumar", "confidence": overall_confidence},
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
            },
            {
                "name": {"value": "Amoxicillin", "confidence": 0.92, "normalized_id": "IN-DRG-002"},
                "strength": {"value": "500 mg"},
                "form": {"value": "capsule"},
                "frequency": {"value": "BD", "expanded": "twice a day"},
                "duration": {"value": "5 days"},
                "instructions": {"value": "after food"}
            }
        ],
        "meta": {
            "pages": 1,
            "language": "en",
            "overall_confidence": overall_confidence,
            "processed_at": time.time(),
            "pipeline_used": "cloud_ocr" if is_printed else "vlm_htr"
        }
    }
