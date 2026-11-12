# What the BI Assistant does

**Status:** the product description the build follows. Nothing here is technical; the engineering view starts at `../design/architecture.md`.

## In one paragraph

The BI Assistant lets people in a business ask questions about their own numbers in ordinary language and get an answer they can trust: a short written answer, the table behind it, a chart, and, when it helps, a recommendation. It reads the company's sales, inventory, customer, purchasing and expense data, and the documents the company uploads. Each company sees only its own data.

## Who uses it

| Person | What they do here |
|---|---|
| **Viewer** (an outlet manager, a team lead) | Asks questions, reads answers and shared reports. May be limited to some outlets |
| **Analyst** | Everything a viewer does, and also sees the query behind an answer, saves answers as reports and exports them |
| **Admin** (the company's owner or head of operations) | Everything an analyst does, and also manages users, decides who sees which outlets, uploads documents, and reviews what was asked |
| **Platform admin** (us) | Creates a company and its first admin. Cannot read a company's data through the product |

## What a person can ask

| Kind of question | Examples | Where the answer comes from |
|---|---|---|
| **A number** | What was our total revenue last month? How many orders did we take yesterday? | the sales data |
| **A ranking** | Which outlet has the highest revenue? What are our top 10 products this quarter? | the sales data |
| **A weak spot** | Which products have low sales? Which items are below their reorder level? | sales and inventory |
| **A trend** | Show revenue by week for the last three months. | the sales data, as a line chart |
| **A comparison** | Compare dine-in and delivery this month against last month. | the sales data |
| **Profit and cost** | What is our profit by outlet this month? Where did expenses rise? | sales, cost of goods and expenses |
| **Customers** | How many new customers joined this month? Which segment spends the most? | customer and sales data |
| **A reason** | Why did sales decrease this month? | a breakdown of the change, plus the company's documents |
| **Something written down** | What is our refund policy? What promotions ran in September? | the uploaded documents |

## What an answer looks like

1. **The answer in a sentence or two**, with the figures in it.
2. **The table** the answer was read from.
3. **A chart**, when the data has a shape worth drawing (a trend, a ranking, a share).
4. **Recommendations**, when the question invites them. Each one names the figure it is based on.
5. **Where it came from**: the documents quoted, and, for analysts and admins, the query that was run.

For a "why" question the answer also shows **what changed and by how much**: for example, that revenue fell by ₹4.2 lakh, that one outlet accounts for most of the fall, and that the outlet was closed for nine days according to the operations notes.

## Rules that always apply

1. **Every figure comes from the data.** The assistant does not estimate or invent a number. If the data cannot answer the question, it says so.
2. **A person sees only what they are allowed to see.** Another company's data is never reachable. A user limited to two outlets gets answers about those two outlets only, and the answer says so.
3. **The assistant only reads.** It cannot change, add or delete business data.
4. **Customer personal details stay private.** The assistant can count and group customers; it does not show names, phone numbers or email addresses.
5. **When a question is unclear, it asks.** "Sales" for which period? It asks one short question instead of guessing.
6. **Every question is recorded** for the company's admin: who asked, what was run, how long it took.

## What it does not do (first version)

- It does not forecast the future.
- It does not send scheduled reports by email.
- It does not connect to a live point-of-sale system. The first version runs on sample data for two demo companies; connecting real data is a later step.
- It does not answer questions outside the business (general knowledge, coding, and so on).

## The demo companies

| Company | Kind | Outlets |
|---|---|---|
| **Brew & Bite Cafés** | café chain | 5 |
| **UrbanMart Retail** | retail stores | 4 |

Each has twelve months of orders, products, customers, stock, purchases and expenses, and a few documents. Some stories are planted in the data on purpose (a sales dip with a real cause, a few slow products, an expense spike) so that the assistant's answers can be checked. They are listed in `../design/data-model.md` §7.
