# MediScan OCR — SDLC & Build Plan

**Companion to:** `PRD.md` v2.0
**Approach:** Incremental milestones, each ending in a **working, tested, demoable** state.
**Testing is built into every milestone**, not deferred to the end.

---

## Engineering standards (apply to all milestones)
- **Config via environment**, never hardcoded IPs/keys. `.env.example` committed; real `.env` gitignored.
- **Secrets** in env / secret store. The leaked Gemini key is **rotated in M0** before anything else.
- **Every PR** ships with tests and passes CI.
- **No fabricated data** ever returned to users; failures are explicit.
- **Definition of Done** per milestone: code + tests green + docs updated + manually smoke-tested.

---

## M0 — Security & Foundation  *(gate for everything else)*
**Goal:** Safe, persistent, authenticated foundation.
- Rotate leaked Gemini key; move all secrets to env; add `.env.example`; scrub key from repo/history.
- Add PostgreSQL + SQLAlchemy models + Alembic migrations (users, shops, documents, connectors,
  push_deliveries, audit_log).
- Replace in-memory `DOCUMENTS_DB` with the database.
- JWT auth (login, hashed passwords), per-shop tenancy scoping, auth middleware.
- Lock down CORS to known origins; add rate limiting + request-size limits.
- Object/file storage abstraction for uploaded images (local disk dev, S3-compatible prod).
- **Tests:** auth flow, tenancy isolation, migration up/down, storage adapter.

## M1 — Backend Core & OCR
**Goal:** Full document lifecycle with real extraction for both doc types.
- Document lifecycle endpoints (`POST/GET/PATCH/approve/push`) with status state machine.
- OCR provider abstraction; Gemini provider; **remove silent mock** (test-only mock behind flag).
- Two versioned schemas: **Prescription** and **Invoice**; auto-detect doc type.
- Post-processing (date/qty/strength normalization, med-name normalization hook).
- Persist per-field confidence; low-confidence flagging.
- **Tests:** lifecycle transitions, schema validation, prescription + invoice extraction against
  sample fixtures, PATCH corrections, error paths.

## M2 — Connector Subsystem  *(the external-software integration)*
**Goal:** Approved data reliably reaches the shop's software three ways.
- Normalized push payload (versioned) + connector config CRUD + `test` round-trip endpoint.
- **Webhook connector:** HMAC-SHA256 signing, retries w/ backoff, delivery log.
- **File-export connector:** CSV + JSON generators, shop-defined layout, downloadable + folder drop.
- **Desktop agent (Windows):** pairing via one-time code, authenticated polling/receive, writes
  import files to target folder. Ships as a small companion app + install guide.
- Idempotency keys so retried/queued pushes never double-post.
- **Tests:** signature verification, retry/backoff, CSV/JSON correctness, agent pairing + delivery,
  idempotency.

## M3 — Mobile App Rebuild (Android)
**Goal:** Production-grade app for shop staff.
- Env-driven backend URL; auth/login; session persistence.
- Capture: camera w/ framing guide, gallery, PDF; client-side crop/rotate/compress.
- **Offline queue:** persistent local store, auto-resume on reconnect, queued-count badge.
- Review screen: sectioned, **inline-editable** fields, confidence highlight, image reference.
- Actions: Save / Push / both, with clear success & retry states.
- History tab: list/search/filter/open.
- **Production UI system:** design tokens, large tap targets, consistent components, empty/error/
  loading states, English + Hindi-ready strings.
- **Tests:** component tests, offline-queue logic, upload/retry, e2e capture→review→push (Detox or
  Maestro).

## M4 — Web Admin Console
**Goal:** Owner/support console backed by real APIs.
- Replace mocked review queue with live paginated API data; search/filter.
- Document detail: edit/approve/push; connector configuration + Test Connection; delivery/audit logs.
- Auth + role-based access.
- **Tests:** component + integration against a test backend.

## M5 — QA, E2E & Release
**Goal:** Verified end-to-end, shippable.
- Full **E2E**: real image → OCR → review/correct → save + push → verify delivery to a mock external
  server, webhook, file, and agent.
- Load/soak on OCR queue; failure-injection on connectors.
- CI: lint + unit + integration + build **signed release APK**; artifact published.
- UAT script for a real pharmacy workflow; bug-fix pass.
- Docs: integrator guide (push payload), operator runbook, user quick-start.

---

## Testing strategy (summary)
| Layer | Tooling | Covers |
|-------|---------|--------|
| Unit | pytest / vitest / jest | pure logic, schemas, parsers, signing |
| Integration | pytest + test DB, supertest | API endpoints, DB, connectors |
| Contract | schema snapshot tests | push payload stability |
| E2E | Maestro/Detox (mobile), Playwright (web) | full user journeys |
| CI | GitHub Actions | all above + signed APK build |

---

## Proposed build order (with your approval)
M0 → M1 → M2 → M3 → M4 → M5, each demoable. I'll implement milestone-by-milestone, run the tests
for each, and report results before moving on. Larger milestones (M2, M3) I can optionally split
across multiple work sessions.
