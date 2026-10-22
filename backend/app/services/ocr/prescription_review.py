"""Transcription review requirements; no clinical dosing or prescribing rules."""
from app.services.ocr.coverage import summarize, _value, _present


def verify_prescription(fields, report):
    missing = []
    for section in ("patient", "prescriber"):
        if not _present(_value(fields.get(section), "name")):
            missing.append(f"{section}.name")
    medications = fields.get("medications") or []
    if not medications:
        missing.append("medications")
    for n, item in enumerate(medications):
        for key in ("name", "strength", "frequency", "duration"):
            if not _present(_value(item, key)):
                missing.append(f"medications[{n}].{key}")
    required = [{"id": "prescription_confirmation", "status": "fail",
                 "label": "Confirm prescription transcription",
                 "message": "A pharmacist must compare every medicine and instruction with the source. "
                            "Do not guess unclear text; confirm it with the prescriber before use.", "fields": []}]
    if missing:
        required.append({"id": "prescription_coverage", "status": "fail",
                         "label": "Prescription fields need review",
                         "message": "Fill missing text from the source or confirm it is absent. "
                                    "Acknowledging absence does not make the prescription clinically complete.",
                         "fields": missing})
    if report.get("pages_failed"):
        required.append({"id": "page_coverage", "status": "fail",
                         "label": "Some prescription pages could not be read",
                         "message": "Re-upload or manually transcribe and check every missing page.", "fields": []})
    return summarize({"checks": [], "review_requirements": required})
