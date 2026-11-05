"""The research agent's knowledge base, ported from the Deep Agents tutorial.

Source: ``search_knowledge_base`` in Example 1 of
https://github.com/dmatrix/mlflow-genai-tutorials/blob/main/11_deep_agents_langgraph.ipynb

The notebook's own comment says a real use case would keep this "in a database or a vector
database". On Databricks that means a Vector Search index (see the `databricks-vector-search` docs)
or a Genie space bound with `agentbricks tools add` — either would arrive as a tool here through
`genie_tools()` or `mcp_tools()` without touching this module. It stays a literal dict for one
reason: `uv run pytest` builds the real graph, and a hermetic test suite cannot reach a workspace.

Two typos in the notebook's prose are corrected here ("Managved" -> "Managed", "to be stores" ->
"to be stored"); the wording of the notebook's four topics is otherwise verbatim.

The topics after those four are not in the notebook. They summarize key concepts from
https://mlflow.org/docs/latest/genai/ -- AI Gateway, the MCP Registry, role-based access control,
agent serving and the Prompt Registry -- and follow the same shape: a lower-cased topic key whose
individual words are what `search_knowledge_base` matches on, mapped to a prose snippet.

Keep multi-word keys free of very short tokens: matching is substring-based, so a bare "ai" key
word would fire on any query containing "langchain", "explain" or "available" -- hence
"mlflow gateway" rather than "mlflow ai gateway", and "rbac" alongside "access control" so both
namings retrieve. "registry" is deliberately shared by the MCP and prompt registry topics, which
is why a bare "registry" query returns both.
"""

from langchain_core.tools import tool

