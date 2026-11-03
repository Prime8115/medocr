# MediScan deployment and ERP handoff

**Version:** 3 November 2026. **Audience:** SohamERP infrastructure, ERP integration and MediScan product owners. This is the single handoff document for a first self-hosted pilot. Run the commands from the private `Prime8115/mediscan-sohamerp` repository. The shared application and deployment files are maintained in both codebases.

**Current readiness:** the package builds and passes repository CI, including a Linux container startup and health smoke test. No SohamERP server has been configured or validated yet. Complete the server and receiver acceptance steps below before putting customer invoices through it. The OCR accuracy and ERP import claims still require a witnessed pilot.

## Handoff inputs and ownership

| Input | Who supplies it | Why it is needed |
| --- | --- | --- |
| Linux host with Docker Engine, Compose v2, disk, backup target and operator access | SohamERP infrastructure | Run and recover the independent stack |
| Distinct public API and web DNS names | SohamERP infrastructure | HTTPS routing and the browser's API URL |
| Existing HTTPS proxy route for both names | SohamERP infrastructure | Forward traffic to the two loopback services; the proxy product can be chosen later |
| Production Gemini API key and secret storage | MediScan owner | Live OCR without exposing credentials in the app or repository |
| First SohamERP receiving product, technical owner and sandbox HTTPS endpoint | SohamERP ERP team | Build and test a purchase voucher receiver |
| Tenant/shop mapping, supplier/product/batch/tax rules | SohamERP ERP team | Prevent a delivered payload from becoming an incorrect purchase entry |
| Pilot invoices with approved ground truth and staff reviewers | Joint pilot team | Measure extraction, save durability and imported voucher accuracy |

The existing proxy only needs to forward `https://<api-domain>` to `127.0.0.1:8090` and `https://<web-domain>` to `127.0.0.1:8091` when it runs on the host. The Compose services and instructions below do not depend on Nginx, Caddy or Traefik. A containerized proxy needs a shared private Docker network or a private host-gateway route instead of its own `127.0.0.1`.

This guide deploys an independent MediScan instance: PostgreSQL, the FastAPI OCR backend, the web review app and optional Caddy HTTPS routing. It does **not** install MediScan inside a SohamERP product or create a SohamERP purchase voucher. The ERP receiver is a separate HTTPS integration described in the [partner endpoint guide](ERP_WEBHOOK_PARTNER_GUIDE.md).

**SohamERP deployment choice:** use the existing-reverse-proxy mode in §4. The standalone Caddy mode in §3 is for a separate host that gives MediScan ports 80 and 443.

## 1. Server and access

- Use a Linux server with Docker Engine and the Docker Compose v2 plugin. Start with at least 4 vCPU, 8 GB RAM and durable disk for a small pilot; measure actual OCR concurrency, page volume and storage before sizing production.
- Create two DNS records pointing to the server, for example `app.yourdomain.in` for the web app and `api.yourdomain.in` for the backend. The records must resolve publicly before automatic HTTPS can work.
- For the standalone stack, allow inbound TCP 80/443 and UDP 443. Ensure no other service owns those host ports. If SohamERP already uses a reverse proxy, use the existing-proxy variant below instead.
- Give the deployment operator access to the private source repository and Docker on the server. Keep the GitHub account, server credentials, Gemini key, JWT secret and database password in the owner's secret manager. Do not commit a filled `.env`.
- Confirm the required data-retention period, backup destination, data residency and who may access invoice/prescription originals before using real customer files.

## 2. Configure one environment file

On the server, use a GitHub account or read-only deploy key that has access to the private partner repository. Do not put a personal access token in the clone URL or shell history. Clone into an application directory such as `/opt/mediscan`, then run:

