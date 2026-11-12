# Start here — the master prompt

> Paste this, or just say **"start phase N"** in this repository. `CLAUDE.md` points here.

You are building **the BI Assistant**: a multi-tenant web application where business users ask questions in plain language and get an answer with a table, a chart and recommendations. What it does is in `docs/product/overview.md`; how it is built is in `docs/design/`; the phases and their gates are in `docs/plan.md`.

## Rules (the short version of `CLAUDE.md`)

1. Build **one phase at a time**, in `docs/plan.md` order, using that phase's prompt in this folder. Stop at its gate and report. Do not start the next phase.
2. **Read before writing**: the phase's section of `plan.md` and the design docs its prompt lists.
3. **Tests first where the design gives the cases**: the SQL guard's table, the isolation suite, the planted stories.
4. Every test except the evaluations uses the **fake model**. No test needs an OpenAI key.
5. **Routes hold no SQL and no prompts.** All data access is in `backend/app/store/`. Agents reach data only through `backend/app/tools/`.
6. **No tool takes a company, user or outlet argument.** The caller's context comes from the session.
7. Any new `biz` table comes with `tenant_id`, its composite key, its row-security policy and its grants, in the same migration.
8. Update `docs/api.md` and the design docs **in the same change** as the code. Update the Status line of every doc you make true.
9. Commit or push only if asked.
10. If a design doc and the code disagree, or a decision is the owner's (`docs/design/architecture.md` §9), **ask**. Do not guess.

## Preflight (do this first, report in five lines)

- `python3 --version` (3.10 or newer), `node --version` (22 or newer; `nvm use 22`), `docker --version` and `docker compose version`.
- Ports free: `ss -ltn | grep -E ':(8110|8111|5290|5610|27027|6343|6344)\b'`. If one is taken, choose the next free one and record it in `docs/design/architecture.md` §4.
- The gate of the previous phase is ticked in `docs/plan.md`. If it is not, stop and say so.
- For Phase 3 and later: `BI_OPENAI_API_KEY` is set (needed only for `make eval`).
- Say in ten lines what this phase will produce.

## How to report at the end of a phase

The gate checklist from `docs/plan.md` with each box ticked or explained · the exact commands you ran and their last lines · screenshots of every new screen, desktop and 360 px, light and dark · the evaluation score and its token cost, where one was run · anything deferred, with why · questions for the owner. Write the evidence under the gate in `plan.md`. Then wait.
