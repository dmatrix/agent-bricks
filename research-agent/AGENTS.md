# Agent Development Guide

This project is a LangGraph workload hosted by `databricks_agentkit.DurableAgentServer`. It uses
LangChain's **Deep Agents** harness (`deepagents.create_deep_agent`) rather than
`langchain.agents.create_agent`, porting Example 1 of
[11_deep_agents_langgraph.ipynb](https://github.com/dmatrix/mlflow-genai-tutorials/blob/main/11_deep_agents_langgraph.ipynb).
Both return a `CompiledStateGraph` and stream the same modes, so `run_agent`, `recovery_input` and
`_validate_approval_resume` are untouched by the swap.

Things to know before editing `agent/agent.py`:

- `create_deep_agent` installs planning, filesystem and sub-agent middleware **unconditionally**;
  you cannot omit them. `agent.toml` stays `framework = "langgraph"` because Deep Agents is
  LangGraph underneath.
- It also **injects a `general-purpose` subagent holding the main agent's whole tool list**, even
  when `subagents=None`. `_build_subagents` overrides that spec to withhold approval-gated tools.
  `tests/test_deep_agent.py` guards this; do not delete those tests.
- `agent.toml` declares one managed MCP service (`web_search`) with **`auth = "app"`**, so a deploy
  carries it and nothing is added by hand. `tests/conftest.py` stubs discovery to keep the suite
  offline; `tests/test_manifest.py` guards that the binding stays declared. See README,
  "Managed MCP tools come with the deploy".
- **`system.ai.slack` was bound here and was removed.** Built-in SaaS MCP services authorize per
  user over OAuth, which `auth = "app"` cannot complete, so discovery failed on every graph build
  and Slack tools never entered the tool list. The `auth = "user"` alternative is worse for this
  project: `mcp_tools` re-raises a discovery failure as `AuthError` for a `user` server instead of
  skipping it, and `agentbricks dev` has no request user, so one `user` binding stops the graph
  from building at all. Do not re-add it without reading REFERENCE.md,
  "Why `system.ai.slack` is not bound".
- **`agent/tools/stats.py` is the only code here that makes an outbound HTTP call.** It reads
  MLflow stars and monthly downloads from the GitHub and PyPI APIs and returns each figure with its
  source URL and read time. Verified working both under `agentbricks dev` and deployed — a
  Databricks App reaches both APIs with no egress configuration, and `httpx` honours `HTTPS_PROXY`
  automatically (`trust_env` defaults true). Two constraints: the body must catch its own lookup
  failures *and* keep `handle_tool_error` set, because `ToolNode`'s default handler re-raises
  anything that is not a `ToolInvocationError` and would end the turn; and the five-minute TTL
  caches successes only, carrying each reading's timestamp so a reused figure is never reported as
  fresher than it is.
- **Do not re-add a hardcoded statistic.** Latency and per-token pricing were removed as
  unverifiable, and three of the figures that were hardcoded turned out wrong (stars read "100K+"
  against an actual 28,694). `tests/test_stats.py` greps the module source for all seven removed
  values and fails if any returns as a constant. See README, "Statistics you can check".
- **The approval gate's subject is the local `agent/tools/send_message.py` stub**, gated
  unconditionally through `REQUIRE_APPROVAL`. It delivers nothing. Binding any managed MCP
  messaging service (`system.ai.slack`, `system.ai.gmail`, `system.ai.microsoft_365`) means
  deleting that module in the same change: its description out-competed the real Slack MCP tool
  last time, so the model picked the stub and reported sends that never happened.
  `tests/test_manifest.py:test_no_mcp_messaging_service_shadows_the_local_sender` fails if both
  exist.

For CLI workflows, read [.agents/skills/agent-bricks-workflow/SKILL.md](.agents/skills/agent-bricks-workflow/SKILL.md).
If it is absent, read the bundled skill at the path printed by the CLI's coding-agent hint.

Read [AGENTKIT_CONTRACT.md](AGENTKIT_CONTRACT.md) before changing integration points. It owns command
requirements, tool/state/tracing wiring, and recovery. [README.md](README.md) is the demo script and
owns setup; [REFERENCE.md](REFERENCE.md) owns the HTTP API, recovery semantics and architecture. Keep this file as a development map rather than repeating those rules.

## Commands

```bash
agentbricks dev
uv run pytest
agentbricks --profile <profile> deploy <name> --source .
```

### Deploying from a coding agent

A first deploy takes ~3 min (mostly starting app compute); redeploys are faster. Either run
`agentbricks deploy` in the foreground with a long command timeout (for example, Claude Code's Bash
`timeout: 600000`), or run `agentbricks deploy <name> --no-wait` and then
`agentbricks deployments wait agent-bricks-<name>` until it exits 0; the agent then answers
requests. Exit 75 means it is still deploying; run the same command again. Don't poll with `sleep`.

## Code map

| Change | File |
| --- | --- |
| Framework-native agent and `run_agent` | `agent/agent.py` |
| System prompt | `agent/prompts.py` |
| Subagent specs (incl. the `general-purpose` override) | `agent/agent.py:_build_subagents` |
| The seam hermetic tests monkeypatch | `agent/agent.py:_chat_model` |
| Knowledge base tool | `agent/tools/knowledge_base.py` |
| Live adoption figures (the only outbound HTTP in the project) | `agent/tools/stats.py` |
| The approval-gated message stub | `agent/tools/send_message.py`, `agent/agent.py:REQUIRE_APPROVAL` |
| Local tools | `agent/tools/` |
| MCP servers | `agent/mcps.py` |
| Managed runtime `invoke`/`recover` hooks and input/output translation | `runtime/adapter.py` |
| `DurableAgentServer` construction and hook registration | `runtime/main.py` |
| Browser and managed-state routes | `runtime/ui.py` |
| Browser behavior | `ui/app.js` |

Shared adapters come from `databricks_agentkit.langgraph` and `databricks_agentkit.runtime`. Consult
their docstrings for API details. Update the shared contract and relevant tests when integration
requirements change.
