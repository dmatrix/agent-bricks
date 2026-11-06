"""MCP servers to offer the agent — this is where you configure them.

The agent gets three sources, and all three are MCP:

- **Genie** (`bakehouse_sales`) and, later, **Slack** are declared in ``agent.toml`` by
  ``agentbricks tools add``, so the runtime builds them.
- **Vector Search** (`bakehouse_reviews`) is built here, because ``agent.toml`` has no kind for it:
  the manifest accepts ``mcp``, ``uc_function``, ``genie_agent``, ``genie_one`` and ``sandbox``
  only. Unity Catalog exposes a vector-search MCP server per catalog/schema at
  ``/api/2.0/mcp/vector-search/{catalog}/{schema}``, which is the path below.

Choosing MCP over a retriever library is deliberate: this project ships no LangChain and no
vector-search SDK, and this path needs neither.

**Servers returned here are best-effort.** ``agent.py`` joins them with the ``agent.toml`` servers
and omits any that fail to connect or list tools — so if this one breaks, the agent silently loses
its unstructured source and answers from sales data alone. Assert its tools are present rather than
assuming; see README, "Verify the three sources".
"""

from databricks.sdk import WorkspaceClient
from databricks_openai.agents import McpServer

# The index lives in a catalog we own; its *source* is derived from the public table
# `samples.bakehouse.media_gold_reviews_chunked`, which is never modified.
#
# A derived table exists for one reason: a DELTA_SYNC index needs a unique single-column primary
# key, and the public table's `chunk_id` is not unique — 196 rows, 138 distinct values. The derived
# table adds `chunk_pk = md5(franchiseID|review_date|chunk_id)`, unique at 196, and keeps
# `franchiseID` so reviews stay attributable to a franchise.
_INDEX_CATALOG = "jules_catalog"
_INDEX_SCHEMA = "default"


def build_mcp_servers() -> list[McpServer]:
    """Return the vector-search MCP server holding the customer-review index."""
    client = WorkspaceClient()
    host = client.config.host.rstrip("/")
    return [
        McpServer(
            url=f"{host}/api/2.0/mcp/vector-search/{_INDEX_CATALOG}/{_INDEX_SCHEMA}",
            name="bakehouse_reviews",
            # `workspace_client` is how this server authenticates — McpServer resolves credentials
            # from it. Do NOT add a top-level `headers=`: McpServer subclasses
            # MCPServerStreamableHttp, which takes headers only inside a
            # MCPServerStreamableHttpParams passed as `params`, and a stray `headers=` kwarg fails
            # the whole graph build with
            # `MCPServerStreamableHttp.__init__() got an unexpected keyword argument 'headers'`.
            workspace_client=client,
        ),
    ]