_KNOWLEDGE = {
    "mlflow tracing": (
        "MLflow Tracing provides observability for AI applications. It captures "
        "hierarchical spans showing LLM calls, tool invocations, and retrieval steps. "
        "Supports auto-tracing for OpenAI, LangChain, LlamaIndex, and LangGraph, and many more. "
        "Traces are stored in the MLflow tracking server and viewable in the UI. With Managed "
        "MLflow traces configured to be stored in Unity Catalog, you can also access them in "
        "Databricks SQL."
    ),
    "mlflow evaluation": (
        "MLflow GenAI Evaluation (mlflow.genai.evaluate) assesses AI application quality "
        "using built-in scorers (Correctness, RelevanceToQuery, Safety) and custom scorers. "
        "It integrates with tracing to evaluate end-to-end agent behavior. "
        "Supports LLM-as-a-judge and Agent-as-a-judge patterns for automated quality assessment."
    ),
    "deep agents": (
        "Deep Agents are LangChain's open-source agent harness built on LangGraph. "
        "They add planning (todo lists), file system tools, and sub-agent delegation "
        "to the standard tool-calling loop. Designed for long-running, multi-step tasks "
        "like research, coding, and analysis."
    ),
    "langgraph": (
        "LangGraph is a framework for building stateful, multi-actor AI applications "
        "using graph-based workflows. It supports conditional routing, cycles, "
        "checkpointing, and streaming. Agents built with LangGraph are automatically "
        "traced by MLflow when mlflow.langchain.autolog() is enabled."
    ),
    "mlflow gateway": (
        "MLflow AI Gateway is a unified serving layer that puts multiple LLM providers "
        "(OpenAI, Anthropic, and others) behind a single secure endpoint. Provider "
        "credentials are held centrally as LLM connections, so applications never embed API "
        "keys. Endpoints can be created, updated, or removed without restarting the gateway, "
        "and support traffic splitting for A/B tests plus fallbacks for high availability. "
        "The gateway tracks token consumption, latency, and cost per endpoint, enforces "
        "budget limits and alerts, and can apply LLM-judge guardrails that block or sanitize "
        "requests and responses. Passthrough endpoints forward requests in a provider's "
        "native format when provider-specific features are needed."
    ),
    "mlflow mcp registry": (
        "MLflow MCP Registry (experimental, MLflow 3.15.0+) is a central catalog of Model "
        "Context Protocol servers. Each server is described by a server.json following the "
        "open MCP registry standard and named with a reverse-DNS namespace such as "
        "io.github.anthropic/brave-search. Register servers in the UI, from a server.json "
        "URL, or with mlflow.genai.register_mcp_server(). An MCPServer is the logical entry; "
        "each MCPServerVersion is a semver release carrying the server_json payload, a "
        "snapshot of the tools the server exposes, and a status of draft, active, deprecated, "
        "or deleted, with aliases available to pin a moving target. An MCPAccessEndpoint, "
        "created with mlflow.genai.create_mcp_access_endpoint(), pairs a URL and transport "
        "type with a version or alias, and the registry generates connection instructions "
        "for clients such as Claude Code and .mcp.json."
    ),
    "mlflow rbac access control": (
        "MLflow role-based access control (RBAC) lets admins grant access by assigning "
        "roles -- reusable bundles of permissions -- rather than wiring up per-user grants. "
        "It requires authentication to be enabled, for example "
        "`mlflow server --app-name basic-auth`. Permissions attach to experiments, "
        "registered models, prompts, scorers, and AI Gateway resources (secrets, endpoints, "
        "model definitions); sub-resources such as runs and model versions can be granted "
        "separately in MLflow 3.17+. The levels are READ, USE (consume without modifying, "
        "such as invoking a gateway endpoint), EDIT, MANAGE, and DENY, where DENY overrides "
        "positive grants on the same resource. Overlapping grants resolve to the highest "
        "level unless a DENY applies, and a resource with no matching grant falls back to "
        "the server default. With `--enable-workspaces` every resource belongs to exactly "
        "one workspace, and each new workspace is seeded with an admin role (workspace "
        "MANAGE) and a user role (workspace USE)."
    ),
    "mlflow agent serving": (
        "MLflow agent serving exposes an agent as a production REST API. The agent server is "
        "a lightweight FastAPI app: annotate agent functions with @invoke for single "
        "responses or @stream for streamed ones, and they are served from a single "
        "POST /invocations endpoint that takes a JSON body with an `input` list of messages. "
        "Agents written against the Responses API schema get automatic request and response "
        "validation, and setting \"stream\": true switches to a streaming response when a "
        "@stream function is registered. Calls are traced automatically and appear in the "
        "MLflow UI's Traces tab, and git-based version tracking can tie traces to a commit. "
        "The registered invoke function can be wrapped in a predict_fn and scored with "
        "mlflow.genai.evaluate(). For plain model serving, `mlflow models serve` hosts a "
        "logged or registered model behind POST /invocations with GET /ping, /health, and "
        "/version health and metadata routes, while ResponsesAgent covers multi-step agent "
        "response patterns."
    ),
    "mlflow prompt registry": (
        "MLflow Prompt Registry is a central store for versioning, tracking, and reusing the "
        "prompts behind LLM applications and agents. Register a prompt in the UI's Prompts "
        "tab or with mlflow.genai.register_prompt(name, template, commit_message, tags); "
        "calling it again under the same name adds a sequentially numbered version. Versions "
        "are immutable and carry git-like commit messages, so editing a template means "
        "cutting a new version, and the UI can diff two versions side by side. Aliases are "
        "movable pointers such as `production` that let you A/B test or roll back without "
        "touching application code, with the reserved `@latest` always resolving to the "
        "newest version. Templates come as text with {{variable}} placeholders, chat message "
        "lists, or sandboxed Jinja2 with conditionals and loops, and "
        "to_single_brace_format() converts them for LangChain or LlamaIndex. Each version "
        "can hold a model_config (model name, temperature, max tokens, provider-specific "
        "extra_params) and an optional response_format. Load prompts with "
        "mlflow.genai.load_prompt() and find them with search_prompts(); version loads are "
        "cached indefinitely and alias loads for 60 seconds by default, tunable via "
        "cache_ttl_seconds. Prompts load into the LLM Playground to test against an AI "
        "Gateway endpoint, and the registry ties into tracing, evaluation, and automated "
        "prompt optimization for lineage from a prompt version to the traces it produced."
    ),
}


@tool
def search_knowledge_base(query: str) -> str:
    """Search an internal knowledge base for information about MLflow, GenAI, and Agentic Workflow topics.

    Returns relevant information snippets.
    """
    # Keyword matching, as in the notebook: a topic is returned when any single word of its key
    # appears in the query, so "tracing" matches "mlflow tracing" and "agents" matches "deep agents".
    results = [
        f"[{topic.upper()}]: {info}"
        for topic, info in _KNOWLEDGE.items()
        if any(word in query.lower() for word in topic.split())
    ]
    return "\n\n".join(results) if results else f"No results found for: {query}"
