# research-agent reference

The material a demo does not need. [README.md](README.md) is the demo script and owns setup;
[study_guide.md](study_guide.md) is the code reading path; [AGENTKIT_CONTRACT.md](AGENTKIT_CONTRACT.md)
owns the managed-runtime integration requirements.

This file holds the HTTP contract, the deploy-time behaviours you only hit in production, and the
one diagnosis worth not repeating.

- [The invocation API](#the-invocation-api) — request shape, idempotency, the four modes, HTTP
  approval, request-user auth
- [Crash recovery and routing](#crash-recovery-and-routing)
- [Why `system.ai.slack` is not bound](#why-systemaislack-is-not-bound)
- [Things that look broken but are not](#things-that-look-broken-but-are-not) — demo-time and
  configuration
- [How it fits together](#how-it-fits-together) — architecture and where to change what

---

## The invocation API

The API is at `http://localhost:8000/api/invocations` locally (or the port you pass with
`--app-port`), and at `<app-url>/api/invocations` deployed. Requests can fail for a few seconds while
the server starts; wait for `Uvicorn running on ...`.

Every request supplies a UUID `id` — the invocation identifier and idempotency key. The stable
application session is the top-level `session_id`; agent-specific values live inside the opaque
`input` object:

```bash
SESSION_ID=$(uuidgen)
INVOCATION_ID=$(uuidgen)

curl -sS http://localhost:8000/api/invocations \
  -H 'Content-Type: application/json' \
  -d "{\"id\":\"$INVOCATION_ID\",\"session_id\":\"$SESSION_ID\",\"input\":{\"messages\":[{\"role\":\"user\",\"content\":\"What time is it? Use your tool.\"}]}}"
```

Reuse `SESSION_ID` for multi-turn conversation state. Generate a new `INVOCATION_ID` for each turn.
Retrying the same request with the same invocation ID returns the persisted result; changing the
request while reusing the ID returns `409`.

### Invocation modes

- Foreground: omit `background` and `stream`; the response carries the agent result under `output`.
- Foreground streaming: set `stream: true`; the response is SSE backed by persisted events.
- Background: set `background: true`; poll the returned `status_url`.
- Background streaming: set both; the `202` response includes `status_url` and `events_url`.

```bash
INVOCATION_ID=$(uuidgen)
curl -sN http://localhost:8000/api/invocations \
  -H 'Content-Type: application/json' \
  -d "{\"id\":\"$INVOCATION_ID\",\"session_id\":\"$SESSION_ID\",\"input\":{\"messages\":[{\"role\":\"user\",\"content\":\"Count to three.\"}]},\"stream\":true}"

INVOCATION_ID=$(uuidgen)
curl -sS http://localhost:8000/api/invocations \
  -H 'Content-Type: application/json' \
  -d "{\"id\":\"$INVOCATION_ID\",\"session_id\":\"$SESSION_ID\",\"input\":{\"messages\":[{\"role\":\"user\",\"content\":\"Summarize durable agents.\"}]},\"background\":true}" | jq
curl -sS "http://localhost:8000/api/invocations/$INVOCATION_ID" | jq
```

SSE records are the events translated by `runtime/adapter.py` — token `delta`s, completed
`message`s, `todos`, `files` and HITL `interrupt`s. Replay from a cursor with
`GET /api/invocations/{id}/events?after={sequence}`.

### Approving a paused tool over HTTP

`HumanInTheLoopMiddleware` gates two things (see **The human-approval gate**): local tools named
literally in `agent.agent.REQUIRE_APPROVAL`, and any resolved MCP tool whose name carries a write
verb (`agent.agent.GATED_MCP_VERBS`). With only `web_search` bound, `send_message` is the one you can
trigger — ask the agent to message someone. When the output or event stream contains an `interrupt`,
submit a *new* invocation with the same application session:

```json
{
  "id": "<new-uuid>",
  "session_id": "<same-session-id>",
  "input": {
    "resume": {"decisions": [{"type": "approve"}]}
  }
}
```

Malformed decisions return `INVALID_APPROVAL` before the resume value is checkpointed, so the user
can correct and resubmit with a new invocation ID. The default checkpointer is process-local; a bound
Session Store is what preserves paused LangGraph state across restarts.

For OBO tools (`auth = "user"`), the same signed-in user submits the approval as a new invocation.
Keep the same `session_id` and, if supplied, `input.actor`. The new request supplies its own user
credentials; the paused invocation's are discarded. The browser normally reuses its authenticated
session, so approval needs no refresh or re-login unless that session expired. Automatic crash
recovery still cannot recover OBO work.

### Request-user authorization

Declared tools in `agent.toml` select `auth = "user"` or `auth = "app"`; legacy missing auth
continues to use the application/default identity. Deployed user tools require the request resolver
and never fall back to application credentials. Model, memory-service, session-service, and custom
MCP server credentials are unchanged.

`runtime/main.py` derives the invocation policy after `configure()`. The runtime adapter namespaces
public session IDs and actor values for the request owner, then passes only `workspace_client_for`
to the framework-native agent. Internal session keys are never returned to clients.

User-policy invocations use the existing Runtime for synchronous, streaming, and background calls,
including status polling, event replay, and invocation-ID idempotency. The Runtime Store persists no
credential; request-user authentication remains process-local for the active first attempt. A
replacement attempt fails with `MCP_USER_AUTH_RECOVERY_UNSUPPORTED` before agent code runs.

---

## Crash recovery and routing

Deployment with a Runtime Store supports invocation recovery after worker loss; graph checkpoint
persistence requires the Session Store separately. Recovery can replay work, so tools must tolerate
repeated side effects. See [Recovery and durability](AGENTKIT_CONTRACT.md#recovery-and-durability).

To keep a session on one app replica, set the `X-Routing-Key` header to your stable session id on
every call. It is a routing hint only: non-blank, at most 128 UTF-8 bytes, used verbatim, and it
takes precedence over the legacy `__Host-databricks-app-router` cookie. It is not authentication and
is never the application's session id — send that as the top-level `session_id` in the body.

When deployment provisions a dedicated Runtime Store, only the app-owned
`databricks_agentkit_runtime_<hash>` schema and runtime tables are added. Managed Runtime Store
deployments use their own default schema.

---

## Why `system.ai.slack` is not bound

It was, and it never worked. Built-in SaaS MCP services authorize
[per user over OAuth](https://docs.databricks.com/aws/en/agents/mcp-tools/built-in-mcp-services):
the caller signs in to Slack once, and the service then acts as that Slack account. Under
`auth = "app"` the caller is the app's service principal, which cannot complete a Slack consent
screen, so discovery returned 401 on every graph build and Slack tools never entered the tool list.
The agent answered every "post this to Slack" request by explaining that it could not.

Making it resolve takes all of:

1. An admin grants the caller the **Consumer access** entitlement and permission to call the MCP
   service (Unity Gateway grants — see
   [Govern an MCP service](https://docs.databricks.com/aws/en/ai-gateway/govern-mcp-service)).
2. The binding set to `auth = "user"`. The sign-in is tied to a person's Slack account, so a service
   principal cannot stand in. `runtime/tool_manifest.py` accepts only the literal `"user"` or
   `"app"` — there is no per-environment override.
3. The Slack provider sign-in completed: the first call returns a login link, or sign in ahead of
   time via **Unity Gateway → MCPs → `system.ai.slack` → Manage provider sign-in**. The service URL
   is `https://<workspace-host>/ai-gateway/mcp-services/system.ai.slack`.
4. A redeploy, then testing in the browser — the deployed chat app's login session is what supplies
   the request user.

**The cost of step 2, and why this project gave up on it:** `auth = "user"` makes a discovery
failure fatal rather than skipped, and `agentbricks dev` has no request user — so the graph cannot
be built locally at all and *every* local turn dies, not just Slack ones. Slack would only ever work
in the deployed app. The local `send_message` stub carries the approval-gate demo instead, at the
cost of not actually sending anything.

If you do re-add a messaging service, **delete `agent/tools/send_message.py` in the same change.**
Its description out-competed the real Slack MCP tool last time, so the model picked the stub for
every send and reported deliveries that never happened.
`tests/test_manifest.py:test_no_mcp_messaging_service_shadows_the_local_sender` fails if both exist.

No Unity Catalog connection is involved. A `SLACK` connection type does not exist in the workspace's
connection list, and creating one is not what this needs.

**`auth = "app"` is deliberate** for what remains. With `auth = "user"`,
`databricks_agentkit.langgraph.mcp` re-raises a discovery failure as `AuthError` rather than skipping
the server, so the graph cannot be built at all. App auth degrades to a warning instead. The trade is
identity — these tools act as the app's service principal, not as the signed-in user.

Tests stay offline because `tests/conftest.py` stubs the discovery call, and
`tests/test_manifest.py` asserts the binding is still declared so the stub cannot hide its removal.

---

## Things that look broken but are not

None of these is a fault. The first five happen during an ordinary local demo; the last three need a
workspace or a hand-edited binding before they can fire at all.

- **Turns hang and the log fills with `Retrying (Retry(total=...))` against a local port.** The local
  tracking server is not up, so every trace export retries with backoff. `agentbricks dev` starts
  that server itself by running `uvx --from 'mlflow>=3.10,<4' mlflow server`, which has to resolve
  mlflow against a package index first; if the index is unreachable, resolution fails after ~126s
  and nothing ever listens. Check `.agentbricks/mlflow-server.log` for an `error:` line. `dev` does
  not health-check it, so the "Agent is running" banner prints a Traces URL either way. If the
  version is already in uv's cache, `UV_OFFLINE=1 agentbricks dev` skips the network entirely.
- **`agentbricks dev` leaves processes behind.** SIGTERM to the `agentbricks dev` parent can orphan
  its `apps run-local` and `start-server` children, which keep holding port 8000. Check with
  `pgrep -fl 'apps run-local'` before restarting.
- **The plan appears, then changes.** `write_todos` replaces the whole list on every call, and the
  harness prompt tells the agent to mark each step complete as it finishes rather than batching. So
  the Plan row rewrites itself several times per turn. That is the agent working, not a loop.
- **The agent sometimes skips `write_todos`.** The harness prompt tells it to use the tool for
  complex multi-step work and skip it for simple requests. A one-line factual question answered
  directly is correct behaviour.
- **The agent says a message was recorded but nothing arrives.** Correct. `send_message` is a stub
  with no messaging backend — see [The human-approval gate](README.md#the-human-approval-gate).

- **A prompt is refused with `blocked by the 'pii' service policy`.** That is your workspace's AI
  Gateway guardrail, upstream of this agent — every model call routes through the gateway
  (`agent/agent.py:_chat_model` sets `use_ai_gateway=True`). Nothing in this project configures it.
  Keep email addresses out of prompts, or have the policy scoped.
- **`Failed to fetch MCP tools from server '<id>'; continuing without it` in the log.** That binding
  resolved nothing and the agent runs without its tools. Under `auth = "app"` this is a warning by
  design. See [Why `system.ai.slack` is not bound](#why-systemaislack-is-not-bound).
- **`AuthError: Could not discover configured MCP tools` and no graph at all.** A binding was
  switched to `auth = "user"`. Discovery failures become fatal in that mode, and neither the tests
  nor `agentbricks dev` has a request user.

---

## How it fits together

A LangGraph agent served by `databricks_agentkit.DurableAgentServer`. The generated project
separates portable agent execution from the managed HTTP protocol:

```text
client -> runtime/main.py -> runtime/adapter.py -> agent/agent.py:run_agent
```

Drawn out, with the agent's internals, its tools and the return event stream:

![research-agent architecture: clients post an invocation to the Agent Bricks runtime, which drives a
Deep Agent with planning, filesystem and subagent middleware over local tools, managed MCP services
and a gateway-routed model, streaming events back and pausing for human
approval](images/research_agent_architecture.svg)

- `agent/agent.py` owns the framework-native agent, sessions, tools, MCP lifetime, and `run_agent`.
- `runtime/adapter.py` owns the agent-author integration hooks: runtime input/output translation plus
  `invoke` and `recover`.
- `runtime/main.py` constructs the server and registers those hooks.
- `ui/` is the browser chat app, included by default. It generates a stable application session ID in
  local storage, sends it as the top-level `session_id`, and a fresh invocation UUID per turn. Use
  `agentbricks init --framework langgraph --disable-chat-app` for API-only output.

Changing things:

- Model, instructions, tools, graph, framework-native execution → `agent/agent.py`.
- A different application input/output contract → `runtime/adapter.py`, and only that.
- New local tools → drop a module in `agent/tools/`; they are auto-discovered.
- What needs human approval → `agent/agent.py:REQUIRE_APPROVAL` for local tools, named literally, and
  `GATED_MCP_VERBS` for resolved MCP tools, matched on the verb in the tool's name.
- MCP servers → `agent/mcps.py`, or `agentbricks tools add mcp`.
- Route the model through a Unity Gateway model service you own with
  `agentbricks experimental models bind`, then search for a better model and prompt on your own eval
  set with `agentbricks experimental models upgrade`. Experimental: flags may change between
  releases.

To bring an existing LangGraph agent, run `agentbricks init --framework langgraph --existing .` in
its project and follow the generated prompt in your coding agent. The migration skill and this
template share [AGENTKIT_CONTRACT.md](AGENTKIT_CONTRACT.md), which owns integration requirements.
This README owns setup and client examples; [AGENTS.md](AGENTS.md) is the development map.

```bash
uv run pytest            # hermetic: builds the real graph, stubbing only _chat_model and mcp_tools
```
