"""Structural tests for the Deep Agents wiring.

Hermetic, through two seams. ``agent.agent._chat_model`` is monkeypatched here to a fake chat model,
so no Databricks auth is needed for the model. ``agent.agent.mcp_tools`` is stubbed in
``tests/conftest.py``, because ``agent.toml`` declares a managed MCP service for deployment and
discovering it would reach the workspace on every graph build. The rest is already offline —
``memory_tools`` returns ``[]`` without ``AGENT_MEMORY_STORE``, ``genie_tools`` parses ``agent.toml``
and makes no network calls with nothing bound, and ``checkpointer()`` falls back to an in-process
saver.

These assertions are deliberately structural rather than behavioural. They check the *shape* of the
compiled graph — which tools exist, which state channels exist, what the subagent specs look like —
because the defects this port can introduce are all shape defects, and catching them here costs
milliseconds instead of a live model call. See ``test_general_purpose_subagent_*`` in particular:
that is the regression guard for a subagent ``create_deep_agent`` injects on its own.
"""

import pytest
from agent.agent import (
    GATED_MCP_VERBS,
    REQUIRE_APPROVAL,
    _build_subagents,
    approval_gate,
    create_agent_graph,
)
from agent.tools import all_tools
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.tools import StructuredTool
from langgraph.types import Overwrite
from runtime.adapter import _serialize_events

# Installed by create_deep_agent unconditionally — planning, the virtual filesystem, and delegation.
HARNESS_TOOLS = {
    "write_todos",
    "task",
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
}

# The scaffold's sample tool, the two ported from the tutorial's Example 1, and the local sender
# that carries the approval-gate demo.
PROJECT_TOOLS = {
    "get_current_time",
    "search_knowledge_base",
    "get_mlflow_adoption",
    "send_message",
}


def _noop(text: str) -> str:
    """Stand-in for an operation on a managed MCP service."""
    return "ok"


def _fake_mcp_tool(name: str) -> StructuredTool:
    """A real BaseTool under an arbitrary name — create_deep_agent binds these, so a stub with a
    `.name` attribute is not enough."""
    return StructuredTool.from_function(func=_noop, name=name, description=_noop.__doc__)


# What a managed MCP service would hand back: one write operation and one read. Hand-built rather
# than discovered, because `tests/conftest.py` stubs discovery to return nothing — and because no
# messaging service is bound any more, so there is no real write tool to exercise the verb gate.
MCP_TOOLS = [_fake_mcp_tool("mcp_post_message"), _fake_mcp_tool("mcp_list_channels")]


@pytest.fixture
def graph(monkeypatch):
    """A compiled graph with the model swapped for a fake, so nothing reaches a workspace."""
    monkeypatch.delenv("AGENT_MEMORY_STORE", raising=False)
    monkeypatch.delenv("AGENT_SESSION_STORE", raising=False)
    monkeypatch.setattr(
        "agent.agent._chat_model",
        lambda endpoint: GenericFakeChatModel(messages=iter(["ok"])),
    )

    async def build():
        return await create_agent_graph("test-actor")

    import asyncio

    return asyncio.run(build())


def _tools(graph):
    return graph.nodes["tools"].bound.tools_by_name


def test_harness_tools_are_installed(graph):
    # If any of these is missing, create_deep_agent did not install its middleware stack and this
    # is just a plain create_agent again.
    assert HARNESS_TOOLS <= set(_tools(graph))


def test_project_tools_survive_the_swap(graph):
    # The scaffold's tools and the tutorial's must both still be reachable: all_tools() discovery
    # has to keep working through create_deep_agent.
    assert PROJECT_TOOLS <= set(_tools(graph))


def test_files_and_todos_live_in_graph_state(graph):
    # The StateBackend contract. Files and the plan are state channels, so they are checkpointed by
    # whatever saver checkpointer() selected and survive restarts and approval pauses. A
    # FilesystemBackend would put files in a replica's container instead, where they would not be.
    # Asserted on the compiled graph rather than via isinstance(backend, StateBackend), which is not
    # reachable from here.
    assert {"files", "todos"} <= set(graph.channels)


