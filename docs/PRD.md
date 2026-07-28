# MediScan OCR — Product Requirements Document (PRD)

**Version:** 2.0 (Production Rebuild)
**Date:** 2026-07-27
**Owner:** Product / Engineering
**Status:** DRAFT — awaiting approval

---

## 1. Executive Summary

MediScan OCR is a mobile-first system that lets **pharmacy shopkeepers** scan medical documents
(prescriptions and supplier/purchase invoices) with their Android phone — via **live camera** or
**file/image upload** — automatically extract the structured information using an AI OCR pipeline,
let the shopkeeper **review and correct** it, and then either:

- **(A) Save** the structured record in the pharmacy's own history, or
- **(B) Push** it to the shop's **external software** (billing / inventory / POS) through a
  configurable **connector**.

Version 1 (this document) rebuilds the existing prototype into a **production-ready, secure,
tested** system with a UI designed for non-technical shop staff.

### What changes from the current prototype
The current code is a working proof-of-concept but is **not production ready**: data lives in an
in-memory dictionary (lost on restart), there is no authentication, the "push to external software"
feature is an empty stub, a live API key is committed to source, and the mock fallback silently
fabricates prescription data. This PRD defines the work to close those gaps.

---

## 2. Goals & Non-Goals

### 2.1 Goals
1. Reliable Android app: scan via camera **or** upload (image/PDF), for **spotty-wifi** shops
   (offline capture with an auto-processing queue).
2. Accurate structured extraction for **two document types**: prescriptions and supplier invoices.
3. A mandatory **human review/correct** step before any data leaves the app (patient safety).
4. **Three interchangeable connectors** to external software:
   - Generic REST **webhook** (signed),
   - **File export** (CSV/JSON) to a watched folder / download,
   - **Local desktop connector agent** for legacy software with no API.
5. **Save-to-history** path with search, independent of any external push.
6. **Production-grade UI** for non-technical pharmacy staff (large tap targets, minimal jargon,
   English + Hindi-ready copy).
7. Secure by default: authentication, secret management, audited actions, no fabricated data.
8. Automated **QA**: unit, integration, and end-to-end tests; CI that builds a signed APK.

