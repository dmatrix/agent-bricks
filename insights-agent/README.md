# Bakehouse insights-agent — structured and unstructured data, on one question

A business analyst for a bakery franchise chain, built on Agent Bricks and deployed as a Databricks
App. Ask it one question and it decides which source answers it: **Genie** for numbers, **Vector
Search** for what customers wrote, or both — then tells you which source each claim came from.

Built with the **OpenAI Agents SDK** (`agentbricks init --framework openai`) on
`system.ai.gpt-5-5` through the AI Gateway, so there is no OpenAI key to manage.

![An analyst at a desk asks "which franchises sold the most, and what are customers complaining
about?" — one question, two sources. The insights-agent chat answers with a bar chart of total sales
by franchise, with a panel of quoted customer reviews beneath it. The app card beside it lists four
steps: ask in plain English, Genie answers the numbers, Vector Search answers the opinions, then
answer with both sources cited. A footer notes the agent is deploy
only](images/insights_agent_scene.png)

Bakerhouse insight agent architecture in relatiion to Agent Bricks

![insights-agent architecture: a chat app and HTTP client post invocations to the Agent Bricks
runtime, which drives an OpenAI Agents SDK agent over three sources — a Genie space for structured
sales data, a Vector Search index for customer reviews, and in-process Python tools behind a
human-approval gate — with session and memory stores bound and every turn traced in
MLflow](images/insights_agent_architecture.svg)

**This agent runs deployed only.** `agentbricks dev` cannot build its graph: every tool binding is
`auth = "user"`, and request-user auth needs a signed-in person, which only the deployed app behind
Apps ingress has. That is the price of querying as *you* rather than as the app's service
principal — see **Why deploy-only**.

## The two data components

The demo's whole point is that these answer different questions and the agent has to choose.

### Genie — the numbers

A Genie space pinned to four **public** Unity Catalog tables:
`samples.bakehouse.sales_transactions` (3,333 rows), `sales_customers` (300), `sales_franchises`
(48) and `sales_suppliers`. Bound with one command:

```bash
agentbricks tools add genie-agent <32-hex-space-id> --name bakehouse_sales --auth user
```

That produces three tools — `bakehouse_sales_ask`, `_poll`, `_query_result`. What matters for a demo
is the third: **Genie returns the SQL it generated and the rows it got**, so the audience can audit a
number instead of trusting it. Two behaviours to know:

- `ask` waits up to 120 seconds and can time out. When it does, call `poll` — never resubmit.
- `query_result` caps at 100 rows with a `truncated` flag, so frame questions as aggregates.

`genie-agent` pins **one** space from the manifest, so the model cannot wander between spaces.

### Vector Search — the opinions

A DELTA_SYNC index over customer reviews, retrieved by meaning rather than keyword:
`jules_catalog.default.bakehouse_reviews_index` — **196 chunks averaging 592 characters**, embedded
with `databricks-gte-large-en`.

It is wired in code rather than in `agent.toml`, because the manifest has no vector-search kind —
see `agent/mcps.py`. Unity Catalog exposes a vector-search MCP server per schema at
`/api/2.0/mcp/vector-search/{catalog}/{schema}`, so the index arrives as an MCP tool and needs no
retriever library: this project ships neither LangChain nor a vector-search SDK.

Each hit carries a `franchiseID`, which is what lets the agent attribute a complaint to the same
franchise Genie just ranked. That linkage is what makes a two-source question work.

**On the source table.** The index reads `jules_catalog.default.bakehouse_review_chunks`, a thin
derived copy of public `samples.bakehouse.media_gold_reviews_chunked`. The copy exists for exactly
one reason: a DELTA_SYNC index needs a unique single-column primary key, and the public table's
`chunk_id` is **not** unique — 196 rows, 138 distinct values. The derived table adds
`chunk_pk = md5(franchiseID|review_date|chunk_id)`, unique at 196, and keeps `franchiseID`. The
public data is never modified.

### Python tools — the action

These run **in the app process**, which is what distinguishes them from the two MCP sources above —
nothing here runs on your machine, because this agent has no local mode at all.

`send_message` carries `needs_approval=True`, so the turn **pauses** and emits an `interrupt`; the
client resumes with the same `session_id`. Memory tools (`remember`, `recall`, `forget`,
`list_memories`) are bound to the Memory Store and keyed by actor.

## Act 1 — build it live (~8 min)

Every step is one command, and nothing but `agent.toml` changes between them.

```bash
agentbricks init insights-agent --framework openai     # scaffold + chat app; binds stores & tracing
cd insights-agent
agentbricks tools add genie-agent <space-id> --name bakehouse_sales --auth user
agentbricks --profile <profile> deploy insights-agent  # ~3 min first time, ~30s after
```

The line to say out loud: **tool bindings are declarative.** Genie arrives as one command plus a
redeploy, with no change to the agent code. Only two files are hand-written — `agent/prompts.py`
(which source answers what) and `agent/mcps.py` (the review index).

`agentbricks init` already binds the Session Store, Memory Store and tracing experiment, so there is
nothing to wire for durability or observability.

## Act 2 — drive it (~8 min)

Open the printed URL and use the prompt buttons, or paste these in turn:

```text
1  Which 3 franchises had the highest total sales, and which products drove them? (Genie)
2  Search customer reviews for those franchises — what do customers dislike? (Search Vector Index)
3  Combine those into a short briefing with one recommendation.
4  Send that briefing to sales-ops Slack Channel
```

What to point at as it runs:

- **Turn 1 shows its SQL, and renders a chart.** Genie returns the generated query alongside the
rows; the chat draws a bar chart and a table from them. On the current data the answer is Baked
Bliss 6,642 · Sweet Sinsations 4,512 · The Crumbly Nook 2,790 — check it against the SQL on screen.
See **Charts and tables** for the queries that reliably produce one.
- **Turn 2 hits a different source entirely** and comes back with quoted review text, each passage
tied to a `franchiseID` from turn 1.
- **Turn 3 answers from session state** without re-querying either source. That is the bound Session
Store working, and the cheapest way to show durable conversation state.
- **Turn 4 pauses for approval.** Run it three times — approve, edit the recipient, reject — to show
all three decisions.
- **Traces**: the whole fan-out appears as one span tree in the bound experiment.
- **Memory**: start a new session, ask *"what did I ask you about last time?"*, and it recalls across
conversations because the actor is stable.

Asking turns 1 and 2 as a single question also works, and is the better one-shot demo: the agent
queries Genie once, then searches reviews once per top franchise, and cites both.

## Charts and tables

A Genie result is not shown as JSON. The chat renders it as a **table**, plus a **chart when the
data's shape earns one** — expand the `query_result · N rows, M columns` row to see it.

The chart appears when three conditions hold, and falls back to the table alone when they don't:

| Condition | Why |
| --- | --- |
| A numeric column | there is nothing to plot without a measure |
| A category **or** a date/timestamp column | category → horizontal bars, date → line |
| 2–25 rows | one bar is not a comparison; 30 bars is not readable |

Only the **first** numeric column is plotted — a second measure would mean a second y-axis, which
is never the right answer. Extra columns stay in the table.

**You never describe the data.** Ask in plain English; the chat reads the column types out of
Genie's own response schema (`type_name` on each column) and picks the form from that. There is no
type syntax, no hint, and nothing to annotate in the prompt.

### Queries that produce a chart

All three are verified against the current data.

```text
Show me total sales by franchise for the top 6 franchises.
Return just the franchise name and total sales.
```

→ **horizontal bar chart**, 6 bars. Baked Bliss 6,642 leads. Asking for "just the name and total" matters: a wider result still charts, but the table
gets noisy with id, city, district and country.

```text
Total sales by product.
```

→ 6 products → **bar chart**, and a good contrast with the franchise one because the same question
shape works on a different dimension.

```text
Show total sales per day as a time series.
Return the day and total sales only, and fetch the rows.
```

→ **line chart**, 18 points. Daily is the right grain here:
the dataset spans 2024-04-30 to 2024-05-17, so *monthly* collapses to 2 points and *weekly* to 3 —
both technically chart, neither reads as a trend.

### Queries that deliberately show only a table

```text
Which single franchise had the highest total sales?
```

→ one row, so no chart. A one-bar bar chart is not a comparison, and the number is the answer.

**"and fetch the rows" is doing real work in those prompts.** Genie's `ask` returns prose plus the
SQL but *not* the rows; the rows only arrive when the agent also calls `query_result`. Without that
nudge it sometimes answers from the prose alone and there is nothing to chart. If a chart does not
appear, that is the first thing to check.

## Verify the three sources

Tools resolve per request, so the honest check is to ask the agent what it has:

```bash
agentbricks --profile <profile> endpoint invoke agent-bricks-insights-agent \
  --path /api/invocations \
  --json "{\"id\":\"$(uuidgen)\",\"session_id\":\"$(uuidgen)\",\"input\":{\"messages\":[{\"role\":\"user\",\"content\":\"List the names of every tool you have available, one per line. Do not call any of them.\"}]}}"
```

All of these must appear:

| Expect | Source |
| --- | --- |
| `bakehouse_sales_ask` / `_poll` / `_query_result` | Genie |
| `jules_catalog__default__bakehouse_reviews_index` | Vector Search |
| `send_message`, `remember`, `recall`, `get_current_time` | Local + memory |

**If the reviews tool is missing, the agent will not tell you.** Servers from `build_mcp_servers()`
are best-effort — the runtime omits any that fail to connect or list tools — so the agent silently
answers from sales data alone. The usual cause is permissions: that server is built with a plain
`WorkspaceClient()`, which deployed is the **app's service principal**, not you. It needs
`USE_CATALOG` on the catalog, `USE_SCHEMA` on the schema, and `SELECT` on both the index and its
source table.

## Why deploy-only

Every binding is `auth = "user"` so Genie runs as the signed-in person, who already has access to
the data. The app's service principal would otherwise need grants on the Genie space and every table.

The cost of that choice, all of it app-wide once any binding is `user`:

- **No local run.** `agentbricks dev` has no request user, and a request-user discovery failure is
re-raised rather than skipped, so no graph builds at all.
- **No automatic crash recovery.** A recovery attempt raises `MCP_USER_AUTH_RECOVERY_UNSUPPORTED`;
an interrupted turn is marked failed rather than replayed.
- **No cross-replica takeover** of queued work — the credential lives in memory on the replica that
accepted the request.

Background invocations still work. Do not promise durability under per-user auth.

## Not wired yet

- **Slack.** `system.ai.slack` is deliberately unbound. Its OAuth connection is ACTIVE with
`chat:write` in scope, but a request-user discovery failure is **app-wide fatal** — a user who has
not authorized Slack could not invoke the agent at all, including the Genie turns. Bind it only
after signing in at **Unity Gateway → MCPs →** `system.ai.slack` **→ Manage provider sign-in**, and add
its write-tool name to `REQUIRE_APPROVAL` in `agent/agent.py` **in the same change**: this
framework gates approval by tool *name*, not by verb, so an unlisted write tool would post with no
pause at all.
- Until then `send_message` carries the approval demo. It logs and returns; it delivers nothing
