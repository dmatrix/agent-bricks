# research-agent study guide

A reading path for understanding how this Agent Bricks app works: the concepts, the request
lifecycle, and the files to study in order.

Companion docs: [AGENTS.md](AGENTS.md) is the development map, [AGENTKIT_CONTRACT.md](AGENTKIT_CONTRACT.md)
owns the integration requirements, [README.md](README.md) is the demo script, and
[REFERENCE.md](REFERENCE.md) holds the HTTP API, recovery semantics and architecture.

## Current state

- **Tools**: four local — `search_knowledge_base`, `get_mlflow_adoption`, `get_current_time`,
  `send_message` — plus the planning / filesystem / `task` tools the harness installs.
- **MCP**: one binding, `system.ai.web_search`, `auth = "app"`. `system.ai.slack` was bound here and
  was removed; see the confusing-things bullet below for why.
- **`send_message` has no backend.** It logs its arguments and returns a string saying nothing was
  delivered. It exists to be approval-gated. Picking a real backend is an open decision.
- **`get_mlflow_adoption` is the only outbound HTTP call in the project.** It fetches MLflow stars
  and monthly downloads live, returning each with its source URL and read time, and reports a failed
  lookup as unverified rather than guessing. Works identically locally and deployed. The hardcoded
  latency and pricing figures it replaced were removed as unverifiable.
- **Suite**: `uv run pytest` → 73 passed, 1 skipped. The skip is the live model test, which needs a
  workspace profile.

## What this thing actually is

Three stacked layers, each with a clear owner:

```text
HTTP client (browser chat app / curl)
      │  POST /api/invocations  {id, session_id, input:{messages, actor, model}}
      ▼
runtime/main.py        DurableAgentServer — the managed HTTP protocol, durability, replay
      ▼
runtime/adapter.py     translation: invocation payload ⇄ LangGraph input/events
      ▼
agent/agent.py         run_agent → a Deep Agent (LangGraph CompiledStateGraph)
      ▼
tools: agent/tools/*  +  memory_tools  +  genie_tools  +  MCP tools (agent.toml)
model: system.ai.gpt-5-5 via the Unity Catalog AI Gateway
```

The deliberate design rule: **`agent/agent.py` knows nothing about Agent Bricks request types**.
You could call `run_agent` from a notebook or a test. Everything runtime-specific lives in
`runtime/`. That seam is what makes the project portable, and it is the single most important thing
to internalize.

## The concepts, in dependency order

### 1. Deep Agents, not a hand-built graph

`create_deep_agent` (`agent/agent.py:234`) is `langchain.agents.create_agent` plus three
middlewares it installs *unconditionally*:

- **planning** → the `write_todos` tool, plus a `todos` state channel
- **filesystem** → `ls`/`read_file`/`write_file`/`edit_file`/`glob`/`grep`, plus a `files` state channel
- **subagents** → the `task` tool for delegation

You cannot opt out of any of them. This project showcases planning (see the prompt), but the other
two tool sets are present regardless.

### 2. The graph is rebuilt every single turn

This is the counterintuitive part. `create_agent_graph` is called per request because memory tools
close over the actor and MCP tools close over that request's credentials. Nothing credential-bound
may be cached across requests or survive an approval pause — only *graph state* persists, via the
checkpointer.

### 3. Managed state = two different stores

Easy to conflate:

- **Session Store** (`checkpointer()`) → LangGraph checkpoints: conversation history, the plan, the
  virtual filesystem, paused interrupts. Keyed by `thread_config(session_id, actor)`.
- **Runtime Store** → invocations and emitted events, for durability / replay / recovery.

A durable Runtime Store does *not* preserve graph state. That is why `backend=StateBackend`
(`agent/agent.py:243`) matters — files live in LangGraph state so they ride the Session Store,
rather than in one replica's container where a redeploy would eat them.

### 4. `session_id` vs `actor` vs `invocation_id`

Three identities, all distinct:

- `invocation_id` — one UUID per turn
- `session_id` — stable per conversation; partitions the checkpointer
- `actor` — the identity whose long-term memory is read/written; defaults to `session_id`, so
  cross-conversation memory only works when a client sends a stable `actor`

`X-Routing-Key` is sticky routing only and is never the session.

### 5. Human-in-the-loop, two gates

`REQUIRE_APPROVAL` (`agent/agent.py:65`) names local tools literally and gates `send_message`
unconditionally — a local tool exists at import time, so it can be named. That is what makes the
approve / edit / reject flow reachable under `agentbricks dev`, where no MCP write tool resolves.

The other half, `GATED_MCP_VERBS` (`agent/agent.py:75`), is the clever one. Managed MCP tool names
are not known until the service resolves at graph-build time, so the gate cannot name tools
literally — it matches on the verb. `send`/`post`/`write`/`delete` → gated;
`web_search`/`list_channels` → unattended.

An interrupt *ends the invocation* and checkpoints. The client resumes with `input.resume` in a
brand-new invocation (new UUID, same `session_id` and `input.actor`).

### 6. The subagent tool-scoping trap

`_build_subagents` (`agent/agent.py:156`) looks like dead weight and is not. `create_deep_agent`
injects a `general-purpose` subagent holding the main agent's *entire* tool list, approval-gated
tools included — even when you pass `subagents=None`. And no subagent graph gets a checkpointer, so
an interrupt raised inside one **can never be resumed — it hangs**. Overriding the spec under the
same name is the only way to withhold gated tools.