```bash
git clone https://github.com/Prime8115/mediscan-sohamerp.git /opt/mediscan
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

These loopback addresses assume the existing reverse proxy runs on the **host**. If SohamERP's proxy is itself a container, `127.0.0.1` inside that container is different. Connect the proxy to a shared Docker network and proxy to the `backend:8080` and `web:80` service names, or have the infrastructure team provide a private host-gateway route. Do not change the override to a public `0.0.0.0` bind to solve that routing issue.

## 5. First-use checks and SohamERP handoff

1. Create a test shop and owner through the app. Upload a **synthetic** invoice, confirm all pages and rows, save a correction, reopen it from a second browser/device, then approve it.
2. Configure a sandbox HTTPS webhook receiver using the [request and acknowledgement examples](ERP_WEBHOOK_PARTNER_GUIDE.md). Verify the HMAC signature over raw request bytes; bind the shop to the ERP tenant; deduplicate by shop/document; return `received` for queueing or `imported` only after a durable voucher exists.
3. Send the synthetic invoice twice. Confirm one ERP voucher and the same MediScan delivery receipt. Change the approved data after a send: MediScan must block a second send pending a reconciled amendment workflow.
4. Only then run a witnessed real invoice pilot. Check supplier, invoice number/date, every medicine/batch/expiry/quantity/tax line and ERP purchase total against the source and the receiving voucher. A successful `/health` response alone does not prove OCR or ERP import works.

SohamERP still needs to choose which product receives purchase entries and implement its receiver, supplier/product mapping, validation, voucher creation, deduplication and operator exception view. The source repository cannot supply those product-specific rules.

### Android pilot build

After the public API endpoint works, run the repository's **Build Android APK** GitHub Actions workflow with `api_url=https://<api-domain>`. Download its `mediscan-apk` artifact and install it on pilot devices. The API URL is compiled into the APK; changing the server environment does not change installed apps. Confirm the app reaches this instance and that a correction saved on one device appears on another. The current workflow produces a pilot APK; production app signing, release ownership and distribution still need a SohamERP/MediScan release decision.

## ERP connectivity for SohamERP and other customers

MediScan is independent of SohamERP. The same approved document can be sent to any supported connector, configured per shop by its owner. The [integration contract](INTEGRATION.md) and [synthetic webhook requests and acknowledgements](ERP_WEBHOOK_PARTNER_GUIDE.md) define the v1 payload. For customer data, configure a webhook signing secret and require the receiver to reject an absent or invalid signature.

| Path in the repositories | Current use | Acceptance boundary |
| --- | --- | --- |
| Signed HTTPS webhook | Any ERP that implements a receiver; recommended for SohamERP | `received` means transport/queue acceptance; `imported` plus a voucher reference means confirmed ERP posting. Receiver must verify the signature, bind the shop, deduplicate and validate rows. |
| CSV/JSON file export | ERP with a watched folder reachable by the MediScan server | File written only; ERP import must be reconciled separately. `generic`, `detailed` and custom column profiles are available. |
| Windows desktop agent | ERP on a shop PC with no reachable API or server folder | Confirms local file write only; staff or an adapter must confirm ERP import. |
| TallyPrime purchase XML | Manual pilot with purchase preflight, mapping, download and reported import outcome | Requires validation in the customer's Tally company. A reported voucher is an operator attestation, not a machine-confirmed Tally receipt. |
| Marg, Vyapar and basic Tally CSV profiles | Starting column layouts for file or webhook adapters | Templates are present, but they are not certified native integrations. Confirm the receiving product's current import schema, batch behavior and totals with a real sandbox before sale. |

For a non-SohamERP customer, first ask whether their ERP can receive a signed HTTPS POST, ingest files from a server/share, or only read files on a Windows PC. Choose the corresponding connector and test one synthetic invoice and one real reviewed invoice. Keep `quantity` separate from `pack` and `free_quantity`; stock and tax must reconcile in the receiving ERP. Prescriptions must not create purchase vouchers. A correction after a successful send is blocked until an operator reconciles the existing ERP voucher; an automatic amendment contract is still pending.

## Pilot sign-off and pending product specifications

