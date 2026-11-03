# Deploy MediScan on a SohamERP server

This guide deploys an independent MediScan instance: PostgreSQL, the FastAPI OCR backend, the web review app and optional Caddy HTTPS routing. It does **not** install MediScan inside a SohamERP product or create a SohamERP purchase voucher. The ERP receiver is a separate HTTPS integration described in the [partner endpoint guide](ERP_WEBHOOK_PARTNER_GUIDE.md).

**SohamERP deployment choice:** use the existing-reverse-proxy mode in §4. The standalone Caddy mode in §3 is for a separate host that gives MediScan ports 80 and 443.

## 1. Server and access

- Use a Linux server with Docker Engine and the Docker Compose v2 plugin. Start with at least 4 vCPU, 8 GB RAM and durable disk for a small pilot; measure actual OCR concurrency, page volume and storage before sizing production.
- Create two DNS records pointing to the server, for example `app.yourdomain.in` for the web app and `api.yourdomain.in` for the backend. The records must resolve publicly before automatic HTTPS can work.
- For the standalone stack, allow inbound TCP 80/443 and UDP 443. Ensure no other service owns those host ports. If SohamERP already uses a reverse proxy, use the existing-proxy variant below instead.
- Give the deployment operator access to the private source repository and Docker on the server. Keep the GitHub account, server credentials, Gemini key, JWT secret and database password in the owner's secret manager. Do not commit a filled `.env`.
- Confirm the required data-retention period, backup destination, data residency and who may access invoice/prescription originals before using real customer files.

## 2. Configure one environment file

On the server, clone the private repository into an application directory such as `/opt/mediscan`, then run:

```bash
cd /opt/mediscan
cp partner-deploy/.env.example partner-deploy/.env
chmod 600 partner-deploy/.env
python3 -c 'import secrets; print(secrets.token_hex(24))'       # POSTGRES_PASSWORD
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'   # JWT_SECRET
python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())'  # optional exact-original retention
```

Use a separate random value for each secret. Put the **same** generated database password in `POSTGRES_PASSWORD` and the password part of `DATABASE_URL`. The template uses a hex password so it can be pasted into the URL without percent-encoding. Set `DATABASE_URL=postgresql+psycopg2://mediscan:<password>@db:5432/mediscan`. Replace both domains, set `CORS_ORIGINS=https://<web-domain>`, and enter a live `GEMINI_API_KEY` or `GEMINI_API_KEYS`. Set `SOURCE_ENCRYPTION_KEYS` if exact uploaded originals must be retained; store a backup of that key separately. Without it, the processed source view remains available but exact-original retention is off. Never use `ALLOW_MOCK_OCR=true` with customer data.

The single `partner-deploy/.env` is read by Compose and the backend. The web API URL is baked into the web image at build time from `API_DOMAIN`. The Android APK must also be built with `EXPO_PUBLIC_API_URL=https://<api-domain>`; changing the server `.env` does not update an installed APK.

| Setting | Purpose | Pilot value or rule |
| --- | --- | --- |
| `API_DOMAIN`, `WEB_DOMAIN` | Public HTTPS names | Distinct real DNS names, no `https://` prefix |
| `POSTGRES_PASSWORD`, `DATABASE_URL` | Private PostgreSQL connection | Same random password; host `db`, database/user `mediscan` |
| `JWT_SECRET` | Signs user sessions | Separate random secret; changing it signs out users |
| `GEMINI_API_KEY` / `GEMINI_API_KEYS` | Live OCR provider credential | Server-side only; never place in web or APK |
| `CORS_ORIGINS` | Allowed browser origins | Include exact `https://WEB_DOMAIN`; no wildcard |
| `SOURCE_ENCRYPTION_KEYS` | Encrypt exact originals | Optional Fernet keys, newest first; retain old keys on rotation |
| `STORAGE_BACKEND`, `STORAGE_LOCAL_DIR` | Source storage | `local` and `/app/storage` for this single-server stack |
| `COMPOSE_PROJECT_NAME` | Isolates Docker resources | Unique per server if another MediScan stack exists |
| `MEDISCAN_API_PORT`, `MEDISCAN_WEB_PORT` | Existing-proxy loopback ports | Default 8090/8091; choose unused distinct ports if needed |

Run the preflight and inspect the Compose model without printing secret values:

```bash
python3 partner-deploy/check_env.py partner-deploy/.env
docker compose --env-file partner-deploy/.env -f partner-deploy/compose.yaml -f partner-deploy/compose.existing-proxy.yaml config --quiet
```

