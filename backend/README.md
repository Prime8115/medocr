# MediScan OCR — Backend

FastAPI + SQLAlchemy + Alembic. Provides authentication, document persistence,
OCR processing, and (from M2) the external-software connectors.

## Setup

```bash
cd backend
python -m venv venv
venv/Scripts/activate            # Windows PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env             # then edit .env — set JWT_SECRET and GEMINI_API_KEY
```

> **Security:** never commit `.env`. Generate a JWT secret with
> `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
> If a Gemini key was ever committed, **rotate it** in Google AI Studio.

## Database migrations

```bash
alembic upgrade head          # apply latest schema
alembic downgrade -1          # roll back one revision
alembic revision --autogenerate -m "message"   # create a new migration
```

Dev uses SQLite (`DATABASE_URL=sqlite:///./mediscan_dev.db`). Production uses
PostgreSQL (`DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/mediscan`).

## Run

```bash
uvicorn app.main:app --reload --port 8080
```

Interactive API docs at http://localhost:8080/docs.

## Test

```bash
pytest -q
```

## Auth quick reference

| Endpoint | Purpose |
|----------|---------|
| `POST /v1/auth/register` | Create a shop + owner (`email`, `password`, `shop_name`) |
| `POST /v1/auth/login` | OAuth2 password form → `{access_token}` |
| `GET  /v1/auth/me` | Current user (requires `Authorization: Bearer <token>`) |

All `/v1/documents` endpoints require a bearer token and are **scoped to the
caller's shop** (multi-tenant isolation).
