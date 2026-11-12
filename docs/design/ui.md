# The web app

**Status:** a first complete web app was built on 2026-11-12 (the owner's Phase 10) on the APIs that exist. It was opened in a headless browser against the real API and PostgreSQL: seven pages render with real figures and no console errors, at desktop and phone width. What is built and what is not:

| Page | Path | State |
|---|---|---|
| Choose a user | `/sign-in` | built. **This is not sign-in.** It lists the demo users (`GET /api/v1/dev/users`, development and test only) and sends the chosen email as `X-Dev-User`. Passwords, tokens and sessions are the authentication phase |
| Dashboard | `/dashboard` | built: 12 figures, revenue by day (line), revenue by outlet (bars), top products, low stock; a period selector |
| Assistant | `/assistant` | built: question box, the conversation of this visit, waiting, clarification, unsupported and error states, the route and agent of each answer, tables, charts, sources, the query for roles that may see it. **Not built:** streaming, saved conversations, follow-up questions that use earlier ones |
| Analytics | `/analytics` | built: Sales, Inventory, Customers, Across areas; period selector. The outlet filter narrows panels that have an outlet column, in the browser: no tool takes an outlet (rule 2 of `CLAUDE.md`). A free date range is not offered: the tools take named periods |
| Reports and insights | `/insights` | built as on-demand insights from one agent (summary, observations, table, chart, query). **Saving, sharing and exporting a report are not built** (no API) and the page says so |
| Knowledge base | `/knowledge` | built: list, status, upload, re-index, delete with confirmation; managing is shown to admins only. Needs MongoDB and Qdrant; without them it shows the API's "not available" |
| Activity | `/activity` | built: the query log for analysts and admins (`GET /api/v1/activity`), the tools the caller may use, and how agents reach them. Viewers see the tools and a note |
| Status | `/status` | as in Phase 0 |

**Visual design (2026-11-13): the Digitory Reservation portal's design system.** The look was ported from `~/reservation/table_reservation` (`web/portal` and its `doc/design/design-sop.md`), so both products are one system. Only styling changed: no store, API call or behaviour.

| Part | What it is now | Where |
|---|---|---|
| Colour | one brand colour, an ocean blue (`#0277bd`; changed from the reservation app's orange on the owner's request, 2026-11-13); the page (a very light blue, 5 % of the brand, fading from white at the top), card, brand wash, lines and chart colours are derived from it; light and a designed dark | `lib/theme.ts` (with contrast tests), `plugins/vuetify.ts` (`appLight`, `appDark`) |
| Tokens | ink ramp, hairlines, fills, radii (8 / 12 / 16), spacing, one shadow recipe for things that float, motion | `assets/main.css` |
| Type | Inter (the same font files), six sizes only: 32, 24, 18, 15, 13, 11; Vuetify's own type classes are mapped onto them | `assets/main.css`, `assets/fonts/` |
| Top bar | 56 px, plain surface, a hairline under it, a soft brand wash at the left that is gone by 300 px. The app is named **DataPulse** in the bar and the page title; there is no mark (removed on the owner's request) | `App.vue` |
| Sidebar | 240 px rail on the card ground, grouped with small capital labels; the active row wears the brand wash and its icon the brand as words; folds to icons and opens on hover | `layouts/AppSidebar.vue`, `lib/nav.ts` |
| Phone | a bottom bar with four screens and "More"; the full list opens from the right | `layouts/MobileNav.vue` |
| User menu | initials on the wash; theme switch and "Switch user" inside | `layouts/AppUserMenu.vue` |
| Page header | title 24 px on the page ground, second line, the page's controls on the right | `components/PageHeader.vue` |
| Cards | flat, a hairline, radius 12; a titled card has a head on the brand wash | `components/DataCard.vue` |
| Tiles | neutral card, the figure at 24 px, the only colour is the icon | `components/StatTile.vue` |
| Tables | head on the brand wash, soft rules, tabular numbers, row hover; **five rows a page** with a pager under every table (`components/TablePager.vue`, `lib/paging.ts`), hidden when all rows fit | `assets/main.css` |
| Fields, buttons | outlined 44 px fields; buttons 44 / 36 / 32, flat; the brand fill is kept for the one primary action | `plugins/vuetify.ts`, `assets/main.css` |
| Chips | status chips are the hue at 14 % with a glyph; a chosen chip is green | `assets/main.css` |
| Notices | a tonal wash with ink words and an accent bar at the start | `assets/main.css` |
| Charts | the brand's chart colour on the neutral track | `BarChart.vue`, `LineChart.vue` |

Not ported: the reservation app's table-to-cards layout below 960 px (a wide table scrolls sideways inside its card here), its command palette, alerts drawer and tours (the BI app has no such features), Chart.js (charts stay plain SVG, restyled), and its build-time icon subsetting.

**Differences from the design below.**

- Charts are plain SVG and CSS (`BarChart.vue`, `LineChart.vue`), not ECharts: no dependency was added. A chart is drawn only for one label or date column with a figure, 2 to 25 bars or up to 400 points, no missing or negative value (`lib/chart.ts`); otherwise the table stands alone.
- Answer text is shown as plain text with line breaks, not Markdown. `v-html` is still a lint error and is used nowhere.
- Money is written by the browser's `Intl` in the company's currency.
- No figure is computed in the browser. Tiles, tables and charts show what a tool or an agent returned.

**Known rough edge:** in one headless screenshot the Analytics title sat a few pixels under the drawer's edge. Not reproduced on the Dashboard; to be checked in a real browser.

**Tests:** 61 (`lib/format`, `lib/chart`, `lib/answer`, `api/client`, the stores, and one component test). The component test exists because a real look at the page found that no chart was ever drawn (an absent boolean prop is `false` in Vue); unit tests of the logic had all passed.

## 1. Stack and structure

Vue 3 with TypeScript, Vite, Pinia (state), Vue Router, Vuetify (components), ECharts through `vue-echarts` (charts), `markdown-it` with `DOMPurify` (answer text).

```
frontend/src/
├── main.ts   App.vue   router.ts
├── api/           client.ts (fetch wrapper: token, refresh, errors), sse.ts (the answer stream), one file per API area
├── stores/        auth.ts, chat.ts, reports.ts, documents.ts, admin.ts
├── pages/         one file per page (below)
├── components/    AnswerCard, ResultTable, ResultChart, SqlBlock, SourceList, StepStatus, QuestionBox, …
├── lib/           format.ts (money, numbers, dates by company currency and locale), chart.ts (spec → ECharts option)
└── styles/        tokens (colours, spacing), light and dark themes
```

Rules:

- **A page fetches through a store; a component gets props.** Components do not call the API.
- **The access token lives in the auth store's memory only.** On load the app calls `/auth/refresh` (the cookie) to get one; a `401` on any call triggers one refresh and one retry, then sign-in.
- **What a role cannot do is not shown.** The permissions come from `/auth/me`. The server still checks every call; hiding is for clarity, not for security.
- **The model's text is never inserted as HTML.** It is rendered from Markdown through a sanitiser; images and raw HTML are dropped.

## 2. Pages

| Page | Path | Who | What it holds |
|---|---|---|---|
| Sign in | `/login` | anyone | email, password, a readable error, a notice when attempts are limited |
| **Chat** | `/chat`, `/chat/:id` | all | the conversation list, the conversation, the question box (§3) |
| Reports | `/reports` | all | saved reports as cards: mine and shared. Open, refresh, export, share, delete by permission |
| A report | `/reports/:id` | all | title, the question, the chart, the table, when it was last refreshed, the query (analysts and admins) |
| Documents | `/documents` | admin | the list with status, an upload box, re-index, delete |
| Users | `/admin/users` | admin | the list, create, role, outlets, reset password, deactivate |
| Activity | `/admin/activity` | admin | the query log (question, status, time, rows; open one to see its SQL), the audit log, usage against the daily limits |
| Examples | `/admin/examples` | admin | learned examples waiting for review: question, SQL, run it, approve or reject |
| Glossary | `/glossary` | all | what each business word means here (from the metric definitions), in plain words |
| Companies | `/platform/tenants` | super admin | the list, create a company and its first admin, deactivate |
| Status | `/status` | development only | the health of the API and its three stores (the Phase 0 page) |

**The shell:** a top bar (the company's name, the user menu with theme switch and sign out) and a left drawer (Chat, Reports, Documents, Admin, Glossary), which becomes a bottom bar on a phone.

## 3. The chat screen

```
┌───────────────┬──────────────────────────────────────────────────────────┐
│ + New chat    │  Revenue last month                                      │
│               │                                                          │
│ Today         │  You: What was our total revenue last month?             │
│  Revenue last…│                                                          │
│  Low sellers  │  ┌─ Assistant ────────────────────────────────────────┐  │
│ Yesterday     │  │ Revenue in October was ₹18,42,300, up 6.1% on      │  │
│  Why sales…   │  │ September.                                         │  │
│               │  │                                                    │  │
│               │  │ [ chart: revenue by outlet ]                       │  │
│               │  │ [ table ]                          Export  Save    │  │
│               │  │ ▸ Query          ▸ Sources                         │  │
│               │  │ Period: 1–31 Oct 2026 · All outlets      👍 👎     │  │
│               │  └────────────────────────────────────────────────────┘  │
│               │                                                          │
│               │  [ Ask about your business…                     Send ]   │
└───────────────┴──────────────────────────────────────────────────────────┘
```

**An empty conversation** shows six suggested questions drawn from what the company's data can answer (from `/meta/suggestions`), so nobody faces a blank box.

**While the answer is being made** the card shows the current step as the server reports it ("Writing the query…", "Running it…", "Writing the answer…"), then the table, then the chart, then the text growing word by word. A **Stop** button ends the run.

**The answer card**, top to bottom:

| Part | Notes |
|---|---|
| The text | Markdown, sanitised. Figures formatted in the company's currency |
| The chart | when there is one. A switch to view it as a table, and to download it as an image |
| The table | sortable, at most 200 rows on screen, a note when it was cut, **Export CSV** (permission) |
| Recommendations | a short list, each with the figure it rests on |
| **Query** (collapsed) | the SQL, read-only, with a copy button. Analysts and admins only |
| **Sources** (collapsed) | the documents quoted: filename, page, the quoted lines |
| The footer | the period and the scope used ("Limited to your outlets: …"), thumbs up and down (down asks for a short comment), **Save as report** |

**A clarifying question** from the assistant is shown as a normal message with quick-reply buttons when the choices are few ("This month" / "Last month").

**Errors** are shown in the card with a readable sentence, the request id and a **Try again** button. The question is never lost.

## 4. Charts

The server sends a small description; the browser draws it. The model never writes chart code.

```json
{ "type": "bar", "title": "Revenue by outlet", "x": "outlet", "y": ["revenue"], "series": null, "unit": "money" }
```

`lib/chart.ts` turns it into an ECharts option:

| Type | Drawn as |
|---|---|
| `kpi` | one large figure, with the change against the comparison when there is one |
| `line` | time on the horizontal axis; one line per measure or per series |
| `bar` | sorted as the data came; horizontal when labels are long |
| `stacked_bar` | the second category as the stack |
| `pie` | a ring, six parts at most, labels with shares |
| `table` | no chart |

Rules: axes start at zero for bars; money axes use the company's currency and the Indian or international digit grouping by locale; colours come from the theme tokens and work in dark mode; every chart has a title and a text alternative (the table).

## 5. States every page handles

Loading (a skeleton, not a spinner over an empty page) · empty (a sentence and the next action) · error (a sentence, a retry) · no permission (the page is not in the menu; a direct link shows "You do not have access to this") · offline (a banner).

## 6. Width, theme, language

- Works from 360 px wide. On a phone the conversation list is a drawer and tables scroll sideways inside their card.
- Light and dark themes; the choice is remembered in the browser.
- English only in the first version. All text passes through one messages file so another language can be added.

## 7. Testing

Unit tests (Vitest) for `lib/` (formatting, chart options, the event-stream parser) and for the stores with a mocked API. Component tests for the answer card in each state. A few end-to-end tests with Playwright against the running stack with the fake model (`testing.md` §6): sign in, ask, see a table and a chart, save a report, a viewer does not see the query.