`tests/test_deep_agent.py` guards this; do not delete those tests.

## Request lifecycle, concretely

1. Client POSTs an invocation. `DurableAgentServer` records it, calls your `invoke` hook.
2. `adapter.invoke` → `_payload` → `_agent_input`: a `resume` key becomes `Command(resume=...)`,
   otherwise `{"messages": [...]}`.
3. `_invoke_agent` resolves session/actor, namespaces the actor under request-user auth, then calls
   `run_agent`.
4. `create_agent_graph` awaits MCP discovery, assembles tools, derives the approval gate, compiles
   the graph.
5. `_validate_approval_resume` rejects malformed HITL decisions *before* LangGraph checkpoints them.
6. `graph.astream(stream_mode=["updates","messages"], durability="sync")` inside `start_trace`.
7. `_serialize_events` maps native events to a wire protocol: `message`, `delta`, `interrupt`,
   `todos`, `files`.
8. Each is `await context.emit(...)`'d for live streaming; the accumulated list becomes the JSON
   return with `status: completed|interrupted`.

On worker loss, `recover` → `recovery_input` checks whether a checkpoint is tagged with *this*
invocation. Tagged → pass `None` and continue; untagged → replay the original input.
`checkpointed_messages` restores already-committed outputs without re-emitting them. Recovery is
at-least-once, so side effects must tolerate replay.

## Study order

| # | File | Lines | Why |
| --- | --- | --- | --- |
| 1 | `agent.toml` | 21 | The manifest: framework, stores, tracing, MCP bindings. Everything else resolves from here. |
| 2 | `agent/prompts.py` | 25 | The 4-step prompt that drives the whole demo. Tiny, read it first. |
| 3 | `agent/tools/knowledge_base.py`, `stats.py` | ~184 | What a `@tool` is. Substring keyword matching standing in for a vector index. |
| 4 | `agent/tools/__init__.py` | 24 | Auto-discovery: drop a `@tool` module in, zero wiring. |
| 5 | `agent/tools/send_message.py` | 37 | The approval gate's subject. Read with `agent/agent.py:65` to see why a local tool can be named literally where an MCP tool cannot. |
| 6 | `agent/tools/stats.py` + `tests/test_stats.py` | 122 + 167 | The only outbound HTTP call, and the only tests that invoke a tool body. Read together: the tests are where the failure-path contract is written down. |
| 7 | **`agent/agent.py`** | 339 | **The heart.** `create_agent_graph`, `approval_gate`, `_build_subagents`, `run_agent`, `recovery_input`. |
| 8 | `runtime/adapter.py` | 158 | The translation layer. `_serialize_events` is where you learn the event protocol. |
| 9 | `runtime/main.py` | 26 | Trivial but shows the wiring order: `load_dotenv` → `configure()` → server → hooks. |
| 10 | `tests/test_deep_agent.py` | — | Structural assertions on graph *shape*. The best executable spec in the repo. |
| 11 | `tests/conftest.py` | — | Why the suite is hermetic: the two monkeypatch seams. |
| 12 | `runtime/ui.py`, `ui/app.js` | — | Optional. Only if you care about the browser chat app. |

Read these alongside: `AGENTKIT_CONTRACT.md` (read before changing any integration point),
`REFERENCE.md` sections "How it fits together" and "Things that look broken but are not", and
`AGENTS.md` as the development map.

## Things that will confuse you

- **`agent/mcps.py` is empty and that is correct.** MCP servers come from `agent.toml` bindings, not
  code. `mcps.py` is the escape hatch for extra servers.
- **`send_message` reports success but delivers nothing.** It is a stub
  (`agent/tools/send_message.py`) whose only purpose is to be approval-gated. `system.ai.slack` was
  bound here once and never resolved — built-in SaaS MCP services need a per-user OAuth sign-in that
  `auth = "app"` cannot complete, and `auth = "user"` makes discovery failure a fatal `AuthError`,
  so **no graph builds at all** without a request user. See REFERENCE.md, "Why `system.ai.slack` is
  not bound".
- **The plan rewrites itself mid-turn.** `write_todos` replaces the whole list on every call. Not a
  loop.
- **`execute` shows up in the tool registry.** It arrives with `FilesystemMiddleware`, which filters
  it out per call for non-sandbox backends. It is not callable here. You only notice this by
  enumerating `graph.nodes["tools"].bound.tools_by_name`, which is exactly what
  `tests/test_deep_agent.py` does.

## One correctness detail worth noticing

`adapter.py:_channel` (line 117) exists because Deep Agents' `PatchToolCallsMiddleware` returns
`{"messages": Overwrite(patched)}` on every turn, and iterating an `Overwrite` raises `TypeError`.
Every state-channel read goes through that helper. It is a good example of the kind of friction the
Deep Agents swap introduced — worth reading if you plan to add state channels of your own.

## See it live

```bash
agentbricks dev
uv run pytest
```

Ask it to research MLflow tracing, and watch the plan populate then collapse as the agent works
through `search_knowledge_base` → `get_mlflow_adoption` → synthesis.
