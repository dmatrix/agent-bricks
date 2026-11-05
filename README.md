# Databricks Agent Development Platform

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Framework](https://img.shields.io/badge/LangGraph-Deep%20Agents-ff6f61.svg)
![Deploy](https://img.shields.io/badge/deploy-Databricks%20Apps-ff5f46.svg)
![Tracing](https://img.shields.io/badge/observability-MLflow-0194e2.svg)

![Agent Bricks Platform](images/agent_bricks.svg)



Custom agents built on [Agent Bricks](https://github.com/databricks/databricks-ai-bridge/tree/main/integrations/agentbricks)

Agent Bricks is the Databricks agent developer platform. Use it to build and deploy the agents that power your agentic products and workflows. Agent Bricks is built for developers who write code. You bring an agent built with any framework or harness and choose any model. They can be deployed either locally or as as Databricks Apps. 

One directory per agent, each self-contained: its own `pyproject.toml`, its own `agent.toml`, its own docs, deployable on its own.

## Projects

| Project | Showcases | Tags | Start here |
| --- | --- | --- | --- |
| [research-agent](research-agent/) | **Planning** — writes a todo list, works the list, then synthesises | `langgraph` `deep-agents` `planning` `human-in-the-loop` `mcp` `memory` `sessions` `mlflow-tracing` | [README](research-agent/README.md) · [study guide](research-agent/study_guide.md) |

More are coming. `research-agent/README.md` already names two of them: `fs-agent` and `multi-agent`,
which showcase the other two middlewares `create_deep_agent` installs — the virtual filesystem and
sub-agent delegation. Neither is in this repo yet.

### Tag vocabulary

Reuse these words rather than inventing synonyms, so the table stays scannable as projects arrive.
A tag means the capability is **wired and demoable in that project**, not merely importable.

| Group | Tags |
| --- | --- |
| Framework | `langgraph`, `deep-agents`, `openai` — the two frameworks `agentbricks init` supports, plus the harness on top |
| Agent capability | `planning`, `filesystem`, `subagents`, `human-in-the-loop` |
| Platform integration | `mcp`, `memory`, `sessions`, `mlflow-tracing`, `genie`, `uc-functions` |

`research-agent` carries `filesystem` and `subagents` capabilities too — `create_deep_agent`
installs all three middlewares unconditionally — but they are not tagged there because the
**Showcases** column is what distinguishes the projects, and those two are `fs-agent` and
`multi-agent`'s headline. Tags describe what a project demonstrates deliberately.

## What each project looks like

Every agent directory follows the same layout, so the docs are in the same place each time:

| File | Owns |
| --- | --- |
| `README.md` | The demo script — run it locally, then deploy it. Start here. |
| `study_guide.md` | The code reading path: concepts, request lifecycle, which files to read in order |
| `REFERENCE.md` | The HTTP API, recovery semantics, architecture — what a demo does not need |
| `AGENTS.md` | Development map for coding agents. `CLAUDE.md` is a one-line include of it |
| `AGENTKIT_CONTRACT.md` | Managed-runtime integration requirements |
| `CHAT_APP.md` | The optional browser chat overlay |

Code sits under `agent/` (the framework-native agent, its prompt and its tools) and `runtime/` (the
managed-runtime adapter and server). That split is deliberate: `agent/` has no dependency on Agent
Bricks request types, so it can be driven from a notebook or a test.

## Getting started

Each project is independent — there is no root build. Pick one and follow its README:

```bash
cd research-agent
uv sync
agentbricks dev          # http://localhost:8000, chat app included
uv run pytest            # hermetic; no workspace needed
```

Deploying needs a Databricks workspace profile:

```bash
databricks auth login --profile <name>
agentbricks --profile <name> deploy
```

## Adding a project

Scaffold it rather than copying an existing directory, then add a row to the table above:

```bash
agentbricks init <name> --framework langgraph
```

`agentbricks init --help` lists the available frameworks and templates. To bring an agent that
already exists, run `agentbricks init --framework <f> --existing .` inside it to generate a
migration prompt for a coding agent, working against that project's `AGENTKIT_CONTRACT.md`.

## License

[Apache 2.0](LICENSE).
