# Deploying MediScan on your own server (Docker + nginx)

This is the exact setup running in production, minus secrets. Any Ubuntu/Debian
box with Docker + nginx works. The stack is self-contained and binds only to
localhost, so it coexists with other apps on the same server.

## 1. Get the code onto the server
```bash
mkdir -p /opt/mediscan && cd /opt/mediscan
# copy the repo's `backend/` folder here, plus the files from `deploy/`
```

## 2. Configure
```bash
cp deploy/docker-compose.example.yml docker-compose.yml
cp backend/.env.example .env
# Edit .env:
#   JWT_SECRET      -> python -c "import secrets;print(secrets.token_urlsafe(48))"
#   DATABASE_URL    -> postgresql+psycopg2://mediscan:<PGPASS>@db:5432/mediscan
#   GEMINI_API_KEY  -> your key from https://aistudio.google.com/apikey
#   ALLOW_MOCK_OCR  -> false
#   CORS_ORIGINS    -> https://<your-domain>
# Set POSTGRES_PASSWORD (same <PGPASS>) in the shell or a db env file.
```

## 3. Run
```bash
docker compose up -d --build      # migrations run automatically on boot
curl http://127.0.0.1:8090/health # -> {"status":"ok"}
```

## 4. Public HTTPS via nginx
```bash
cp deploy/nginx.example.conf /etc/nginx/sites-available/<your-domain>
# edit server_name to your domain
ln -s /etc/nginx/sites-available/<your-domain> /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d <your-domain>  # issues TLS + adds HTTP->HTTPS redirect
```

## 5. Web admin console
```bash
cd web
VITE_BASE=admin VITE_API_URL=https://<your-domain> npm run build   # or: npx vite build
# copy web/dist/* to /opt/mediscan/web-admin/  -> served at https://<your-domain>/admin/
```
> Note: pass `VITE_BASE` without slashes (the vite config normalizes it). On
> Git Bash, a slashed value gets path-mangled.

## 6. Mobile app
Set `mobile/eas.json` → `preview.env.EXPO_PUBLIC_API_URL` to `https://<your-domain>`,
then build:
```bash
cd mobile
EAS_NO_VCS=1 npx eas-cli build -p android --profile preview
```

## Updating later
```bash
# backend
cd /opt/mediscan && docker compose up -d --build backend
# web admin: rebuild and copy dist/* again
```

## Security checklist
- [ ] Strong unique `JWT_SECRET` and DB password (never committed)
- [ ] `ALLOW_MOCK_OCR=false` in production
- [ ] `CORS_ORIGINS` set to your admin URL only
- [ ] SSH key-only auth; firewall (ufw) limiting inbound to 22/80/443
- [ ] Managed Postgres backups (or `pg_dump` cron on the volume)