Do not paste the output of `docker compose config` without `--quiet` into a ticket: it can expose environment values.

## 3. Start with automatic HTTPS

When this server owns ports 80 and 443:

```bash
docker compose --env-file partner-deploy/.env -f partner-deploy/compose.yaml --profile standalone up -d --build
docker compose --env-file partner-deploy/.env -f partner-deploy/compose.yaml --profile standalone ps
curl --fail --silent https://<api-domain>/health
curl --fail --silent --output /dev/null --write-out '%{http_code}\n' https://<web-domain>/
```

Expected: API returns `{"status":"ok",...}` and web returns HTTP `200`. Caddy obtains and renews certificates for the two public DNS names; its certificate data lives in a persistent Docker volume. The database and application containers have no public host ports. If HTTPS fails, check DNS, firewall, Caddy logs and port ownership before changing application code.

## 4. Use an existing SohamERP reverse proxy

If the host already terminates TLS, omit the `standalone` profile and add the loopback-port override:

```bash
docker compose --env-file partner-deploy/.env -f partner-deploy/compose.yaml -f partner-deploy/compose.existing-proxy.yaml config --quiet
docker compose --env-file partner-deploy/.env -f partner-deploy/compose.yaml -f partner-deploy/compose.existing-proxy.yaml up -d --build
curl --fail http://127.0.0.1:8090/health
```

Configure the host proxy so `https://<api-domain>` forwards to `127.0.0.1:8090` and `https://<web-domain>` forwards to `127.0.0.1:8091`. Preserve the incoming `Host` header and forward HTTPS scheme information. Change `MEDISCAN_API_PORT` and `MEDISCAN_WEB_PORT` in `.env` if those local ports are occupied. Keep them bound to `127.0.0.1`, and verify the public HTTPS URLs after the proxy is configured.

## 5. First-use checks and SohamERP handoff

1. Create a test shop and owner through the app. Upload a **synthetic** invoice, confirm all pages and rows, save a correction, reopen it from a second browser/device, then approve it.
2. Configure a sandbox HTTPS webhook receiver using the [request and acknowledgement examples](ERP_WEBHOOK_PARTNER_GUIDE.md). Verify the HMAC signature over raw request bytes; bind the shop to the ERP tenant; deduplicate by shop/document; return `received` for queueing or `imported` only after a durable voucher exists.
3. Send the synthetic invoice twice. Confirm one ERP voucher and the same MediScan delivery receipt. Change the approved data after a send: MediScan must block a second send pending a reconciled amendment workflow.
4. Only then run a witnessed real invoice pilot. Check supplier, invoice number/date, every medicine/batch/expiry/quantity/tax line and ERP purchase total against the source and the receiving voucher. A successful `/health` response alone does not prove OCR or ERP import works.

SohamERP still needs to choose which product receives purchase entries and implement its receiver, supplier/product mapping, validation, voucher creation, deduplication and operator exception view. The source repository cannot supply those product-specific rules.

## 6. Operations

- **Updates:** back up database and source storage, note the current commit, `git pull` the reviewed release, then run the same `docker compose ... up -d --build` command. Backend startup applies Alembic migrations. A code rollback may require restoring the matching database backup; do not assume a schema downgrade is safe.
- **Backups:** schedule a PostgreSQL custom-format dump (`pg_dump -Fc`) and a snapshot of the Compose `source_storage` volume. Keep backups encrypted outside the server along with any `SOURCE_ENCRYPTION_KEYS`, and practice restoring both to a staging instance. A source backup without its encryption key cannot recover exact originals.
- **Monitoring:** check `docker compose ... ps`, backend and Caddy logs, `/health`, OCR job failures, low disk space, database backup age, and failed ERP deliveries. Restrict log access; never copy raw invoice data or secrets into support tickets.
- **Recovery:** if a build or migration fails, stop, keep the database and source volumes intact, inspect logs, and restore a tested matching snapshot before retrying. Never use `docker compose down -v` on a live instance.
- **Scale:** this stack runs one backend container with a local source volume. For more than one app host or a larger workload, move originals to durable object storage and validate worker/database concurrency before scaling out.

This package has been validated by environment unit tests, Compose configuration checks and repository CI. It still requires a server-specific DNS/TLS check, a backup/restore drill, a live OCR smoke test and a witnessed SohamERP import before production sign-off.
