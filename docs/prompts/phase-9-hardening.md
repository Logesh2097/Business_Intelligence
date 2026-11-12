# Phase 9 — Hardening

**Goal:** the whole build is reviewed against the security design and measured.
**Read:** `docs/plan.md` Phase 9, `design/security.md` (all), `design/testing.md` §4–7, `docs/api.md` §10.

## Order of work (test after each step)

1. The limits and the one-answer rule, with tests at each threshold.
2. Headers and the content policy; check the web app still works under it.
3. Activity, Examples and Glossary pages with their routes; approval embeds an example for that company only.
4. The authorization matrix generated into `design/security.md` and its staleness test.
5. The review: read every route, tool and store function against `security.md`; write the findings table; fix or list for the owner.
6. `pip-audit`, `npm audit`; the load run; the golden set to target; record costs in `design/deployment.md` §9.

## Acceptance

The gate of this phase in `docs/plan.md`. Every box ticked with its evidence, or explained.

## Do not

Mark a finding accepted without the owner's word. Tune a prompt to pass one golden question while another group drops.
