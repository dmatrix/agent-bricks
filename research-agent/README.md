# research-agent — a Deep Agent that plans before it answers

A port of **Example 1** of the
[Deep Agents tutorial](https://github.com/dmatrix/mlflow-genai-tutorials/blob/main/11_deep_agents_langgraph.ipynb)
onto Agent Bricks, deployable as a Databricks App. Give it a research topic, MLflow or any other, and it does not answer
straight away: it writes a todo list as a plan, works the list, and only then synthesises a summary. The
origiinal tutorial's `openai:gpt-5-mini` is replaced by Databricks model serving through the Unity Gateway, so
there is no OpenAI key to manage.

**This file is the demo script.** Run Part 1 locally, then Part 2 on a workspace. If something looks
broken, check
[Things that look broken but are not](./REFERENCE.md) first — a
silent hang and a held port 8000 both have non-obvious causes. [REFERENCE.md](REFERENCE.md) also has
the HTTP API, recovery semantics and architecture; [study_guide.md](study_guide.md) is the code
reading path; [AGENTS.md](AGENTS.md) is the development map.

![A researcher at a desk types a research question into the research-assistant chat app, whose screen
shows a live plan with two steps checked off. The app, a Databricks App, works four steps in turn:
plan with write_todos, search the knowledge base, gather statistics, and synthesise the four-section
answer](images/research_assistant_scene.svg)

Its system prompt (`agent/prompts.py`, verbatim from the notebook) is the whole contract:

```text
You are a research assistant. When given a research topic:
1. Use write_todos to plan your research steps
2. Search the knowledge base for relevant information
3. Gather supporting statistics
4. Synthesize findings into a structured summary with sections:
   Overview, Key Findings, Statistics, and Conclusion
```

> **It showcases planning; it does not only do planning.** `create_deep_agent` installs three
> middlewares unconditionally — planning (`write_todos`), a virtual filesystem (`ls`, `read_file`,
> `write_file`, `edit_file`, `glob`, `grep`) and sub-agent delegation (`task`). There is no flag to
> omit any of them, so this agent has all three. Its *prompt, tools and tests* are built around
> planning. Sibling projects `fs-agent` and `multi-agent` showcase the other two.

## What it can research, and with what

Four local tools, plus whatever the harness adds:

| Tool | Does |
| --- | --- |
| `search_knowledge_base` | Looks up one of nine MLflow/GenAI topics in a literal dict |
| `get_mlflow_adoption` | Fetches MLflow stars and monthly downloads live, with their sources |
| `get_current_time` | The scaffold's sample tool |
| `send_message` | **Delivers nothing** — a stub that exists to be approval-gated, see below |

The knowledge base (`agent/tools/knowledge_base.py`) holds the notebook's four topics — MLflow
tracing, MLflow evaluation, Deep Agents, LangGraph — plus five summarised from the
[MLflow GenAI docs](https://mlflow.org/docs/latest/genai/): the **AI Gateway**, the **MCP
Registry**, **role-based access control**, **agent serving** and the **Prompt Registry**. Statistics
come from `agent/tools/stats.py`, which reproduces the tutorial's figures verbatim and does not
maintain them — point it at a real source before any of it informs a decision.

Both are literal dicts on purpose: `uv run pytest` builds the real graph, and a hermetic test suite
cannot reach a workspace. A Vector Search index or a Genie space would arrive as a tool through
`genie_tools()` or `mcp_tools()` without touching those modules.

### Who gets which tools

| Agent | Tools |
| --- | --- |
| main agent | all four above, the harness's planning/filesystem/`task` tools, and whatever the declared MCP services resolve to |
| `general-purpose` subagent | the same, **minus any approval-gated tool** |

`create_deep_agent` injects a `general-purpose` subagent even when you pass no `subagents`, and by
default hands it the main agent's entire tool list. `agent/agent.py:_build_subagents` overrides that
spec to drop approval-gated tools, because no subagent graph is compiled with a checkpointer — an
interrupt raised inside one can never be resumed, so a gated tool there would hang the turn rather
than gate it.

---

# Part 1 — Demo it locally

Everything here runs on your machine. `agentbricks dev` is a local deployment: it reads the same
`app.yaml` the Apps runtime would, so what you demo locally is what ships. It does not need a
workspace for its resources, and traces go to a local sqlite-backed MLflow server under
`.agentbricks/`.

```bash
uv sync
agentbricks dev          # serves on http://localhost:8000
```

Two things are *off* locally, by design: long-term memory, and durable conversation history. The
startup banner says so. Both are Part 2.

## Planning, end to end

Open `http://localhost:8000/` and click **Full research run**, or paste:

> Research how MLflow provides observability for AI agents. Cover tracing capabilities, evaluation
> features, and how it integrates with frameworks like LangGraph and Deep Agents. Include current
> adoption figures and cite your sources.

What to watch, in order:

1. Tokens start streaming immediately.
2. A **Tool request** row for `write_todos`.
3. A **Plan** row showing the checklist, which *updates in place* as the agent marks steps done.
4. **Tool request** and **Tool result** rows for `search_knowledge_base` and `get_mlflow_adoption`,

   collapsed — click one to see the arguments or the payload.
5. The four-section summary (Overview, Key Findings, Statistics, Conclusion) as the final message.

A full run takes about a minute and spends 5-6 `write_todos` calls, several knowledge-base searches
and one `get_mlflow_adoption` call. Asking it to cite sources, as above, is what gets the fetched
URLs into the answer rather than just the numbers. Each of those searches returns all seven
`mlflow *` topics rather than the two the prompt asks about, because the single word "MLflow" is
enough to match every one of them — see **Where the knowledge base breaks**.

The adoption figures are fetched from the GitHub and PyPI APIs on every call, so they are current
and checkable — and if a lookup fails the agent reports it as unverified instead of guessing. Try it
with the network off. Statistics on model latency and pricing used to live here as hardcoded
constants and were removed: see **Statistics you can check**.

## Session state across turns

The agent is conversational: state lives in the session, so each turn builds on the last. Type these
in turn by turn.

```text
1  What topics can you actually research? Check your knowledge base first.
2  Good. Now research MLflow tracing properly and give me just the Key Findings.
3  Add the current adoption figures to that, with sources.
4  Now rewrite the whole thing as a 5-bullet executive summary.
```

Turn 1 should answer *without* calling `write_todos` — the harness prompt tells it to skip planning
for simple requests, so a plan here would be over-eager. Turns 2-3 plan. Turn 4 is the one that
demonstrates session state: it rewrites from context rather than searching again.

History carries *within* a session and nothing carries across sessions, because no memory store is
active locally.

## The human-approval gate

The notebook has no equivalent for this; it is the scaffold's contribution.

```text
Send a one-line summary of MLflow's GenAI evaluation to the #mlflow-platform channel.
```

> `send_message` **delivers nothing.** It is a local stub (`agent/tools/send_message.py`) that logs
> its arguments and returns a string saying no message was sent. It exists so this flow is
> demonstrable with no managed MCP messaging service bound: `system.ai.slack` was declared here once
> and could never resolve — see
> [Why](REFERENCE.md#why-systemaislack-is-not-bound) `system.ai.slack` [is not bound](REFERENCE.md#why-systemaislack-is-not-bound). Point it at a real
> client (Slack's Web API, an SMTP relay, a webhook) before anyone relies on delivery.
>
> Two gates feed the same middleware. `agent.agent.REQUIRE_APPROVAL` names local tools literally, and
> gates `send_message` unconditionally — that is what makes this reachable under `agentbricks dev`.
> `agent.agent.approval_gate` additionally gates any *resolved MCP* tool with a write verb in its
> name (`send`, `post`, `write`, `reply`, `upload`, `create`, `update`, `delete`), so a gate arrives
> with such a tool rather than naming it up front.

The turn pauses, an approval panel opens, and it completes only when you decide. The gate accepts
three decisions, so run it three times:

| Decision | Expected |
| --- | --- |
| **Approve** | the tool runs and reports the message recorded (not delivered) |
| **Edit** | change the recipient or body first; the agent proceeds with *your* arguments |
| **Reject** | the agent acknowledges and does not call the tool |

Use the browser for this. Resuming over HTTP means hand-assembling a `resume` payload — see
[Approving a paused tool over HTTP](REFERENCE.md#approving-a-paused-tool-over-http).

> If your workspace's AI Gateway enforces a PII guardrail, a prompt containing an email address can
> be rejected before the model sees it (`This request was blocked by the 'pii' service policy`).
> That is the gateway, not the agent, and it fires before any tool is chosen.

## Files across turns (Recommended for local mode)

The filesystem tools are always installed, even though this project showcases planning:

```text
1  Write a short brief on Deep Agents to /brief.md, then tell me the filename only.
2  What files exist now?
3  Read /brief.md back to me.
4  Edit /brief.md to add a sentence about sub-agent delegation, then show me the result.
```

Watch the **Files** rows. Turn 2 should use `ls` and turn 4 `edit_file`. Locally the checkpointer is
in-process, so this proves the `files` channel round-trips across turns but *not* that it survives a
restart — that needs Part 2.

## Delegation, and the tool-scoping guarantee

```text
1  I need a thorough investigation of everything in your knowledge base about agent
   frameworks. Spawn a subagent to do the digging and report back.
2  Now have that subagent message the findings to the #mlflow-platform channel.
```

Turn 1 should produce a `task(...)` **Tool request** row, a pause while the subagent runs, then one
**Tool result** row. Turn 2 is a guarantee worth checking: the `general-purpose` subagent does not
have the gated `send_message`, so the main agent must make the call itself and still pause for
approval. A `send_message` call completing with no pause means subagent tool scoping has regressed —
see **Who gets which tools**.

## A second planning run

The same shape as the research prompt above, over the governance material instead. Useful for
showing the plan adapt to different content:

> Research how MLflow governs GenAI applications in production. Cover the AI Gateway, role-based
> access control, the MCP Registry, prompt versioning and agent serving. Include current adoption
> figures and cite your sources.

Expect fewer, fatter searches than the observability prompt gets: the word "MLflow" alone matches all
seven `mlflow *` topics, so one **Tool result** row often satisfies several todo steps.

## Where the knowledge base breaks

Matching is literal word-containment against the nine topic keys. It fails in two opposite
directions, which is worth knowing before a query surprises you mid-demo:

| Query | Matches |
| --- | --- |
| `mlflow` | all seven `mlflow *` topics at once, 833 words |
| `registry` | both registry topics, MCP *and* prompt |
| `model context protocol` | **nothing** — the key word is the acronym, "mcp" |
| `vector databases` | nothing |

Either one common word dumps every topic into the context, or a query misses a topic the dict
plainly covers. Both are the argument for pointing `search_knowledge_base` at Vector Search or a
Genie space instead of a dict — see `agent/tools/knowledge_base.py`, whose docstring also explains
why the keys are worded as they are.

## Statistics you can check

`get_mlflow_adoption` reads both of its figures from a named field of a public API and returns the
value, its source URL, the field name, and the UTC time it was read:

```text
MLflow adoption:
GitHub stars: 28,694 — read 2026-11-05T21:43:33+00:00 (source: https://api.github.com/repos/mlflow/mlflow, field stargazers_count)
PyPI downloads, last 30 days: 19,887,934 — read 2026-11-05T21:43:33+00:00 (source: https://pypistats.org/api/packages/mlflow/recent, field data.last_month)
```

Nothing is hardcoded, so there is no invented figure to catch you out, and a failed lookup is
reported as unverified rather than guessed — try it with the network off.

A reading is reused for five minutes. That is per process, successes only, and the **cached read time
travels with the value**, which is why the timestamp is printed per figure rather than once for the
call: a reused figure can never be presented as fresher than it is. The cache exists because both
APIs are unauthenticated and GitHub allows only 60 requests/hour **per IP** — deployed, every user of
the app shares one egress IP, and a single research run can call the tool more than once.

**Works identically locally and deployed.** Verified on both: a Databricks App can reach both APIs
with no egress configuration, and `httpx` honours `HTTPS_PROXY` / `NO_PROXY` automatically
(`trust_env` defaults to true), so it keeps working if a proxy is ever introduced.

**Two categories were removed rather than corrected.** The tool used to return hardcoded latency and
per-token pricing alongside adoption, copied from the tutorial notebook. When checked, GitHub stars
read "100K+" against an actual 28,694 and contributors "1000+" against the 448 GitHub's API tracks.
Latency can't be sourced at all — it varies by region, load, prompt length and streaming config, and
no canonical figure is published for any model. Pricing has no public, no-auth API and had drifted
two model generations. A number baked into source gives a reader no way to tell a current figure from
a stale one, which is exactly how the stars claim survived. `agent/tools/stats.py` documents this so
the next editor does not put them back.

## Reading the chat app

- **Plan row**: one per turn, rewritten in place. `write_todos` replaces the whole list on every
call, so appending would stack near-identical checklists and push the answer off-screen.
- **Tool rows are collapsed.** A request row's summary names the tool; a result row's adds the
payload size, so `search_knowledge_base · 5,837 characters` tells you the query fanned out before
you open it. Click to expand.
- **Events pane**: the same stream, unsummarized — every `todos`, `message`, `delta`, `interrupt`
and `files` event as raw JSON, newest first, with filter pills and a payload view. This is the only
place the plan's *revision history* survives (the Plan row overwrites itself) and the only place
you can read the literal `query` an agent passed to a tool.
- **Traces**: opens the local MLflow experiment, where `write_todos` and the tool spans appear
nested under the root `invoke` span.
- **Model picker** (in the composer): ask the same question on two different `system.ai.`* models
across consecutive turns. The agent is rebuilt per turn, so no restart is needed. The default is
`system.ai.gpt-5-5` unless a model service is bound.
- **Transport → Background**: run the long research prompt without holding the connection open.
- **Sessions card → new session**, then ask *"what did we just discuss?"* — it will not know.
Locally the session boundary is absolute.

## The same thing over HTTP

```bash
curl -s localhost:8000/api/invocations \
  -H 'Content-Type: application/json' \
  -d '{"id":"'"$(uuidgen)"'","session_id":"demo-1","input":{"messages":[
       {"role":"user","content":"Research how MLflow provides observability for AI agents."}]}}' \
  | jq -r '.output.output[-1].content'
```

Reuse `session_id` across turns for conversation state; generate a new `id` for each turn. The full
contract is in [The invocation API](REFERENCE.md#the-invocation-api).

---

# Part 2 — Deploy it to a Databricks workspace

Local first: `agentbricks dev` runs the same entrypoint as the deployed runtime against the same
model serving, so the demo you just ran is the demo you get deployed — **plus** the three things
local mode cannot do.

| | Local (`agentbricks dev`) | Deployed |
| --- | --- | --- |
| Conversation history | in-process, lost on restart | durable, in the bound Session Store |
| Long-term memory | off | on, keyed by `actor` |
| Invocation state and events | in memory | persistent Runtime Store, recoverable |
| Traces | local sqlite MLflow server | the bound workspace experiment |

## Bind the stores, then deploy

This project already has all three bound in `agent.toml`:

```toml
[memory_store]
name = "research-agent-oohnbh-memory"
[session_store]
name = "research-agent-oohnbh-sessions"
[tracing]
experiment_name = "/Shared/agentbricks_traces/research-agent-oohnbh"
```

If you are starting from a project without them, bind before the first deploy — that is what makes
the plan and the virtual filesystem survive a replica swap, since both live in LangGraph state
channels that are only as durable as the checkpointer behind them:

```bash
agentbricks sessions bind <session-store-name>
agentbricks memory bind <memory-store-name>
```

Then:

```bash
agentbricks --profile <profile> deploy       # no NAME needed; agent.toml records "research-agent"
```

Deploys as `agent-bricks-research-agent` and prints the app URL. Deployment provisions the declared
stores, reconciles app access, and ships the source. A first deploy takes about 3 minutes, mostly
starting app compute; redeploys are faster. Reusing the same name updates that app rather than
creating a second one.

Deploying from a coding agent needs a long command timeout or the `--no-wait` + `deployments wait`
pair; [AGENTS.md](AGENTS.md) owns that recipe.

## Managed MCP tools come with the deploy

`agent.toml` declares one managed MCP service, so a deploy carries it and there is nothing to
remember:

```toml
[[tools]]
id = "web_search"
auth = "app"
source = {kind = "mcp", service = "system.ai.web_search"}
```

`create_agent_graph` discovers each declared server's tools on every graph build, which means the
tool list is whatever the workspace can currently resolve. `system.ai.web_search` resolves, so its
tools are in the agent's tool list. Confirm with `agentbricks tools list --kind mcp` for what else
your workspace exposes, and add one with `agentbricks tools add mcp <service>`.

Verify what actually resolved from the log, not the chat app's Tools card: that card lists the
`[[tools]]` bindings from `agent.toml`, so it shows a service as connected whether or not its tools
resolved.

```bash
agentbricks -p <profile> deployments logs agent-bricks-research-agent | grep -i mcp
```

A line reading `Failed to fetch MCP tools from server '<id>'; continuing without it` means that
binding contributed nothing to the tool list.

## Proof of life

Open the printed URL and run the **Full research run** demo again — same chat app, same prompts. For
the API:

```bash
agentbricks --profile <profile> endpoint invoke agent-bricks-research-agent \
  --path /api/invocations \
  --json "{\"id\":\"$(uuidgen)\",\"session_id\":\"$(uuidgen)\",\"input\":{\"messages\":[{\"role\":\"user\",\"content\":\"What is the MLflow AI Gateway?\"}]}}"
```

A successful HTTP status with no useful answer is not proof of agent behaviour — read the content,
and check the invocation's trace in the bound experiment.

## Durable conversation history

The thing local mode cannot show. Use the *same* `session_id` for two turns with different
invocation IDs, and confirm the second turn uses the first turn's context:

```bash
SESSION=$(uuidgen)
agentbricks --profile <profile> endpoint invoke agent-bricks-research-agent --path /api/invocations \
  --json "{\"id\":\"$(uuidgen)\",\"session_id\":\"$SESSION\",\"input\":{\"messages\":[{\"role\":\"user\",\"content\":\"Research MLflow tracing. Keep it to three bullets.\"}]}}"

agentbricks --profile <profile> endpoint invoke agent-bricks-research-agent --path /api/invocations \
  --json "{\"id\":\"$(uuidgen)\",\"session_id\":\"$SESSION\",\"input\":{\"messages\":[{\"role\":\"user\",\"content\":\"Rewrite that as one sentence.\"}]}}"
```

With a Session Store bound, that also holds across an app restart, and a plan or a `/brief.md`
written in one turn is still there in the next.

## Long-term memory across conversations

The Session Store keeps one conversation, keyed by the top-level `session_id`. The Memory Store keeps
facts the agent recalls *across* conversations, keyed by `actor`: `runtime/adapter.py` reads
`input.actor` and passes it to `memory_tools(actor)`. Omit `actor` and it defaults to the
`session_id`, so nothing carries. Pass a stable `actor` from trusted application context — an end
user's ID — and it does.

```bash
ACTOR=user-123

# Conversation 1: the agent saves a fact with its memory tool.
agentbricks --profile <profile> endpoint invoke agent-bricks-research-agent --path /api/invocations \
  --json "{\"id\":\"$(uuidgen)\",\"session_id\":\"$(uuidgen)\",\"input\":{\"actor\":\"$ACTOR\",\"messages\":[{\"role\":\"user\",\"content\":\"Remember that I prefer answers as bullet points.\"}]}}"

# Conversation 2: a new session_id, same actor, so the agent can recall it.
agentbricks --profile <profile> endpoint invoke agent-bricks-research-agent --path /api/invocations \
  --json "{\"id\":\"$(uuidgen)\",\"session_id\":\"$(uuidgen)\",\"input\":{\"actor\":\"$ACTOR\",\"messages\":[{\"role\":\"user\",\"content\":\"How do I like my answers formatted?\"}]}}"
```

Different `session_id`s, same `actor`. Remove only test data you created.
