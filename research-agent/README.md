# research-agent — a Deep Agent that plans before it answers

A port of **Example 1** of the
[Deep Agents tutorial](https://github.com/dmatrix/mlflow-genai-tutorials/blob/main/11_deep_agents_langgraph.ipynb) onto Agent Bricks, deployable as a Databricks App. Give it a research topic, MLflow or any other, and it does not answer straight away: it writes a todo list as a plan, works the list, and only then synthesizes a summary. The
original tutorial's `openai:gpt-5-mini` is replaced by Databricks model serving through the Unity Gateway, so
there is no OpenAI key to manage.

**This file is the demo script.** Run Part 1 locally, then Part 2 on a workspace. [study_guide.md](study_guide.md) is the code
reading path; [AGENTS.md](AGENTS.md) is the development map.

![A researcher at a desk types a research question into the research-assistant chat app, whose screen
shows a live plan with two steps checked off. The app, a Databricks App, works four steps in turn:
plan with write_todos, search the knowledge base, gather statistics, and synthesize the four-section
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
tracing, MLflow evaluation, Deep Agents, LangGraph — plus five summarized from the
[MLflow GenAI docs](https://mlflow.org/docs/latest/genai/): the **AI Gateway**, the **MCP Registry**, **role-based access control**, **agent serving** and the **Prompt Registry**. Statistics come from the `get_mlflow_adoption` tool (`agent/tools/stats.py`), which reads each figure from its
primary source — the GitHub and PyPI APIs — at call time and returns it with the source URL and the UTC
time of the read. Nothing is hardcoded: the tutorial's copied figures were removed because three of them
were wrong.

---

# Part 1 — Demo it locally

Everything here runs on your machine. `agentbricks dev` is a local deployment: it reads the same
`app.yaml` the Agent Bricks runtime would, so what you demo locally is what ships. It does not need a
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

A full run takes about a minute and makes 5-6 `write_todos` calls, several knowledge-base searches
and one `get_mlflow_adoption` call. Asking it to cite sources, as above, is what gets the fetched
URLs into the answer rather than just the numbers. Each of those searches returns all seven
`mlflow *` topics rather than the two the prompt asks about, because the single word "MLflow" is
enough to match every one of them.

The adoption figures are fetched from the GitHub and PyPI APIs on every call, so they are current
and checkable, and if a lookup fails the agent reports it as unverified instead of guessing.

## Session state across turns

The agent is conversational: state lives in the session, so each turn builds on the last. Type these
in, turn by turn.

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
> and could never resolve.

The turn pauses, an approval panel opens, and it completes only when you decide. The gate accepts
three decisions, so run it three times:

| Decision | Expected |
| --- | --- |
| **Approve** | the tool runs and reports the message recorded (not delivered) |
| **Edit** | change the recipient or body first; the agent proceeds with *your* arguments |
| **Reject** | the agent acknowledges and does not call the tool |

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
showing how the plan adapts to different content:

> Research how MLflow governs GenAI applications in production. Cover the AI Gateway, role-based
> access control, the MCP Registry, prompt versioning and agent serving. Include current adoption
> figures and cite your sources.

Expect fewer, fatter searches than the observability prompt gets: the word "MLflow" alone matches all
seven `mlflow *` topics, so one **Tool result** row often satisfies several todo steps.

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
