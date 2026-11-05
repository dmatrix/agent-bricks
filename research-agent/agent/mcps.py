"""MCP servers to offer the agent — this is where you configure them.

Empty by default: the agent runs with only the MCP servers declared in ``agent.toml``. Add servers
to ``build_mcp_servers`` to offer more; ``agent.py`` passes them to ``mcp_tools``, which joins them
with the ``agent.toml`` servers and fetches their tools each request.

**Prefer a binding to a hand-built server.** ``agentbricks tools add mcp <service>`` writes a
``[[tools]]`` entry, and ``databricks_agentkit.langgraph.mcp._server_from_tool`` then constructs the
server for you — with the account-host routing header, a 120s timeout, the ``auth`` mode resolved
per request, and the skip-vs-``AuthError`` behaviour on discovery failure. A server built here gets
none of that unless you pass it yourself. Use this module only for servers that have no
``agent.toml`` kind.
"""

from databricks_langchain import DatabricksMCPServer


def build_mcp_servers() -> list[DatabricksMCPServer]:
    """Return the MCP servers to offer the agent. Empty by default — add your own.

    Databricks speaks MCP over several URL families and they are easy to confuse.
    ``databricks_agentkit.langgraph.mcp._server_from_tool`` and
    ``databricks_langchain.DatabricksMCPServer.from_uc_function`` are the source of truth:

    - Unity Gateway managed service — ``{host}/ai-gateway/mcp-services/{catalog.schema.name}``
    - Genie One — ``{host}/api/2.0/mcp/genie``
    - UC functions — ``{host}/api/2.0/mcp/functions/{catalog}/{schema}[/{function}]``
    - UC vector search — ``{host}/api/2.0/mcp/vector-search/{catalog}/{schema}[/{index}]``

    ``/api/2.1/unity-catalog/mcp-services`` is *not* one of them, despite the name. That is the
    control-plane listing API behind ``agentbricks tools list --kind mcp``
    (``databricks_agentkit.runtime`` uses it via ``_api_client``); speaking MCP to it does not work.

    **Do not hand-build a Unity Gateway managed service here.** Those are the ``system.ai.*``
    connectors — ``web_search``, ``slack``, ``github``, ``sandbox`` and the rest of
    ``agentbricks tools list --kind mcp``. Bind them instead, so the runtime owns the URL and the
    auth mode::

        agentbricks tools add mcp system.ai.web_search

    Note the URL's last segment is the full three-part name (``system.ai.slack``), not a bare
    ``slack`` — a frequent mistake when writing one by hand.

    What belongs here is a server with no ``agent.toml`` kind: a UC functions server scoped to a
    whole catalog and schema, or a third-party MCP server. Authed as the app service principal,
    with one ``WorkspaceClient`` reused rather than built per call::

        from databricks.sdk import WorkspaceClient
        from databricks_agentkit import workspace_headers

        client = WorkspaceClient()
        host = client.config.host.rstrip("/")
        return [
            DatabricksMCPServer(
                name="system-ai-functions",
                url=f"{host}/api/2.0/mcp/functions/system/ai",
                # Account-host routing (X-Databricks-Org-Id). `mcp_tools` passes this for every
                # declared server; omit it and an account-host workspace resolves the call against
                # the wrong workspace.
                headers=workspace_headers() or None,
                workspace_client=client,
                # Match what the runtime gives declared servers.
                timeout=120.0,
            ),
        ]

    A server that should act as the *signed-in user* rather than the app cannot be built here: it
    needs the request's resolver, which only ``mcp_tools(..., workspace_client_for=...)`` holds.
    """
    return []
