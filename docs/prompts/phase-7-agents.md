# Phase 7 — The full agent graph

**Goal:** the assistant picks the right approach for each question and can explain a change.
**Read:** `docs/plan.md` Phase 7, `design/agents.md` (all), `design/semantic-layer.md` §3–4.

## Order of work (test after each step)

1. `workflows/state.py` and `workflows/graph.py`: the nodes, the routes, the limits; the trace. The agents themselves stay in `agents/`.
2. The supervisor; the labelled routing set (40 questions) with the fake, then measured with the real model.
3. The insight agent: framing, the fixed templates, contributions in code. **Test against stories S1, S2 and S5 before any model is involved.**
4. The report agent: the number check, one regeneration, the template fallback, recommendations, the scope note.
5. Clarifying questions and quick replies in the web app.
6. Re-run the golden set.

## Acceptance

The gate of this phase in `docs/plan.md`. Every box ticked with its evidence, or explained.

## Do not

Let the insight agent write free SQL. Let the model compute a difference or a percentage. Add a sixth agent.
