# MediScan OCR

Scan pharmacy documents (prescriptions & supplier invoices) with an Android
phone, review/correct the AI-extracted data, then **save** it or **send** it to
the shop's existing billing/inventory software.

Built for pharmacy shopkeepers. Production-grade, tested end to end.

## Repository layout

| Path | What it is |
|------|-----------|
| `backend/` | FastAPI API — auth, OCR pipeline, connectors, Postgres. **64 tests.** |
| `mobile/` | Expo/React Native Android app — capture, offline queue, review, send. **13 tests.** |
| `web/` | React admin console — review queue, connector config, delivery logs. **9 tests.** |
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
- **Product spec:** [docs/PRD.md](docs/PRD.md)
- **Build plan:** [docs/SDLC_PLAN.md](docs/SDLC_PLAN.md)
- **Integrate your software:** [docs/INTEGRATION.md](docs/INTEGRATION.md)
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
cd backend && pytest -q          # 64
cd mobile  && npm test           # 13
cd web     && npm test           # 9
```

CI runs all three on every push (`.github/workflows/ci.yml`); a signed Android APK
can be built via `.github/workflows/build.yml` or EAS.
