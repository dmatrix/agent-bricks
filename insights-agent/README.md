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
Which 3 franchises had the highest total sales, and which products drove them and draw chart for the appropriate numbers
```

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

## Use your own data

Nothing here is bakery-specific by design: the Genie space is a 32-hex ID in `agent.toml`, and the
index is three constants in `agent/mcps.py`

### Point Genie at your own tables

**Permissions are the easy half.** The binding is `auth = "user"`, so Genie runs as the signed-in
person: only the people asking questions need `SELECT` on your tables, and the app's service
principal needs nothing. The index in the next part is the opposite, which is why its grant list is
longer.

1. **Create a Genie space over your tables**, in the UI: *Genie → New*, attach a Pro or Serverless

   SQL warehouse, add the tables, and give it instructions. 
2. **Swap the binding.** Use the CLI rather than hand-editing `agent.toml`, so the manifest stays schema-valid
3. **Rewrite** `agent/prompts.py`**.** `INSIGHTS_PROMPT` is what makes the agent reach for the right source, 
4. **Edit the chat app's copy** at `ui/index.html:195-205` — the welcome paragraph and the`data-prompt` buttons are written about franchises.
5. **Redeploy.**

**What you do not need to touch:** the chart renderer. It is shape-based, not name-based — any tool
result shaped `{columns, rows}` becomes a table plus a chart when the conditions in **Charts and
tables** hold (`ui/app.js:627`), and the row is labelled from the tool's own name (`ui/app.js:965`).
Only the illustrative comment at `ui/app.js:541` names this demo's tool.

### Build an evidence index over your own text

A DELTA_SYNC index needs three things from its source table:

| Requirement | Why |
| --- | --- |
| A **unique single-column** primary key | the index keys rows by it |
| **Change Data Feed** enabled | the sync reads the feed to stay current |
| One text column to embed | the thing retrieval actually matches on |

This demo needed a derived table for the first of those, and the numbers are worth seeing, because
"mostly unique" is the case that looks fine until it is not:

```sql
SELECT count(*)                 AS rows,              -- 196
       count(DISTINCT chunk_id) AS distinct_chunk_id, -- 138  <- not a key
       count(DISTINCT md5(concat_ws('|', franchiseID, review_date, chunk_id)))
                                AS distinct_md5_key   -- 196  <- is a key
FROM samples.bakehouse.media_gold_reviews_chunked
```

1. **Derive a table if yours has no key.** Keep any column you want to filter or attribute by — this

   demo keeps `franchiseID` so a quoted review stays traceable to one franchise:

    If your table already has a key, just enable the feed:
    `ALTER TABLE <table> SET TBLPROPERTIES (delta.enableChangeDataFeed = true)`.
2. **Create a Vector Search endpoint**:
   ```bash
    databricks api post /api/2.0/vector-search/endpoints \
      --json '{"name": "<endpoint-name>", "endpoint_type": "STANDARD"}'
   ```

    It reports `"state": "ONLINE"` immediately, which is misleading: a brand-new endpoint cannot host
    an index for several minutes yet. Expect the next step to sit in `PROVISIONING_ENDPOINT` — it
    took about 8 minutes here before any row was indexed.
3. **Create the index**:
   ```bash
    databricks api post /api/2.0/vector-search/indexes --json '{
      "name": "<catalog>.<schema>.<index_name>",
      "endpoint_name": "<endpoint-name>",
      "primary_key": "chunk_pk",
      "index_type": "DELTA_SYNC",
      "delta_sync_index_spec": {
        "source_table": "<catalog>.<schema>.<chunks_table>",
        "pipeline_type": "TRIGGERED",
        "embedding_source_columns": [
          {"name": "<text_col>", "embedding_model_endpoint_name": "databricks-gte-large-en"}
        ]
      }
    }'
   ```
4. **Point the code at it** — three constants in `agent/mcps.py`:
   ```python
    _INDEX_CATALOG = "<catalog>"
    _INDEX_SCHEMA = "<schema>"
    _INDEX_NAME = "<index_name>"
   ```

    `REQUIRED_REVIEW_TOOL` is derived from those three, so the guard retargets itself. That cuts in
    your favour: get `_INDEX_NAME` wrong and the graph build fails naming the tool it expected,
    rather than quietly answering from Genie alone.
5. **Grant the app's service principal four privileges.** This server is built in code with a plain

   `WorkspaceClient()`, so deployed it runs as the app, not as you. Take the principal from the
    `service_principal_client_id` field:

    The index is a **separate securable** from its source table; granting one does not cover the
    other. `agentbricks deploy` makes none of these — its deploy record reports `"uc_grants": 0`,
    because only declared `[[tools]]` bindings get reconciled and this server is not one.
6. **Verify before redeploying.** `status.ready` should be `true` and `status.indexed_row_count`

   should match your table:

    Use the **2.1** effective-permissions path; `1.0` returns an empty body.
7. **Redeploy**, then update the index name and row count in **Vector Search — the opinions** above.

## Why deploy-only

The Genie binding is `auth = "user"` so Genie runs as the signed-in person, who already has access  
to the data. The app's service principal would otherwise need grants on the Genie space and every  
table. Vector Search is the exception: `agent/mcps.py` builds that server with a plain  
`WorkspaceClient()`, so deployed it runs as the app's service principal and needs its own grants.
