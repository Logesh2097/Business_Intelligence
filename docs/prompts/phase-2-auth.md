# Phase 2 — Sign-in and roles

**Goal:** people sign in, each sees what their role allows, and an admin manages the company's users.
**Read:** `docs/plan.md` Phase 2, `design/security.md` §2–4, `docs/api.md` §1, §3, §4, `design/ui.md` §1–2.

## Order of work (test after each step)

1. `core/security.py`: Argon2id, access tokens, refresh tokens (hash at rest, rotation, reuse detection); unit tests.
2. `store/users.py`, `store/tenants.py`, `store/audit.py`; the login-attempt limit.
3. `api/deps.py`: current user, company, outlet scope, `require(permission)`; the permission table as data.
4. Routes `/auth/*`, `/admin/users*`, `/admin/outlets`, `/platform/tenants*`; the error body of `api.md` §1.
5. Tests: the role table over every route, the sweep, the guards (last admin, own role), token version.
6. Web: sign-in, the auth store with silent refresh, guards, the menu by permission, Users, Companies.

## Acceptance

The gate of this phase in `docs/plan.md`. Every box ticked with its evidence, or explained.

## Do not

Store the access token in local storage. Accept a company id from a company's user. Add custom roles or single sign-on.
