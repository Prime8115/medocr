"""Extraction health — how well the OCR pipeline is actually doing.

Pure functions over Document rows, so they are testable without a database and
cheap to call from an endpoint.

This exists because of how the invoice defects were found: a user noticed them
on WhatsApp. Everything summarised here is something we should have seen first:

  * how often the free, exact Tier-1 PDF parser handles an invoice, versus how
    often we fall back to the paid AI path (a silent Tier-1 regression looks
    like nothing at all - the fallback still returns plausible data);
  * how often an invoice's lines fail to add up to its own printed total;
  * how often we are collapsing duplicate rows or printed copies, which says
    whether the 143-shown-as-429 class of bug is still out there;
  * what users are reporting as wrong.
"""
from collections import Counter
from typing import Iterable, List, Optional


def _meta(document) -> dict:
    payload = getattr(document, "payload", None) or {}
    meta = payload.get("meta") or {}
    return meta if isinstance(meta, dict) else {}


def _pct(part: int, whole: int) -> Optional[float]:
    return round(100.0 * part / whole, 1) if whole else None


def extraction_health(documents: Iterable, reported_ids: Optional[Iterable[str]] = None) -> dict:
    """Summarise a window of documents.

    `reported_ids` are documents a user flagged as wrong (from the audit log).
    """
    reported = set(reported_ids or [])

    total = 0
    statuses: Counter = Counter()
    pipelines: Counter = Counter()
    doc_types: Counter = Counter()

    invoices = 0
    reconciled = 0
    mismatched = 0
    no_total = 0
    with_duplicates = 0
    duplicate_rows = 0
    multi_copy = 0
    tier1 = 0
    pages_failed = 0

    reported_seen = 0

    for doc in documents:
        total += 1
        statuses[getattr(doc, "status", None) or "unknown"] += 1
        doc_type = getattr(doc, "doc_type", None) or "unknown"
        doc_types[doc_type] += 1
        if getattr(doc, "id", None) in reported:
            reported_seen += 1

        meta = _meta(doc)
        pipeline = meta.get("pipeline")
        if pipeline:
            pipelines[pipeline] += 1
        if meta.get("pages_failed"):
            pages_failed += 1

        if doc_type != "invoice" or not meta:
            continue
        invoices += 1
        if pipeline == "pdf_parser":
            tier1 += 1

        reconciles = meta.get("total_reconciles")
        if reconciles is True:
            reconciled += 1
        elif reconciles is False:
            mismatched += 1
        else:
            no_total += 1

        removed = meta.get("duplicates_removed") or 0
        if removed:
            with_duplicates += 1
            duplicate_rows += removed
        if (meta.get("copies_detected") or 1) > 1:
            multi_copy += 1

    return {
        "documents": total,
        "by_status": dict(statuses),
        "by_doc_type": dict(doc_types),
        "by_pipeline": dict(pipelines),
        "pages_failed_documents": pages_failed,
        "reported_by_users": reported_seen,
        "invoices": {
            "total": invoices,
            # The free, exact path. A drop here means we are paying for AI calls
            # we should not need - and losing exactness.
            "tier1_parser": tier1,
            "tier1_parser_pct": _pct(tier1, invoices),
            "reconciled": reconciled,
            "reconciled_pct": _pct(reconciled, invoices),
            "total_mismatch": mismatched,
            "total_unreadable": no_total,
            "with_duplicates_removed": with_duplicates,
            "duplicate_rows_removed": duplicate_rows,
            "multi_copy_pdfs": multi_copy,
        },
    }


def health_warnings(health: dict, min_invoices: int = 5) -> List[str]:
    """Plain-language flags an operator should act on.

    Kept deliberately quiet below `min_invoices`: two odd invoices in a slow week
    is noise, not a signal.
    """
    invoices = health.get("invoices") or {}
    count = invoices.get("total") or 0
    out: List[str] = []
    if count < min_invoices:
        return out

    mismatch_pct = _pct(invoices.get("total_mismatch") or 0, count) or 0
    if mismatch_pct >= 20:
        out.append(
            f"{mismatch_pct}% of invoices do not add up to their printed total. "
            "Check the line-item parsing before these reach users."
        )

    tier1_pct = invoices.get("tier1_parser_pct")
    if tier1_pct is not None and tier1_pct < 40:
        out.append(
            f"Only {tier1_pct}% of invoices used the exact PDF parser; the rest fell back to AI. "
            "Check the logs for tier1 parse failures."
        )

    unreadable = invoices.get("total_unreadable") or 0
    unreadable_pct = _pct(unreadable, count) or 0
    if unreadable_pct >= 30:
        out.append(f"{unreadable_pct}% of invoices had no readable total, so they cannot be reconciled.")

    if health.get("reported_by_users"):
        out.append(f"{health['reported_by_users']} document(s) were reported as wrong by users.")

    return out