def _graph_with_mcp(monkeypatch, mcp):
    monkeypatch.delenv("AGENT_MEMORY_STORE", raising=False)
    monkeypatch.delenv("AGENT_SESSION_STORE", raising=False)
    monkeypatch.setattr(
        "agent.agent._chat_model",
        lambda endpoint: GenericFakeChatModel(messages=iter(["ok"])),
    )

    async def _mcp_tools(_servers, **_kwargs):
        return mcp

    monkeypatch.setattr("agent.agent.mcp_tools", _mcp_tools)
    import asyncio

    return asyncio.run(create_agent_graph("test-actor"))


def test_approval_gate_arrives_with_a_resolved_mcp_write_tool(monkeypatch):
    # The gate is no longer a literal tool name: a managed MCP tool only exists once its service
    # resolves, so the middleware has to appear when the tool does.
    graph = _graph_with_mcp(monkeypatch, MCP_TOOLS)
    assert "HumanInTheLoopMiddleware.after_model" in graph.nodes
    assert {"mcp_post_message", "mcp_list_channels"} <= set(_tools(graph))


def test_approval_gate_is_installed_for_the_local_sender(graph):
    # No MCP tool resolves here (conftest stubs discovery), which is also what `agentbricks dev`
    # sees. The middleware is present anyway because REQUIRE_APPROVAL gates `send_message` — that is
    # what makes the approve / edit / reject flow reachable locally.
    assert "HumanInTheLoopMiddleware.after_model" in graph.nodes
    assert "send_message" in _tools(graph)


def test_approval_gate_is_absent_when_nothing_is_gated(monkeypatch):
    # create_deep_agent branches on `interrupt_on is not None`, so passing an empty dict would
    # install an inert middleware instead of none. agent.py passes `gated or None`. Reaching this
    # now takes emptying REQUIRE_APPROVAL, since the local sender is gated unconditionally.
    monkeypatch.setattr("agent.agent.REQUIRE_APPROVAL", {})
    graph = _graph_with_mcp(monkeypatch, [])
    assert "HumanInTheLoopMiddleware.after_model" not in graph.nodes


def test_read_only_mcp_tools_are_not_gated():
    # A gate that paused on every MCP call would make the agent unusable for search. Compared
    # against REQUIRE_APPROVAL rather than {}: the local sender is always in the map, so what is
    # asserted is that these two tools add nothing to it.
    read_only = [_fake_mcp_tool("web_search"), _fake_mcp_tool("mcp_list_channels")]
    assert approval_gate(read_only) == REQUIRE_APPROVAL


def test_mcp_write_tools_are_gated():
    assert approval_gate(MCP_TOOLS) == {**REQUIRE_APPROVAL, "mcp_post_message": True}


def test_harness_filesystem_tools_are_never_passed_to_the_gate():
    # `write_file` and `edit_file` contain a gated verb. They reach the agent through the harness,
    # not through MCP, so create_agent_graph passes only the MCP list to approval_gate — this is the
    # guard for that, since gating them would pause the agent on its own scratch filesystem.
    assert any(verb in "write_file" for verb in GATED_MCP_VERBS)
    assert HARNESS_TOOLS.isdisjoint(approval_gate([]))


def test_general_purpose_subagent_is_overridden_not_appended(graph):
    # create_deep_agent always seeds SubAgentMiddleware with [general_purpose_spec, *ours], and that
    # built-in spec is built with the main agent's whole tool list. Supplying a spec under the same
    # name replaces it. Two occurrences here would mean ours did not match and the default came back
    # alongside it, carrying send_message into a subagent.
    assert _tools(graph)["task"].description.count("- general-purpose:") == 1


def test_general_purpose_subagent_cannot_call_gated_tools():
    # The regression guard worth keeping. No subagent graph is compiled with a checkpointer, so an
    # interrupt raised inside one can never be resumed: a gated tool reachable from a subagent hangs
    # the turn rather than gating it. Keep approval-gated tools on the main agent only.
    tools = [*all_tools(), *MCP_TOOLS]
    gated = approval_gate(MCP_TOOLS)
    assert gated, "the fixture must gate something or this asserts nothing"
    for spec in _build_subagents(tools, gated):
        assert set(gated).isdisjoint({tool.name for tool in spec["tools"]})


