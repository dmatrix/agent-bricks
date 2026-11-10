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

**This agent runs deployed only.** `agentbricks dev` cannot build its graph: the Genie binding is
`auth = "user"`, and request-user auth needs a signed-in person, which only the deployed app behind
Apps ingress has. 

## The two data components

The demo's whole point is that these answer different questions and the agent has to choose.

### Genie — the sales numbers

A Genie space pinned to four **public** Unity Catalog tables: `samples.bakehouse.sales_transactions` (3,333 rows), `sales_customers` (300), `sales_franchises`(48) and `sales_suppliers`. Bound with one command:

```bash
agentbricks tools add genie-agent <32-hex-space-id> --name bakehouse_sales --auth user
```

What matters for a demo is that **Genie returns the SQL it generated and the rows it got**, so the audience can audit a
number instead of trusting it. 

### Vector Search — the opinions

A DELTA_SYNC index over customer reviews, retrieved by meaning rather than keyword:
`jules_catalog.default.bakehouse_reviews_index` — **196 chunks averaging 592 characters**, embedded
with `databricks-gte-large-en`.

It is served from the `jules-bakehouse-vs` endpoint, and wired in code rather than in `agent.toml`,
because the manifest has no vector-search kind — see `agent/mcps.py`. Unity Catalog exposes a
vector-search MCP server per schema at `/api/2.0/mcp/vector-search/{catalog}/{schema}`, so the index
arrives as an MCP tool and needs no retriever library: this project ships neither LangChain nor a
vector-search SDK.

Because that server is scoped to the *schema*, not the index, its presence proves nothing about the
index. `agent/agent.py` therefore asserts the index's tool actually resolved and fails the graph
build if it did not — see **If the reviews tool goes missing**.

## Demo Act 1 — build it live

Each step is one command. A binding only writes a `[[tools]]` entry into `agent.toml`, which is why
the agent code does not change between them — and the one capability with no manifest kind, Vector
Search, is exactly the one that needed hand-written code.

```bash
agentbricks init insights-agent --framework openai     # scaffold + chat app; binds stores & tracing
cd insights-agent
agentbricks tools add genie-agent <space-id> --name bakehouse_sales --auth user
agentbricks --profile <profile> deploy insights-agent  # ~3 min first time, ~30s after
```

The line to say out loud: **tool bindings are declarative.** Genie arrives as one command plus a
redeploy, with no change to the agent code. Beyond the scaffold, three files are hand-written or
edited, about 138 lines in total: `agent/mcps.py` (the review index, 68 lines),
`agent/prompts.py` (which source answers what, 38 lines), and `agent/agent.py` — three edited lines
for the prompt import, `instructions=` and `MODEL`, plus a 29-line guard that fails the build when
the review index does not resolve.

`agentbricks init` already binds the Session Store, Memory Store and tracing experiment, so there is
nothing to wire for durability or observability.

## Demo Act 2 — drive it 

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

A Genie result is not shown as JSON. The chat renders it as a **table**, plus a **chart when the**  
**data's shape earns one.** The chart appears when three conditions hold, and falls back to the table alone when they don't:

| Condition                                 | Why                                                 |
| ----------------------------------------- | --------------------------------------------------- |
| A numeric column                          | there is nothing to plot without a measure          |
| A category **or** a date/timestamp column | category → horizontal bars, date → line             |
| 2–25 rows                                 | one bar is not a comparison; 30 bars is not readabl |

### Queries that produce a chart

All three are verified against the current data.

```text
Show me total sales by franchise for the top 6 franchises.
Return just the franchise name and total sales and plot a chart
```

```text
Show total sales per day as a time series.
Return the day and total sales only, and fetch the rows.
```

### Queries that deliberately show only a table

```text
Which single franchise had the highest total sales?
```

## Why deploy-only

The Genie binding is `auth = "user"` so Genie runs as the signed-in person, who already has access
to the data. The app's service principal would otherwise need grants on the Genie space and every
table. Vector Search is the exception: `agent/mcps.py` builds that server with a plain
`WorkspaceClient()`, so deployed it runs as the app's service principal and needs its own grants.

## If the reviews tool goes missing

The symptom is an answer, not an error: the agent replies that it has no review-search tool, or
quietly answers from sales data alone. **Check whether the index still exists before you check
grants.** That ordering is the lesson from the one time this actually broke, on 2026-11-10 — all
three grants were in place and the index had been deleted along with the endpoint hosting it.

Why it was silent: the MCP server is *schema*-scoped, so it exists as long as the schema does. With
grants intact it connects and `list_tools()` **succeeds** — it just returns zero tools. Nothing is
logged, so `deployments logs | grep -i mcp` shows only build lines. `agent/agent.py` now raises on
this instead, naming the tool that failed to resolve and listing the ones that did, but the checks
below are still the way to find out why.

1. **Does the index exist?**

   ```bash
   databricks api get /api/2.0/vector-search/indexes/jules_catalog.default.bakehouse_reviews_index
   ```

   `Unity Catalog entity ... does not exist` means it is gone. Recreate it over
   `jules_catalog.default.bakehouse_review_chunks` — `DELTA_SYNC`, primary key `chunk_pk`, embeddings
   from `chunked_text` via `databricks-gte-large-en` — and check the endpoint still exists too, since
   deleting an endpoint takes its indexes with it.

2. **Is it ready and synced?** The same call reports `status.ready` and `status.indexed_row_count`;
   expect `true` and **196**. A newly created endpoint reports `ONLINE` well before it can host an
   index, so a fresh index sits in `PROVISIONING_ENDPOINT` for several minutes with no rows.

3. **Can the caller read it?** Deployed, that is the app's service principal, not you. It needs
   `USE_CATALOG` on the catalog, `USE_SCHEMA` on the schema, and `SELECT` on **both** the index and
   its source table — the index is a separate securable, so granting the table is not enough.

   ```bash
   databricks apps get agent-bricks-insights-agent   # service_principal_client_id
   databricks grants get TABLE jules_catalog.default.bakehouse_reviews_index
   databricks grants get TABLE jules_catalog.default.bakehouse_review_chunks
   ```

   `agentbricks deploy` does not make these grants. Its deploy record shows `"uc_grants": 0`,
   because only declared `[[tools]]` bindings get reconciled and this server is not one.
