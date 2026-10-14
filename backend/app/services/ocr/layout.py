"""Stable, value-free identity for an ordered set of invoice table headings."""
import hashlib
import json
import re


def signature(headings) -> str:
    normalized = [re.sub(r"[\s._-]+", "", str(h or "")).casefold() for h in headings or []]
    if not any(normalized):
        return ""
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=True).encode()).hexdigest()
