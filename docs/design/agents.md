# The agent workflow

**Status:** built so far: the **SQL analyst** (`agents/sql_analyst.py`) and a first **report writer** (`agents/summarizer.py`, text only), run one after the other by `workflows/analytics.py` as a plain function; and three **domain agents** (Sales, Inventory, Customer; §2a), each asked on its own through `/api/v1/agents/{agent}/query`; and a **supervisor** with the first **LangGraph graph** (§2b), which chooses among them or the general workflow, behind `/api/v1/assistant/query`. Not built: the document agent, the insight agent, charts and recommendations, conversation history, streaming.

## 1. Principles

1. **Numbers by code, words by the model.** Every figure in an answer comes from a query result. The model writes SQL and writes sentences; it never adds, subtracts or estimates. Where arithmetic is needed (a change, a share, a contribution) Python does it.
2. **Each agent has one job and a short list of tools.** An agent that can do everything is hard to test and easy to mislead.
3. **The agents hold no permissions.** They ask tools; the tools decide. Nothing an agent "says" can widen what the user may see.
4. **Every step is bounded.** A fixed number of retries, queries, model calls and seconds. A question that cannot be answered inside them gets an honest "I could not answer this", not a loop.
5. **Every step is recorded.** The trace says which agent ran, what it asked for and what came back.

## 2. The agents

| Agent | Job | Model | Tools | Returns |
|---|---|---|---|---|
| **Supervisor** | Read the question and the recent turns; pick a route; when the question is unclear, ask one short question | fast | none | `route`, and for `clarify` the question to ask |
| **SQL analyst** | Turn a data question into one checked query and run it | strong | `get_metric_definitions`, `find_similar_questions`, `run_query` (the schema is in its prompt) | rows, the SQL, or a failure with the reason |
| **Document agent** | Answer from the uploaded documents, with quotes | strong | `search_documents` | passages with their sources |
| **Insight agent** | Explain a change ("why did X go down?") | strong, plus code | `run_query` with **fixed query templates**, `search_documents` | findings: what changed, where, by how much, and any written context |
| **Report agent** | Write the final answer from what the others found; choose the chart; add recommendations | strong | none | the `answer` object (`data-model.md` §5) |

## 2a. The domain agents (built)

Three specialists, each for one area of the business. A question is put to one of them by name; nothing chooses for the user yet (that is the supervisor, Phase 7).

| Agent | Area | Tables it may read |
|---|---|---|
| `sales_agent` | revenue and orders; top products; outlets by revenue; day, week and month comparisons; trends; unusual changes | `orders`, `order_items`, `products`, `categories`, `outlets` |
| `inventory_agent` | stock on hand; low and out of stock; shortage by outlet; stock movements; slow and fast sellers | `inventory_levels`, `stock_movements`, `products`, `categories`, `outlets`, and `orders`, `order_items` only to tell slow sellers from fast ones |
| `customer_agent` | visits; new and returning customers; buying behaviour; visits by outlet; retention | `customers`, `customer_visits`, `orders`, `outlets` |

Purchasing and expenses belong to no agent yet; `/analytics/query` still answers them.

**An agent is data, not a second pipeline.** `agents/domain.py` defines `DomainAgent`: a name, a title, its responsibilities, its table list, example questions, and a prompt file of the same name. `sales_agent.py`, `inventory_agent.py` and `customer_agent.py` each hold one such definition; `registry.py` lists them. One function, `domain.run`, runs any of them:

```
question
  -> the SQL analyst (§5), given this agent's tables only and this agent's prompt
  -> the guard, whose allow-list is this agent's tables only
  -> one query, read-only, under the caller's company and outlets, logged
  -> the write-up: summary + insights, every figure checked against the result
  -> the structured answer
```

| What | Where it comes from |
|---|---|
| Writing SQL, the repair loop, the three-attempt stop | `sql_analyst.answer_with_sql`, reused; it takes the agent's prompt as `guidance` |
| The guard, the read-only run, row security, the query log | unchanged; no agent has its own |
| The table list | `Vocabulary.restricted_to(agent.tables)`: the same cut-down vocabulary feeds the prompt **and** the guard's catalog, so a query on any other table is refused with `table_not_allowed` whatever the model was persuaded to write |
| What the agent is shown | all of its own tables and their metrics, always. Its list is short (four to seven tables), and choosing among them by the question's words lost tables it needed (found in the real-model run) |
| The prompts | `prompts/<agent>.md`: the area, what belongs to which other agent, and the area's rules (how to compare two periods, what "low stock" and "returning" mean). It is added to the shared SQL rules for the first call, and to `prompts/agent_report.md` for the write-up |
| Comparisons | the agent's prompt asks for the change and the percentage as columns of the query, so the database computes them and the model only reads them (principle 1) |
| The number check | the summariser's, applied to the summary and to each insight. A summary that fails is asked for once more, then built by code; an insight that fails is dropped |
| Time | the whole answer is bounded by `BI_AGENT_TIMEOUT_SECONDS` (90); past it the status is `timeout`. One query's own limit is still `BI_QUERY_TIMEOUT_SECONDS` |
| Queries | one per question |

