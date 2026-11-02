# MediScan OCR

Scan pharmacy documents (prescriptions & supplier invoices) with an Android
phone, review/correct the AI-extracted data, then **save** it or **send** it to
the shop's existing billing/inventory software.

Built as an independent product for pharmacies and pharmacy groups. OCR output
requires human review before it is sent to an external system. Accuracy and ERP
acceptance must be measured on each pilot's invoices.

## Repository layout

| Path | What it is |
|------|-----------|
| `backend/` | FastAPI API — auth, OCR pipeline, connectors, Postgres. |
| `mobile/` | Expo/React Native Android app — capture, offline queue, review, send. |
| `web/` | React admin console — review queue, connector config, delivery logs. |
| `desktop-agent/` | Windows companion that writes data into legacy software. |
| `docs/` | PRD, SDLC plan, integration guide, deployment, user guide, UAT. |

## How it works

```
Android app ──► FastAPI backend ──► Vision LLM (Gemini) extraction
   (offline        │   (auth, DB,
    queue)         │    lifecycle)
                   └──► Connectors ──► webhook (signed) / CSV-JSON export / desktop agent
                                        └──► the shop's billing / inventory software
```

Every scan goes **queued → processing → needs_review → approved → pushed**, with a
mandatory human review step. On any OCR failure the document is marked **failed** —
it never fabricates medical data.

## Quick links
- **Current product requirements and release gates:** [docs/INDEPENDENT_PRODUCT_PRD.md](docs/INDEPENDENT_PRODUCT_PRD.md)
- **Earlier product spec:** [docs/PRD.md](docs/PRD.md)
- **Build plan:** [docs/SDLC_PLAN.md](docs/SDLC_PLAN.md)
- **Integrate your software:** [ERP endpoint guide and example JSON](docs/ERP_WEBHOOK_PARTNER_GUIDE.md) · [full integration contract](docs/INTEGRATION.md)
- **Deploy to the cloud:** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- **Staff guide:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md)
- **Backend setup:** [backend/README.md](backend/README.md)

## Run locally

```bash
# Backend
cd backend && pip install -r requirements.txt
cp .env.example .env          # set JWT_SECRET, GEMINI_API_KEY
alembic upgrade head
uvicorn app.main:app --reload --port 8080

# Mobile (Expo Go for dev)
cd mobile && npm install
# set EXPO_PUBLIC_API_URL in .env
npx expo start

# Web admin
cd web && npm install && npm run dev
```

## Tests

```bash
cd backend && pytest -q
cd mobile  && npm test
cd web     && npm test
```

CI runs all three on every push (`.github/workflows/ci.yml`); a signed Android APK
can be built via `.github/workflows/build.yml` or EAS.