def test_subagent_specs_are_well_formed():
    specs = _build_subagents([*all_tools()], {})
    assert [spec["name"] for spec in specs] == [GENERAL_PURPOSE_SUBAGENT["name"]]
    for spec in specs:
        # Never omit `tools`: the default is the main agent's entire list.
        assert "tools" in spec
        # deepagents 0.4.12's SubAgent TypedDict requires `system_prompt`. A spec using `prompt`
        # would be silently ignored, leaving the subagent on the default prompt.
        assert "system_prompt" in spec
        assert "prompt" not in spec


def test_general_purpose_description_does_not_overstate_its_tools():
    # The description is what the main agent reads when choosing whether to delegate. The stock text
    # claims the subagent "has access to all tools as the main agent", which our tool scoping makes
    # false; left alone it invites delegation of work the subagent cannot do.
    tools = [*all_tools(), *MCP_TOOLS]
    (spec,) = _build_subagents(tools, approval_gate(MCP_TOOLS))
    assert "access to all tools as the main agent" not in spec["description"]
    assert "mcp_post_message" in spec["description"]
    assert "send_message" in spec["description"]


async def _aiter(events):
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_serialize_events_relays_the_plan():
    # The plan is custom state; the contract says custom state needs an explicit mapping rather
    # than being dropped to fit the messages-only example.
    todos = [{"content": "Search the knowledge base", "status": "pending"}]
    stream = _aiter([("updates", {"model": {"todos": todos}})])
    assert [e async for e in _serialize_events(stream)] == [{"type": "todos", "todos": todos}]


@pytest.mark.asyncio
async def test_serialize_events_relays_written_files():
    stream = _aiter([("updates", {"tools": {"files": {"/b.md": "second", "/a.md": "first"}}})])
    events = [e async for e in _serialize_events(stream)]
    # Sorted, so a client rendering the paths gets a stable order.
    assert events == [{"type": "files", "paths": ["/a.md", "/b.md"]}]


@pytest.mark.asyncio
async def test_serialize_events_unwraps_overwrite_updates():
    """The regression test for the bug that broke every single turn.

    ``PatchToolCallsMiddleware``, which ``create_deep_agent`` installs unconditionally, repairs
    dangling tool calls in ``before_agent`` and returns ``{"messages": Overwrite(patched)}``. That
    reaches the adapter through ``stream_mode="updates"``, and the stock serializer iterated the
    channel directly — so it raised ``TypeError: 'Overwrite' object is not iterable`` on the first
    message of the first turn. The original version of these tests passed a plain list and so never
    caught it; use ``Overwrite`` here, the way the harness really does.
    """

    class _Message:
        def model_dump(self):
            return {"role": "assistant", "content": "hi"}

    stream = _aiter([("updates", {"PatchToolCallsMiddleware.before_agent": {"messages": Overwrite([_Message()])}})])
    events = [event async for event in _serialize_events(stream)]
    assert events == [{"type": "message", "message": {"role": "assistant", "content": "hi"}}]


@pytest.mark.asyncio
async def test_serialize_events_unwraps_overwrite_for_custom_state():
    todos = [{"content": "step one", "status": "pending"}]
    stream = _aiter([("updates", {"model": {"todos": Overwrite(todos)}})])
    assert [e async for e in _serialize_events(stream)] == [{"type": "todos", "todos": todos}]


@pytest.mark.asyncio
async def test_serialize_events_still_relays_messages_alongside_custom_state():
    # The additive mapping must not displace the message relay the chat transcript depends on.
    class _Message:
        def model_dump(self):
            return {"role": "assistant", "content": "hi"}

    stream = _aiter([("updates", {"model": {"messages": [_Message()], "todos": [{"content": "x"}]}})])
    types = [event["type"] async for event in _serialize_events(stream)]
    assert types == ["message", "todos"]