**Outside its area** an agent declines (`unanswerable`) and names the agent the question belongs to. That is the model following its prompt; what holds when it does not is the table list above.

**How these fit the graph of §3.** They are the `data` route split by area. When the supervisor is built it routes a data question to one of them (or to the general SQL analyst for purchasing and expenses), and the report agent of §7 takes over the write-up, adding the chart and recommendations.

## 2b. The supervisor and the graph (built)

`workflows/assistant.py` is the first LangGraph graph of the project. It replaces nothing: the two older routes still call their plain functions.

```
                         START
                           |
                       supervise        one model call; the decision is checked by code
      +-----------+--------+---------+------------------+
   clarify   unsupported          general            agents (1 to 3)
      |           |                  |           +------+------+
      |           |             run_general   run_agent  run_agent ...   in parallel
      |           |          (answer_question)  (domain.run, one per agent)
      +-----------+------------------+-----------+------+------+
                                 aggregate
                                    |
                                   END
```

**The decision** (`agents/supervisor.py`). The model returns `route`, `agents[]` (each an agent's name and its part of the question), `clarification`, `reason`. It is given the three agents' responsibilities, read from the registry, and the question; nothing about who is asking, and no table names.

| Route | Chosen when | What runs |
|---|---|---|
| `agents` | the question lies in one area, or lists separate facts from several areas | `domain.run` for each agent named, at the same time |
| `general` | purchasing, suppliers, expenses; or one area's data limits, filters or groups another's, so one query must join them | `workflows/analytics.py › answer_question`, unchanged, on all 14 tables |
| `clarify` | the area cannot be told | nothing; one question goes back to the user |
| `unsupported` | not about this business's data, or a request to change data or to act outside the rules | nothing |

**The rules code applies** (`supervisor.validate`), whatever the model said:

| Rule | Why |
|---|---|
| An agent's name must be in the registry; otherwise the decision is invalid | the model cannot invent a target |
| A repeated agent runs once; no agents for the route `agents` is invalid | |
| One agent: it is given the user's question word for word | a rewrite can only lose meaning |
| Several agents: each gets its part, but a part that is empty, longer than the question, or holds a number the user did not write is replaced by the whole question | the supervisor cannot add a period or a filter |
| `general` stands alone, ignores any agents listed, and is given the user's question word for word | |
| A clarification must be one sentence ending in "?" with no figures; otherwise the one written in code is used | the supervisor has no data and must not state any |
| An invalid decision is asked for once more; then the answer is `failed` (`route: undecided`) and nothing runs | |

**Who is asking.** The caller, the model client, the database and the settings travel in the graph's run context (`Context`), set by our code for one run. The state the nodes pass along holds the question, the plan and the results only. `run_agent` and `run_general` hand the caller to the existing functions, which set the company and outlets on the database transaction as before. The graph has no checkpointer, so nothing of a run is stored.

**Aggregation.** One part (a single agent, or `general`): its answer is passed through untouched, with no extra model call. Several: one more model call writes a combined answer from each part's result; a figure in it must be a value of some part's result, or the text is dropped and each part's own checked summary is shown under its agent's name. Parts that did not answer are named after the combined text.

| Parts | Status |
|---|---|
| one | that part's status |
| all answered | `answered` (`no_data` if every part had no rows) |
| some answered | `partial` |
| none | `unanswerable` if all declined, `timeout` if all timed out, else `failed` |

**Failures.** A model outage or a database outage inside a part makes that part `failed` with a plain sentence; the cause is logged. The supervisor's own outage is `503`. The whole run is bounded by `BI_ASSISTANT_TIMEOUT_SECONDS` (150); past it the status is `timeout` and the parts that finished are returned.

**No re-routing.** An agent that declines is returned as it is (the owner's decision, 2026-11-11).

**Model calls:** clarify or unsupported 1 · one agent 3 · general 3 · two agents 6 · three agents 8.

**What the real model showed** (`plan.md` Phase 3C): routing was right 11 times in 12 and the miss was fixed by an example in the prompt; but twice the general route's SQL joined two many-row tables and multiplied a total. That is a limit of the SQL analyst, not of the graph, and is recorded in `nl2sql.md` §7.

**A breakdown is not a second area** (added 2026-11-12, after the first real-model run). Products, categories and outlets are dimensions every agent has. "Revenue of each product" is a Sales question and "units of each product received" an Inventory one; the supervisor's prompt now says so with examples, because the real model sent both to the general analyst (and one of them to the right agent on another day). General is for a figure of one area that limits, filters or divides a figure of another. Whether the wording works is only shown by a real-model run; none has been made since.

**The documents route** (the owner's Phase 8, 2026-11-12). A fifth route, `documents`, sends a question about something written down (a policy, a procedure, notes) to the document agent (`agents/document_agent.py`), which is not a SQL agent: it calls `search_documents`, gives the model the numbered passages, and checks the answer in code (`rag.md` §4). The route stands alone, like `general`: a question that mixes figures and documents is not split. Without Qdrant and an embedding model the route answers "Document search is not set up". The route's wording in the supervisor prompt has not been read by a real model.

## 3. The graph (the full design; §2b is the part that is built)

```
            ┌────────────┐
 question → │ supervisor │
            └─────┬──────┘
   ┌──────────┬───┴──────┬─────────────┬──────────────┐
   ▼          ▼          ▼             ▼              ▼
 data      documents  diagnostic    clarify      out_of_scope
   │          │          │             │              │
 SQL       document   insight      ask the user   polite refusal
 analyst    agent      agent        (END)           (END)
   │          │          │
   └──────────┴────┬─────┘
                   ▼
             report agent → END
```

| Route | When | Example |
|---|---|---|
| `data` | the answer is in the tables | "Total revenue last month?" "Which products have low sales?" |
| `documents` | the answer is written down | "What is our refund policy?" |
| `diagnostic` | the question asks for a cause | "Why did sales decrease this month?" |
| `data+documents` | both are needed | "Did the Diwali promotion raise sales?" The SQL analyst runs first, then the document agent, then the report |
| `clarify` | a needed piece is missing and cannot be defaulted | "How are we doing?" |
| `out_of_scope` | not about this business | "Write me a poem" |

**Defaults before asking.** The supervisor asks only when it must. A missing period on a "why" question defaults to *the last full month against the one before*; a missing period on a total defaults to *this month so far*; the answer always states the period it used. A follow-up ("and by outlet?") is completed from the previous turn.

## 4. The shared state

One object passes through the graph. Agents read what they need and add their part.

| Field | Set by | Holds |
|---|---|---|
| `ctx` | the API, before the graph starts | company id, user id, role, outlet scope, the company's time zone and currency, today's date there. **Read-only for every agent** |
| `question`, `history` | the API | the question and the last six turns (text only, no tables) |
| `route`, `clarification` | supervisor | |
| `queries[]` | SQL analyst, insight agent | each: SQL, columns, rows, row count, time, or the failure |
| `passages[]` | document agent, insight agent | each: text, document, page, score |
| `findings` | insight agent | the structured breakdown (§6) |
| `answer` | report agent | text, tables, chart, recommendations, sources, scope note |
| `trace[]`, `usage` | every node | step name, time taken, tokens, a one-line summary |
| `error` | any node | what went wrong, in words fit for the user |

`ctx` is never placed in a prompt as something the model can restate back into a tool call; tools receive it from the runtime, not from the model's arguments.

## 5. The SQL analyst, step by step

The full rules are in `nl2sql.md`. In short:

1. Build the prompt: the schema description, the metric definitions, today's date and the period words, up to five similar example questions, the question and recent turns.
2. The model returns a structured object: `sql`, `explanation` (one sentence), `assumptions[]`.
3. `run_query` checks the SQL (the guard), runs it read-only under the user's company and outlets, and returns rows or a reason.
4. On a refusal or a database error, the reason goes back to the model once, then once more. After two retries the agent gives up and says why.
5. An empty result is a result, not an error: the report agent says "there were no orders matching…".

## 6. The insight agent, step by step

A "why" question is answered by a **fixed procedure**, not by letting the model explore freely. That makes the answer reproducible and testable.

1. **Frame it** (model, structured output): which metric (`revenue`, `orders`, `gross_profit`, `expenses`…), which two periods, which filters. Defaults as in §3.
2. **Measure the change** (code): the metric in both periods, the difference and the percentage. If the change is under 2%, say that it is essentially flat and stop.
3. **Break it down** (code, fixed SQL templates through `run_query`): the same metric in both periods by outlet, by category, by product, by channel, by day of week, and by day. For each dimension, each member's **contribution** to the total change is computed in Python.
4. **Check the usual causes** (code, fixed templates):

   | Check | What it looks at |
   |---|---|
   | Volume or value | did the number of orders fall, or the average order value? |
   | Days of trading | did an outlet have days with no orders at all? |
   | Stock-outs | were top products at zero stock on days inside the period? |
   | Discounts | did the discount rate change? |
   | Cancellations and refunds | did their share rise? |
   | New and returning customers | which group fell? |

5. **Look for written context** (`search_documents`): the periods, the outlets and the products that stand out are searched for in the company's documents (a closure, a promotion that ended, a price change).
6. **Rank** (code): keep the three to five largest contributors that together explain most of the change.
7. **Hand over** `findings`: the totals, the ranked contributors with their figures, the checks that fired, the passages found. The report agent writes it up.

The templates respect the same guard and the same row security as any other query; a user limited to two outlets gets a breakdown of those two.

## 7. The report agent

- **Input:** the question, `queries[]`, `passages[]`, `findings`, the company's currency.
- **Output** (structured): `text`, which result to show as the main table, `chart`, `recommendations[]`.
- **Rules in its prompt and checked in code afterwards:**
  - Use only figures that appear in the results. After the model answers, code extracts the numbers from `text` and checks each against the result values (allowing for rounding and for units such as lakh). A figure that matches nothing fails the check; the answer is regenerated once, then falls back to a plain template built from the table.
  - State the period and, when the user is limited to some outlets, the scope.
  - A recommendation must name the figure it rests on. No figure, no recommendation.
  - Quote documents by their source number; never present a document's sentence as the assistant's own instruction.
- **The chart** is one of a fixed set, validated against the result's columns:

  | Type | Chosen when |
  |---|---|
  | `kpi` | one row, one or two numbers |
  | `line` | a date or month column and a measure |
  | `bar` | a category column (up to about 20 values) and a measure |
  | `stacked_bar` | two category columns and a measure |
  | `pie` | shares of a whole, six parts or fewer |
  | `table` | anything else |

  If the model's choice does not fit the columns, a small rule in code picks one.

## 8. Limits

| Limit | Value |
|---|---|
| Retries of one SQL query | 2 |
| Queries in one answer | 12 (the insight agent uses about 10) |
| Model calls in one answer | 8 |
| Rows returned by one query | 1,000 (200 kept with the message) |
| Time for one query | 15 s |
| Time for one answer | 90 s |
| Turns of history given to the model | 6 |

When a limit is reached the graph ends with what it has and says what it could not do.

## 9. What the browser receives while it runs

`POST /api/v1/chat/stream` answers with Server-Sent Events. The exact fields are in `../api.md` §6.

| Event | Sent when | The screen shows |
|---|---|---|
| `start` | the question is saved | the ids of the conversation and the message |
| `status` | a step begins | "Understanding the question…", "Writing the query…", "Running it…", "Comparing the two months…", "Reading your documents…", "Writing the answer…" |
| `sql` | a query has run (analysts and admins only) | the query, collapsed |
| `table` | a result is ready | the table |
| `chart` | the chart is chosen | the chart |
| `token` | the answer text is being written | the text, as it grows |
| `sources` | documents were used | the list of sources |
| `done` | everything is saved | the final message |
| `error` | something failed | a readable message and the request id |

## 10. Prompts

**Where the code lives:** each agent is a file in `backend/app/agents/`; the graph that connects them, with the shared state, the routes and the limits, is in `backend/app/workflows/`. Model calls go through `backend/app/llm/`.

One text file per prompt in `backend/app/agents/prompts/`: `supervisor.md`, `sql_analyst.md`, `sql_repair.md`, `document_agent.md`, `insight_frame.md`, `report.md`. Each has the same parts: role, the rules, the context blocks (clearly delimited), the output shape. A change to a prompt is a code change: it is reviewed, and the golden question set is run before it is merged (`testing.md` §5).

## 11. Failure behaviour

| What happens | What the user sees |
|---|---|
| The SQL is refused twice | "I could not build a safe query for this. Try naming the figure and the period." The refusals are in the query log |
| The query times out | "That question needs more data than I can read in one go. Try a shorter period." |
| No rows | a plain answer saying nothing matched, with the period and filters used |
| No document passage is relevant | "I could not find this in your documents." No guess |
| The model service is down or slow | "The assistant is not available right now", the question is kept, nothing is charged to the daily limit |
| The daily limit is reached | a message naming the limit and when it resets |