The stack is ready for a **controlled staging install** when the inputs above are available. Sign off on a customer pilot only after all of these are witnessed and recorded:

1. Public HTTPS API and web health, login, upload and page-count checks pass through the existing proxy.
2. A corrected field and line item survive a server reload, a second-device reload and an exact-file reupload where replay is expected.
3. A labelled invoice's supplier, addresses, GST, all rows, batch, expiry, billed/free quantity and total match the source. Missing or ambiguous rows block silent approval.
4. A signed synthetic webhook request creates exactly one sandbox voucher despite retry and timeout drills; the voucher reference, every stock line and total reconcile in MediScan and the ERP.
5. PostgreSQL and source-storage backups are encrypted off-host; a restore to a separate staging stack is completed and recorded. Keep original-encryption keys with the recovery material.
6. Two tenant accounts cannot read each other's documents or connector configuration. Support access, logs and retention are approved by the owner.

| Remaining specification or feature | Why it matters | Current gate |
| --- | --- | --- |
| SohamERP receiver and product-specific mappings | A webhook contract alone does not create a purchase voucher | Select the receiving product and build a signed, idempotent sandbox receiver with an exception view. |
| Versioned amendments and reversals | A corrected imported invoice must not duplicate stock or reuse an old receipt | Keep the current stale-send block; design a versioned receiver protocol before automatic corrections. |
| Blind, labelled accuracy benchmark | No evidence yet for a market-wide accuracy claim, especially photos and handwriting | Report field, page and row metrics by document cohort against human ground truth. |
| Photo-quality and supervised prescription flow | Poor capture and handwriting can silently change medicines | Add retake cues and a pharmacist review gate before expanding prescription use. |
| Usage, cost and support controls | A saleable standalone product needs tenant economics and incident handling | Add metering, quotas, support diagnostics, backup and restore drills, and SLA ownership. |
| Production mobile release | A CI-built APK is an installable pilot artifact, not a managed store release | Decide signing keys, app identity, update channel and distribution ownership. |
| Third-party ERP certifications | Marg/Vyapar/Tally layouts are starting templates | Validate each target ERP's real import contract and voucher acknowledgement before advertising certified support. |
| Required webhook signing policy | The current connector permits an empty secret for compatibility | Require a configured secret for customer-data connectors after existing installations are migrated. |

The detailed scope and proposed release metrics are in the [independent product PRD](INDEPENDENT_PRODUCT_PRD.md). Both repositories should receive each shared runtime, deployment, contract and PRD change. The original repository intentionally retains private invoice tests and its own production workflow; the partner repository intentionally excludes those, live secrets and customer samples.

## 6. Operations

- **Updates:** back up database and source storage, note the current commit, `git pull` the reviewed release, then run the same `docker compose ... up -d --build` command. Backend startup applies Alembic migrations. A code rollback may require restoring the matching database backup; do not assume a schema downgrade is safe.
- **Backups:** schedule a PostgreSQL custom-format dump (`pg_dump -Fc`) and a snapshot of the Compose `source_storage` volume. Keep backups encrypted outside the server along with any `SOURCE_ENCRYPTION_KEYS`, and practice restoring both to a staging instance. A source backup without its encryption key cannot recover exact originals.
- **Monitoring:** check `docker compose ... ps`, backend and Caddy logs, `/health`, OCR job failures, low disk space, database backup age, and failed ERP deliveries. Restrict log access; never copy raw invoice data or secrets into support tickets.
- **Recovery:** if a build or migration fails, stop, keep the database and source volumes intact, inspect logs, and restore a tested matching snapshot before retrying. Never use `docker compose down -v` on a live instance.
- **Scale:** this stack runs one backend container with a local source volume. For more than one app host or a larger workload, move originals to durable object storage and validate worker/database concurrency before scaling out.

This package has been validated by environment unit tests, Compose configuration checks and repository CI. It still requires a server-specific DNS/TLS check, a backup/restore drill, a live OCR smoke test and a witnessed SohamERP import before production sign-off.
