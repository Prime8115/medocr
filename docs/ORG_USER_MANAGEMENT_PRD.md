# MediScan: organisations, workspaces and user management

Version 1.0 · 3 November 2026 · Baseline: `main` at `b68863e`

## 1. Problem and outcome

A customer company has several people who scan and review bills, often across
more than one branch or GSTIN. Today a MediScan account is one shop with one
owner. Adding a colleague means registering another shop, so the colleague
sees none of the first shop's bills, suppliers or lessons. Nothing limits how
many accounts a customer may use.

The outcome:

- A customer is an **organisation** holding a **licence**.
- The organisation has one or more **workspaces**: a shop, branch or GSTIN with
  its own bills, suppliers, settings and lessons.
- The organisation's admin adds **users** up to the licence's seat limit and
  gives each user access to one or more workspaces.
- Every existing OCR screen and API works inside the user's **current
  workspace**, unchanged.

## 2. Current state (audited at `b68863e`)

| Area | Today |
|---|---|
| Tenant | `shops` table. Every document, supplier lesson, connector and audit row carries `shop_id`. |
| User | `users.shop_id`, `role` = `owner` or `staff`, `is_active`, `is_platform_admin` (PR #42). |
| Tenancy | Every request resolves `user.shop_id` from the database (`get_current_user`), not from the token. |
| Adding users | Only `POST /v1/auth/register`, which creates a new shop with its owner. There is no way to add a user to an existing shop. |
| Licence | None. |
| Platform admin | `is_platform_admin` flag, `scripts/set_platform_admin.py`, and a Customer reports screen. |
| Org / workspace code | None. The `codex/org-users` branch in `mediscan-sohamerp` holds no work beyond `main`. |

## 3. Concepts

| Term | Meaning | Stored as |
|---|---|---|
| Organisation | The customer company. Holds the licence. | New `organizations` table. |
| Workspace | A shop, branch or GSTIN inside the organisation. Owns its bills and data. | Existing `shops` table plus `org_id`. Every existing shop becomes a workspace. |
| User | A person who signs in. Belongs to one organisation. | `users` plus `org_id`. |
| Current workspace | The workspace the user is working in now. | `users.shop_id`, so all existing tenancy works unchanged. |
| Workspace access | Which workspaces a member may open. | New `user_workspaces` table. |
| Org admin | Manages users and workspaces, and may open every workspace. | `role = "owner"`. |
| Member | Works only in the workspaces they are given. | `role = "staff"`. |
| Platform admin | MediScan staff. Creates organisations and sets their plan. | `is_platform_admin`. |

## 4. Licence plans (fixed)

The platform admin picks one plan per organisation. Plans are fixed; their
numbers live in one table in code (`app/services/licensing.py`) and are
changed there only.

| Plan | Users (active) | Workspaces |
|---|---|---|
| Starter | 2 | 1 |
| Growth | 5 | 3 |
| Business | 15 | 10 |
| Enterprise | 50 | 50 |

A licence also has an optional **valid-until** date, and the organisation an
**active** switch.

- **Seats count active users only.** Deactivating a user frees a seat;
  reactivating one needs a free seat.
- **Adding** a user or a workspace is refused at the limit, after the
  valid-until date, or while the organisation is inactive. The refusal says
  why: "All 5 seats on the Growth plan are in use".
- **Over the limit** (a downgrade, or an existing customer moved onto plans):
  nothing is removed. The organisation shows as over its limit, and adding is
  refused until it is back within the limit.
- **Expired licence (past valid-until): scanning stops.** Uploading a bill,
  Scan again and Recheck are refused with the reason. Bills already scanned
  stay open to review, correct, approve and send, so no data is cut off.
  Renewing (a new valid-until, or none) restores scanning at once.
- **Inactive organisation:** its users cannot sign in or use the API.
  Platform admins are never locked out.

## 5. Requirements

### 5.1 Organisation admin (web admin, "Organization" page)

1. See the organisation: name, plan, seats used/allowed, workspaces
   used/allowed, valid-until date, and a warning when over the limit or
   expired.
2. **Users:** list email, role, workspaces, status and date added; add,
   change role, change workspace access, deactivate and reactivate users,
   and set a new password for one.
3. **Add user:** email, a temporary password (8+ characters), role, and
   workspace access (at least one workspace for a member). The email must
   not be in use anywhere in MediScan.
4. **Workspaces:** list each with its user count; add one; rename one.
5. **Safety rules:**
   - The last active admin cannot be demoted or deactivated.
   - An admin cannot deactivate themselves.
   - A user moved out of their current workspace is placed in one they can
     still open.

### 5.2 Every user

1. A workspace switcher in the sidebar: the current workspace's name, and a
   list of those the user can open. Switching changes the current workspace
   and reloads the page.
2. `GET /v1/auth/me` returns the organisation, the current workspace and the
   workspaces the user can open.

### 5.3 Platform admin ("Organizations" page)

1. List every organisation with plan, seats and workspaces used/allowed,
   valid-until date and status.
2. Create an organisation with its first workspace and first admin.
3. Change an organisation's plan, valid-until date and active switch.

### 5.4 Sign-up and existing customers

- `POST /v1/auth/register` creates an organisation on the Starter plan, its
  first workspace and its first admin, as one step.
- The migration gives every existing shop its own organisation, named after
  the shop and on the smallest plan that fits its current active users.
  Each user gets access to their shop. No bill, lesson or setting moves.

### 5.5 Audit

Every change is written to the audit log of the workspace it happened in:

- a user added, changed, deactivated, reactivated or given a new password;
- a workspace added or renamed;
- a plan changed;
- an organisation created, activated or deactivated;
- a workspace switch.

## 6. API

| Method and path | Who | Purpose |
|---|---|---|
| `GET /v1/org` | any user | Organisation, plan, usage, limits and the plan table |
| `GET /v1/org/users` | org admin | Users with role, workspaces and status |
| `POST /v1/org/users` | org admin | Add a user (seat-checked) |
| `PATCH /v1/org/users/{id}` | org admin | Role, workspace access, active |
| `POST /v1/org/users/{id}/password` | org admin | Set a new password |
| `GET /v1/org/workspaces` | any user | Workspaces the user can open (all, for an admin) |
| `POST /v1/org/workspaces` | org admin | Add a workspace (limit-checked) |
| `PATCH /v1/org/workspaces/{id}` | org admin | Rename |
| `POST /v1/auth/workspace` | any user | Switch the current workspace |
| `GET /v1/platform/orgs` | platform admin | Every organisation with usage |
| `POST /v1/platform/orgs` | platform admin | Create an organisation, first workspace and admin |
| `PATCH /v1/platform/orgs/{id}` | platform admin | Plan, valid-until date, active |

## 7. Out of scope for this release

- Email invitations and password-reset emails. The admin sets a temporary
  password and passes it on.
- Self-service plan purchase and billing.
- Per-workspace roles. A user is an admin or a member for the whole
  organisation.
- A workspace switcher in the Android app. The app works in the user's
  current workspace, which the web admin switches.
- Moving a workspace between organisations.

## 8. Acceptance

1. A new sign-up gets an organisation (Starter), a workspace and an admin.
2. An admin adds users up to the plan's seat count; the next is refused with
   the reason.
3. A member sees only bills from workspaces they were given. Switching to a
   workspace they were not given is refused.
4. An admin sees all workspaces and can switch between them. Bills and
   suppliers follow the current workspace.
5. Deactivating a user frees a seat and stops their sign-in.
6. The last admin cannot be demoted or deactivated.
7. A platform admin creates an organisation and changes its plan. A
   downgrade below current use blocks adding, and removes no one.
8. An inactive organisation's users cannot sign in.
8a. With an expired licence, uploading, Scan again and Recheck are refused;
    existing bills still open. Renewal restores scanning.
9. The migration upgrades and downgrades cleanly, and existing shops keep
   every bill, lesson and setting.

## 9. Open questions

1. Plan seat and workspace numbers: placeholders in §4, to confirm.
2. ~~After the valid-until date, should scanning also stop?~~ Decided
   4 November 2026: yes. Scanning stops; scanned bills stay open (section 4).
3. Should a member be allowed to see the organisation's plan and usage? This
   release shows it to every user.
