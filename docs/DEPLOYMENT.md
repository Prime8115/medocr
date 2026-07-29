# MediScan — Deployment & Operator Runbook

How to put the backend on a public URL so the mobile APK works anywhere (not
just on the shop's wifi), plus day-2 operations.

## 1. Deploy the backend (Render, free tier)

The repo ships a `render.yaml` blueprint.

1. Push the repo to GitHub (done: `Prime8115/medocr`).
2. On https://render.com → **New → Blueprint** → pick this repo.
3. Render creates a **web service** (Docker) + a **Postgres** database and wires
   `DATABASE_URL` automatically. `JWT_SECRET` is auto-generated.
4. After the first deploy, set these in the service's **Environment** tab:
   - `GEMINI_API_KEY` — your **rotated** key (see Security below).
   - `ALLOW_MOCK_OCR` — keep `false` for real OCR.
   - `CORS_ORIGINS` — the URL where you host the web admin (see step 3).
5. The service boots by running `alembic upgrade head` then uvicorn. Health check:
   `GET /health` → `{"status":"ok"}`.

You now have a URL like `https://mediscan-backend.onrender.com`.

> Any Docker host works (Railway, Fly.io, a VPS). Only `render.yaml` is
> Render-specific; the `Dockerfile` is portable.

## 2. Point the mobile app at the deployed URL & rebuild the APK

1. In `mobile/eas.json`, set the `preview.env.EXPO_PUBLIC_API_URL` to your
   deployed URL (e.g. `https://mediscan-backend.onrender.com`).
2. Rebuild:
   ```
   cd mobile
   EAS_NO_VCS=1 npx eas-cli build -p android --profile preview
   ```
3. Install the new APK. It now works on **any** network — no PC required.

## 3. Deploy the web admin (optional, static hosting)

```
cd web
VITE_API_URL=https://mediscan-backend.onrender.com npm run build
```
Upload `web/dist/` to any static host (Netlify, Vercel, Render static site).
Put that host's URL into the backend's `CORS_ORIGINS`.

## Day-2 operations

- **Logs:** the backend logs each request and every push delivery. Failed pushes
  are retryable from the web admin (connector delivery log).
- **Backups:** enable managed Postgres backups on your host.
- **Migrations:** ship schema changes as Alembic revisions; the container runs
  `alembic upgrade head` on every deploy.
- **Storage:** default `local` disk works for a single instance. For multiple
  instances or durability, set `STORAGE_BACKEND=s3` + S3 vars.
- **Scaling OCR:** current processing uses FastAPI background tasks. If volume
  grows, move OCR to a worker queue (documented as a future step).

## Security checklist (do before real use)

- [ ] **Rotate the Gemini API key** that was committed early on; set the new one
      only as an env var (never in git).
- [ ] **Rotate the GitHub PAT** embedded in the local git remote; switch to SSH
      or `gh auth login`.
- [ ] Confirm `JWT_SECRET` is a strong, unique value in production.
- [ ] Set `CORS_ORIGINS` to your exact admin URL (not `*`).
- [ ] Serve only over HTTPS (Render/Railway/Fly do this automatically).
