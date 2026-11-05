"""Shared fixtures that keep the suite hermetic.

The tests build the *real* graph, so every path `create_agent_graph` touches has to be offline.
Two seams make that true. `agent.agent._chat_model` is patched per test (see
`tests/test_deep_agent.py`), and `agent.agent.mcp_tools` is patched here, for every test.

Why the second seam exists: `agent.toml` declares the managed MCP service `web_search`, so a deploy
picks it up with no manual step. `create_agent_graph` calls `mcp_tools(...)` on *every* graph build
to discover each declared server's tools, and that discovery is a real workspace round-trip.
`web_search` resolves today, so without this stub the suite would depend on workspace auth and
network reachability, and pay the latency once per graph build.

The binding is declared `auth = "app"`, which also means a discovery failure is only logged
(`Failed to fetch MCP tools from server '<id>'; continuing without it`). Were it switched to
`auth = "user"`, `databricks_agentkit.langgraph.mcp` would re-raise it as `AuthError` instead and no
graph could be built at all without a request user — the stub covers that case too.

Returning `[]` means every test sees the no-MCP-resolved case, which is also what `agentbricks dev`
sees. So the only gated tool in the suite is the local `send_message` (via
`agent.agent.REQUIRE_APPROVAL`); `approval_gate`'s verb matching is exercised with hand-built fake
MCP tools in `tests/test_deep_agent.py` rather than through this fixture.

Stubbing discovery is deliberately narrow: it replaces the network call, not the manifest. The
binding still has to be declared, and `tests/test_manifest.py` asserts it is — so this fixture
cannot quietly hide its removal. It also means no test here proves a declared MCP tool reaches the
agent; that is deployment behaviour, and the place to check it is a deployed invocation.
"""

import pytest


@pytest.fixture(autouse=True)
def stub_mcp_discovery(monkeypatch):
    """Discover no MCP tools, instead of calling the workspace once per declared server."""

    async def _no_mcp_tools(_servers, **_kwargs):
        return []

    monkeypatch.setattr("agent.agent.mcp_tools", _no_mcp_tools)