### 2.2 Non-Goals (v1)
- iOS release (architecture stays cross-platform, but only Android is shipped/tested in v1).
- Full pharmacy inventory management (we push to *their* software; we don't replace it).
- Clinical decision support / drug-interaction checking.
- Multi-language OCR beyond English + common Hindi/Devanagari handwriting (best-effort).
- Insurance / claims workflows.

---

## 3. Users & Personas

| Persona | Description | Primary needs |
|---------|-------------|---------------|
| **Shop staff (primary)** | Counter staff at a pharmacy, low technical skill, busy, phone in hand | Scan fast, fix mistakes easily, push to their billing software in 1 tap |
| **Shop owner / admin** | Owns 1–3 shops, configures the integration | Set up the connector once, see history, trust the data |
| **(Internal) Support** | Our team | Diagnose failed pushes, re-process documents |

---

## 4. Core User Journeys

### 4.1 Scan → Review → Save or Push (happy path)
1. Staff opens app → **Capture** tab.
2. Chooses **Camera** (with edge-detection framing guide) or **Upload** (gallery image / PDF).
3. Selects doc type or lets the app auto-detect (**Prescription** / **Invoice**).
4. App uploads to backend; shows progress. (If offline → queued, see 4.3.)
5. OCR returns structured fields with **per-field confidence**; low-confidence fields are
   highlighted.
6. Staff reviews on the **Review** screen, edits any wrong field inline.
7. Staff taps **Save** (stored in history) and/or **Push to <software>** (sent via connector).
8. Confirmation with a clear success/failure state; failures are retryable.

### 4.2 Configure connector (owner, one-time)
1. Owner opens the **Web Admin Console** (or in-app Settings).
2. Picks connector type (Webhook / File export / Desktop agent).
3. Enters URL + secret / folder path / pairs the desktop agent.
4. Uses **Test Connection** to verify a real round-trip before going live.

### 4.3 Offline capture & queue
1. No connectivity → capture still works; document is stored locally with status **Queued**.
2. A visible badge shows N queued items.
3. On reconnect, the app auto-uploads queued items in order and processes them; staff is notified.

---

## 5. Functional Requirements

### 5.1 Mobile app (Android, Expo/React Native)
- **FR-M1** Capture via camera with a framing guide; capture via gallery image; upload PDF.
- **FR-M2** Client-side image pre-processing (auto-crop, rotate, compress) before upload.
- **FR-M3** Document-type selector (Prescription / Invoice / Auto-detect).
- **FR-M4** Upload with progress, retry, and cancellation.
- **FR-M5** Offline queue with persistent local storage and auto-resume.
- **FR-M6** Review screen: render all extracted fields grouped by section, **inline editable**,
  with confidence highlighting and a raw-image side-by-side reference.
- **FR-M7** Actions: **Save** to history, **Push** to configured connector(s), or both.
- **FR-M8** History tab: list, search, filter by type/status/date, open past documents.
- **FR-M9** Auth: login, session persistence, logout.
- **FR-M10** Settings: backend URL (env-driven, not hardcoded), connector status, language.
- **FR-M11** Clear error, empty, and loading states everywhere (no silent failures).

### 5.2 Backend (FastAPI)
- **FR-B1** `POST /v1/documents` — accept image/PDF + doc_type; validate type & size; enqueue OCR.
- **FR-B2** Async OCR processing with real status transitions: `queued → processing → needs_review
  → approved → pushed / failed`.
- **FR-B3** `GET /v1/documents` (list, paginated, filterable) and `GET /v1/documents/{id}`.
- **FR-B4** `PATCH /v1/documents/{id}` — persist human corrections to fields.
- **FR-B5** `POST /v1/documents/{id}/approve` — mark approved and (optionally) trigger push.
- **FR-B6** `POST /v1/documents/{id}/push` — push approved data through selected connector(s).
- **FR-B7** **Persistent storage** (PostgreSQL) for documents, fields, users, connectors, audit log.
- **FR-B8** File/object storage for uploaded images (not in DB).
- **FR-B9** Connector subsystem (see 5.4) with per-shop configuration.
- **FR-B10** Authentication (JWT) + per-shop data isolation (multi-tenant).
- **FR-B11** **Remove the silent mock fallback**; on OCR failure, return an explicit error status —
  never fabricated medical data. (A mock provider may exist ONLY behind an explicit test flag.)
- **FR-B12** Structured logging + audit trail of who approved/edited/pushed what and when.
- **FR-B13** Rate limiting and request size limits.

### 5.3 OCR / extraction pipeline
- **FR-O1** Vision-LLM extraction (Gemini, current default) with a strict, versioned JSON schema
  per doc type.
- **FR-O2** Two schemas: **Prescription** (patient, prescriber, medications[]) and **Invoice**
  (supplier, invoice meta, line items[] with batch/expiry/qty/price/HSN/GST).
- **FR-O3** Per-field confidence scores; overall document confidence; language detection.
- **FR-O4** Deterministic post-processing: date normalization, quantity/strength parsing,
  medication-name normalization hook.
- **FR-O5** Provider abstraction so the OCR engine can be swapped/upgraded without touching APIs.

### 5.4 Connector subsystem (the "external software" integration)
Three connector types, all configured per shop, all reusing one normalized **push payload**:

- **FR-C1 Webhook connector:** POST signed JSON (HMAC-SHA256 in `X-MediScan-Signature`) to a
  shop-configured URL; configurable retries with exponential backoff; delivery log.
- **FR-C2 File-export connector:** Generate CSV and/or JSON in a shop-defined layout; deliver via
  (a) downloadable file in web admin, or (b) drop into a folder watched by the desktop agent.
- **FR-C3 Desktop connector agent:** A small companion app (Windows, since Indian pharmacy software
  is Windows-based) that authenticates to the backend, receives pushes for its shop, and writes
  CSV/JSON into the target software's import folder (or calls its local API). Pairing via a
  one-time code.
- **FR-C4** `POST /v1/connectors` CRUD; `POST /v1/connectors/{id}/test` for a real test round-trip.
- **FR-C5** Every push is recorded (payload, response, status, timestamp) and is **retryable**.
- **FR-C6** Stable, versioned **push payload schema** documented for third-party integrators.

### 5.5 Web Admin Console
- **FR-W1** Real (non-mocked) review queue backed by the API, with search/filter/pagination.
- **FR-W2** Document detail view with edit + approve + push.
- **FR-W3** Connector configuration + Test Connection + delivery/audit logs.
- **FR-W4** Auth + role-based access (owner vs staff).

---

## 6. Non-Functional Requirements
- **Security:** JWT auth; secrets in env/secret store (never in git); rotate the leaked key;
  HMAC-signed webhooks; TLS everywhere; per-shop isolation; input validation; PII handled per
  6.1.
- **Privacy/compliance:** Prescriptions contain patient PII. Encrypt at rest and in transit;
  configurable retention; audit access; region-appropriate handling (India DPDP Act awareness).
- **Reliability:** OCR job queue survives restarts; push retries; no data loss on crash.
- **Performance:** Upload→result under ~10s typical for a single-page image; UI stays responsive.
- **Usability:** Primary actions reachable in ≤2 taps; large targets; readable at arm's length.
- **Observability:** Structured logs, health checks, error tracking, delivery dashboards.
- **Testability:** ≥ unit + integration + e2e coverage on critical paths; CI-gated.

### 6.1 Data handling
- Uploaded images stored in object storage with signed, expiring access URLs.
- Patient PII fields encrypted at rest; access audited.
- Configurable retention (default: raw image purged after N days; structured record retained).

---

## 7. System Architecture (target)

```
                         ┌─────────────────────────┐
   Android app  ───────► │      FastAPI backend     │ ◄──── Web Admin (React)
 (Expo, offline queue)   │  ┌───────────────────┐   │
                         │  │  API + Auth (JWT) │   │
                         │  ├───────────────────┤   │
                         │  │  OCR job worker   │──►│──► Vision LLM (Gemini)
                         │  ├───────────────────┤   │
                         │  │ Connector engine  │   │
                         │  └───────────────────┘   │
                         │     │        │       │    │
                         │  Postgres  Object   Audit │
                         │            storage  log   │
                         └──────┼─────────┼──────────┘
                                │         │
              Webhook (HMAC) ◄──┘         └──► File export ──► Desktop Agent (Windows)
              to shop's server                                  └► shop's billing software
```

**Stack:** FastAPI + PostgreSQL + SQLAlchemy + Alembic; async worker (FastAPI background tasks
for v1, upgradeable to a real queue); Expo/React Native (Android); React + Vite web admin;
small .NET/Node desktop agent for Windows.

---

## 8. Data Model (high level)
- **users** (id, shop_id, email, role, hashed_password)
- **shops** (id, name, settings)
- **documents** (id, shop_id, type, status, image_ref, overall_confidence, created_by,
  timestamps)
- **document_fields** / structured JSON payload (versioned schema per type)
- **connectors** (id, shop_id, type, config_json, secret_ref, enabled)
- **push_deliveries** (id, document_id, connector_id, status, request, response, attempts, ts)
- **audit_log** (id, shop_id, actor, action, target, ts)

---

## 9. Milestones (delivery plan → detailed in SDLC doc)
- **M0 Security hardening & foundation** — rotate key, secrets, DB, auth, persistence.
- **M1 Backend core** — documents lifecycle, OCR pipeline (both schemas), storage.
- **M2 Connectors** — webhook + file export + agent + test round-trips.
- **M3 Mobile app rebuild** — capture, offline queue, review/edit, save/push, history, auth, UI.
- **M4 Web admin** — real queue, detail, connector config, logs.
- **M5 QA & E2E** — full test suites, CI signed APK, UAT script, docs.

---

## 10. Success Metrics
- ≥ 90% of fields require no correction on clear printed documents.
- 100% of pushes are logged and retryable; zero silent data loss.
- Scan→push in < 30s for a trained user on a clear document.
- Zero fabricated-data incidents (mock fallback removed).
- All critical-path tests green in CI before release.

---

## 11. Risks & Mitigations
| Risk | Mitigation |
|------|------------|
| Handwritten prescriptions extract poorly | Mandatory human review; confidence highlighting; side-by-side image |
| Leaked API key already abused | Rotate immediately; move to secret store; add usage alerts |
| Shop software has no API | File-export + desktop agent connectors |
| Patient PII exposure | Encryption, auth, audit, retention limits |
| Offline data loss | Persistent local queue + server idempotency keys |
| Scope creep (inventory features) | Non-goals section; we push to their software, not replace it |

---

## 12. Open Questions (to confirm during M0)
1. Exact target billing software(s) for the desktop agent's import format (Marg? Vyapar? Tally?).
2. Data-retention period for raw images and PII.
3. Single-shop vs multi-shop tenancy at launch (design supports multi; confirm rollout).
4. Hindi/Devanagari handwriting priority for v1.
