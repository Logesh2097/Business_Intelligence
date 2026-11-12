# Phase 10 — Cloud

**Goal:** staging and production on GCP, deployed by CI, restorable.
**Read:** `docs/plan.md` Phase 10, `design/deployment.md` (all), `design/security.md` §10. **Every action that creates or changes a cloud resource is confirmed with the owner first.**

## Order of work (test after each step)

1. The production image; run it locally against the Compose stores.
2. With the owner: the projects, the region, the domain, the Atlas and Qdrant Cloud accounts.
3. Staging: Cloud SQL, the bucket, Secret Manager, service accounts with least access, `bi-migrate`, `bi-mcp` (internal), `bi-api`.
4. The CI workflows with Workload Identity Federation; a merge deploys to staging.
5. Alerts; the smoke journey against staging with the real model.
6. The restore, the Qdrant rebuild and the rollback, each done once and timed.
7. Production from a tag, after approval.

## Acceptance

The gate of this phase in `docs/plan.md`. Every box ticked with its evidence, or explained.

## Do not

Put a secret in the repository or an image. Give the API the database owner's password. Run the demo seed in production.
