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

**A missing index does not look like a failure.** The server URL below is *schema*-scoped, so it
exists as long as the schema does. Delete the index and the server still connects and still lists
tools successfully — it just lists zero of them, lands in ``active_servers`` as an empty server, and
logs nothing. The agent then answers from sales data alone and the only symptom is a reply saying it
has no review tool.

``REQUIRED_REVIEW_TOOL`` is the guard: ``agent.py`` asserts that name resolved and raises if it did
not, so the index going missing is a loud error instead of a quiet half-answer. Connection success
proves nothing on its own.
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
_INDEX_NAME = "bakehouse_reviews_index"

# This server's name, and the MCP tool name Unity Catalog exposes for the index it carries. A
# schema-scoped vector-search server lists one tool per index, named ``{catalog}__{schema}__{index}``.
#
# `agent.py` pairs these two: *if* a server by this name was offered, its tools must include that
# name, or the graph build fails. Scoping the assertion to this server rather than to the whole tool
# list is what keeps it from firing when there is no review server to begin with — the hermetic
# tests stub `mcp_servers` with their own mocks, and `tests/test_agent.py` still asserts that an
# unrelated unavailable server is skipped rather than fatal.
REVIEW_SERVER_NAME = "bakehouse_reviews"
REQUIRED_REVIEW_TOOL = f"{_INDEX_CATALOG}__{_INDEX_SCHEMA}__{_INDEX_NAME}"


def build_mcp_servers() -> list[McpServer]:
    """Return the vector-search MCP server holding the customer-review index."""
    client = WorkspaceClient()
    host = client.config.host.rstrip("/")
    return [
        McpServer(
            url=f"{host}/api/2.0/mcp/vector-search/{_INDEX_CATALOG}/{_INDEX_SCHEMA}",
            name=REVIEW_SERVER_NAME,
            # `workspace_client` is how this server authenticates — McpServer resolves credentials
            # from it. Do NOT add a top-level `headers=`: McpServer subclasses
            # MCPServerStreamableHttp, which takes headers only inside a
            # MCPServerStreamableHttpParams passed as `params`, and a stray `headers=` kwarg fails
            # the whole graph build with
            # `MCPServerStreamableHttp.__init__() got an unexpected keyword argument 'headers'`.
            workspace_client=client,
        ),
    ]
