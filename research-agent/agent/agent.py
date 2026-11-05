import os
from collections.abc import AsyncGenerator, Callable
from functools import lru_cache
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks_langchain import ChatDatabricks
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

# `interrupt_on` installs this same middleware inside create_deep_agent, so the interrupt payload
# shape is unchanged and `_validate_approval_resume` still needs HITLResponse to validate it.
from langchain.agents.middleware.human_in_the_loop import HITLResponse
from langchain_core.language_models import BaseChatModel
from langgraph.types import Command
from pydantic import TypeAdapter, ValidationError

from agent.mcps import build_mcp_servers
from agent.prompts import RESEARCH_PROMPT

# Importing the tools package auto-registers every tool module.
from agent.tools import all_tools
from databricks_agentkit import workspace_client, workspace_headers
from databricks_agentkit.langgraph import (
    checkpointer,
    configure_tracing,
    genie_tools,
    mcp_tools,
    memory_tools,
    start_trace,
    thread_config,
)
from databricks_agentkit.langgraph.session_store import (
    invocation_id_from_metadata,
    invocation_metadata,
)
from databricks_agentkit.runtime.auth import AuthError
from databricks_agentkit.runtime.tool_manifest import resolve_model_service

# A Unity Catalog AI Gateway model service, served from the `system.ai` schema and queried through
# the gateway (see `use_ai_gateway=True` below). Swap for any `system.ai.*` model service your
# workspace exposes — the demo chat app's picker lists what's available.
# `agentbricks experimental models bind` swaps this for a model service you own (wired in by `agentbricks deploy`
# as AGENT_MODEL_SERVICE_AGENT), so `agentbricks experimental models upgrade` can change the model without a code
# change.
MODEL = resolve_model_service() or "system.ai.gpt-5-5"

# Local tools that always require human approval.
#
# `send_message` is the gate's subject, and gating it locally is what makes the approve / edit /
# reject flow demonstrable with no managed MCP messaging service bound: `system.ai.slack` needs a
# per-user OAuth sign-in that `auth = "app"` cannot complete, and the `auth = "user"` alternative
# stops the graph from building locally at all. See `agent/tools/send_message.py` — it delivers
# nothing, and must be deleted if a real MCP messaging service is ever bound, or it will shadow that
# service's tool the way it did before (`tests/test_agent.py` guards the one-sender rule).
#
# This gate can name its tool literally, unlike GATED_MCP_VERBS below: a local tool exists at import
# time, whereas a managed MCP tool only exists once its service resolves.
#
# Map a tool name to True to allow every decision (approve / edit / reject / respond), or to a config
# dict to restrict them (see HumanInTheLoopMiddleware). When a gated tool is about to run, the agent
# pauses and emits an `interrupt` event; the client resumes by sending `resume` with the same session
# id.
REQUIRE_APPROVAL: dict[str, Any] = {"send_message": True}

# A discovered MCP tool whose name contains one of these is gated too. The gate cannot be a literal
# name here: managed MCP tools only exist once their service resolves, so the agent learns their
# names at graph-build time. Matching on the verb is what makes the gate arrive with the tool.
#
# Read-only operations are deliberately not gated — `web_search`, `list_channels` and `get_messages`
# match nothing here, so they run unattended. Anything that leaves a mark somewhere else does not.
# Over-gating is the safe direction: an extra approval prompt costs a click, an unreviewed post
# cannot be taken back.
GATED_MCP_VERBS = ("send", "post", "write", "reply", "upload", "create", "update", "delete")


def approval_gate(mcp: list[Any]) -> dict[str, Any]:
    """The HumanInTheLoopMiddleware map for one request's tool list.

    Takes only the MCP tools, never the whole list: the harness's own `write_file` and `edit_file`
    would match ``GATED_MCP_VERBS`` and gating those would pause the agent on its own scratch
    filesystem, which no human needs to approve.
    """
    gated = dict(REQUIRE_APPROVAL)
    for tool in mcp:
        name = getattr(tool, "name", "")
        if any(verb in name.lower() for verb in GATED_MCP_VERBS):
            gated[name] = True
    return gated


class _RoutedChatDatabricks(ChatDatabricks):
    """Forward account-host workspace routing to the underlying OpenAI clients."""

    def _get_client_kwargs(self) -> dict[str, Any]:
        kwargs = super()._get_client_kwargs()
        if headers := workspace_headers():
            kwargs["default_headers"] = headers
        return kwargs


@lru_cache(maxsize=1)
def _app_workspace_client() -> WorkspaceClient:
    """The App's own client, shared across turns.

    Constructing a WorkspaceClient resolves auth with blocking network calls (and, locally, a
    `databricks auth token` subprocess). Doing that per turn inside the async graph builder stalls
    every request on the event loop; the SDK refreshes tokens itself, so one client suffices.
    """
    return workspace_client()


def configure() -> None:
    """Wire up global state; call once at server startup (not at import)."""
    _check_databricks_auth()
    configure_tracing()


def _check_databricks_auth() -> None:
    """Fail fast at startup with a clear message if Databricks auth isn't configured.

    Without this, a missing/invalid profile only surfaces on the first model call — as a generic SDK
    error buried in a request traceback. Resolving a WorkspaceClient here validates the same config
    the model client uses, so the failure is immediate and actionable.
    """
    try:
        _app_workspace_client()
    except Exception as e:
        profile = os.getenv("DATABRICKS_CONFIG_PROFILE")
        target = (
            f"profile {profile!r}" if profile else "the DEFAULT profile / DATABRICKS_HOST+TOKEN"
        )
        raise RuntimeError(
            f"Databricks auth is not configured — the agent can't call the model. Tried {target}.\n"
            "Fix one of:\n"
            "  • set DATABRICKS_CONFIG_PROFILE in .env to a profile from `databricks auth profiles`, or\n"
            "  • run `databricks auth login --profile <name>` to create one, or\n"
            "  • set DATABRICKS_HOST and DATABRICKS_TOKEN in .env.\n"
            f"(underlying error: {e})"
        ) from e


def _chat_model(endpoint: str) -> BaseChatModel:
    """The gateway-routed chat model for ``endpoint``.

    use_ai_gateway routes to the Unity Catalog AI Gateway (`<host>/ai-gateway/mlflow/v1`), so
    `endpoint` is a `system.ai.*` model name rather than a serving-endpoint name. Extracted into one
    function so the hermetic tests have a single seam to monkeypatch.
    """
    return _RoutedChatDatabricks(
        endpoint=endpoint, workspace_client=_app_workspace_client(), use_ai_gateway=True
    )


def _build_subagents(tools: list[Any], gated: dict[str, Any]) -> list[dict[str, Any]]:
    """The subagent specs for this agent — just an override of the built-in general-purpose one.

    This looks like dead weight, because the notebook's Example 1 passes no ``subagents`` at all.
    It isn't: ``create_deep_agent`` installs ``SubAgentMiddleware`` unconditionally and seeds it with
    ``[general_purpose_spec, *your_subagents]``, where ``general_purpose_spec`` is built with
    ``tools=tools`` — i.e. *everything the main agent has*, the approval-gated tools included.
    Supplying a spec under the same name replaces it (that is the documented override hook), which is
    the only way to keep an approval-gated tool off it.

    Why that matters: no subagent graph is compiled with a checkpointer, so an interrupt raised
    inside one can never be resumed. The general-purpose subagent *does* get the HITL middleware, so
    leaving it alone would not make a Slack post ungated — it would make it hang. Gated tools
    therefore stay on the main agent only.

    ``GENERAL_PURPOSE_SUBAGENT`` is imported rather than retyped: if upstream renames the subagent,
    a literal ``"general-purpose"`` would silently stop overriding anything and the default spec
    would come back. ``tests/test_deep_agent.py`` asserts this, so a rename fails the suite instead.

    Takes the already-built tool list rather than building its own: ``memory_tools(actor)`` closures
    capture the request's actor and the MCP tools capture its credentials, so they must never be
    cached across requests.
    """
    delegable = [tool for tool in tools if tool.name not in gated]
    withheld = sorted(set(gated) & {tool.name for tool in tools})
    return [
        {
            **GENERAL_PURPOSE_SUBAGENT,
            "tools": delegable,
            # The stock description ends "This agent has access to all tools as the main agent",
            # which the line above makes untrue. That text lands in the `task` tool's description —
            # the only thing the main agent reads when deciding what to delegate — so leaving it
            # would invite it to hand off work this subagent cannot do, burning a hop to find out.
            "description": (
                f"{GENERAL_PURPOSE_SUBAGENT['description'].removesuffix(' This agent has access to all tools as the main agent.')}"
                f" This agent has the main agent's tools except {', '.join(withheld)}, which"
                " require human approval and must be called by the main agent itself."
                if withheld
                else GENERAL_PURPOSE_SUBAGENT["description"]
            ),
        }
    ]


async def create_agent_graph(
    actor: str,
    model: str | None = None,
    *,
    workspace_client_for: Callable[[str], WorkspaceClient] | None = None,
):
    """Build the Deep Agent: local tools + long-term-memory tools + any MCP tools.

    ``create_deep_agent`` is ``langchain.agents.create_agent`` plus three middlewares it installs
    unconditionally: planning (``write_todos``), a pluggable filesystem (``ls``/``read_file``/
    ``write_file``/``edit_file``/``glob``/``grep``), and subagent delegation (``task``). It returns
    the same ``CompiledStateGraph`` and streams the same modes, so ``run_agent``, ``recovery_input``
    and ``_validate_approval_resume`` are unchanged. This project showcases the *planning* piece —
    see ``agent/prompts.py`` — but the filesystem and ``task`` tools are present either way, because
    the harness offers no way to omit them.

    Two behaviours ``create_deep_agent`` adds to the graph config: ``recursion_limit`` 1000 (so a
    runaway loop burns 1000 steps rather than LangGraph's default 25) and a ``metadata`` entry. The
    metadata is *merged*, not replaced, so ``run_agent``'s ``invocation_metadata(invocation_id)``
    still reaches ``invocation_id_from_metadata`` during recovery.

    ``actor`` is the identity whose long-term memory the agent reads/writes; it's captured in the
    memory tools' closures (never exposed to the model).

    ``model`` selects the gateway model for this run; the chat UI passes the picker's choice and
    everything else falls back to ``MODEL``. The agent is rebuilt per turn, so the model can vary
    request to request.
    """
    mcp = await mcp_tools(build_mcp_servers(), workspace_client_for=workspace_client_for)
    memory = memory_tools(actor)
    genie = genie_tools(workspace_client_for=workspace_client_for)
    tools = [*all_tools(), *memory, *genie, *mcp]
    # Derived per request, because which MCP tools resolved is a per-request fact.
    gated = approval_gate(mcp)
    return create_deep_agent(
        model=_chat_model(model or MODEL),
        tools=tools,
        system_prompt=RESEARCH_PROMPT,
        subagents=_build_subagents(tools, gated),
        # Files live in LangGraph state, so they ride the managed Session Store checkpointer and
        # survive restarts, replica swaps, and approval pauses. A FilesystemBackend would write to
        # one replica's container and vanish on redeploy. StateBackend stores plain path->content
        # entries, so the contract's "state must be serializable by the selected saver" holds.
        backend=StateBackend,
        # `or None`: create_deep_agent branches on `interrupt_on is not None`, so an empty dict
        # would still install an inert HumanInTheLoopMiddleware. With no MCP write tool resolved
        # there is nothing to gate, and the middleware is correctly absent.
        interrupt_on=gated or None,
        checkpointer=checkpointer(),
    )


async def recovery_input(
    agent_input: Any,
    *,
    session_id: str,
    actor: str,
    invocation_id: str,
) -> Any:
    """Return the input that should be passed to ``run_agent`` after worker loss.

    A checkpoint tagged with this invocation means LangGraph can continue by receiving ``None``.
    Otherwise recovery replays the original application input.
    """
    checkpoint = await checkpointer().aget_tuple(thread_config(session_id, actor))
    current_invocation_checkpointed = bool(
        checkpoint and invocation_id_from_metadata(checkpoint.metadata) == invocation_id
    )
    return None if current_invocation_checkpointed else agent_input


async def _validate_approval_resume(graph: Any, config: dict, agent_input: Any) -> None:
    """Reject malformed middleware decisions before LangGraph checkpoints the resume value."""
    if not isinstance(agent_input, Command) or agent_input.resume is None:
        return
    state = await graph.aget_state(config)
    pending = [item for task in state.tasks for item in task.interrupts]
    resume = agent_input.resume
    keyed = isinstance(resume, dict) and any(item.id in resume for item in pending)
    for item in pending:
        if not isinstance(item.value, dict) or "review_configs" not in item.value:
            continue  # Application-defined interrupts keep their native resume contract.
        if keyed and item.id not in resume:
            continue
        response = resume[item.id] if keyed else resume
        try:
            decisions = TypeAdapter(HITLResponse).validate_python(response)["decisions"]
        except ValidationError as error:
            raise AuthError(
                "INVALID_APPROVAL", "Invalid approval decision payload.", 400
            ) from error
        reviews = item.value["review_configs"]
        if len(decisions) != len(reviews) or any(
            decision["type"] not in review["allowed_decisions"]
            for decision, review in zip(decisions, reviews, strict=True)
        ):
            raise AuthError(
                "INVALID_APPROVAL", "Provide one allowed decision per pending tool call.", 400
            )


async def run_agent(
    agent_input: Any,
    *,
    session_id: str,
    actor: str | None = None,
    model: str | None = None,
    invocation_id: str | None = None,
    workspace_client_for: Callable[[str], WorkspaceClient] | None = None,
    graph: Any = None,
) -> AsyncGenerator[Any, None]:
    """Run the agent and yield native LangGraph stream events.

    This is the framework-native entrypoint. It has no dependency on Agent Bricks request or context types,
    so it can be called from another server, a notebook, or a test harness.
    """
    # Each interactive resume builds tools from this request's credentials. Only graph state,
    # never the graph or its credential-bound clients, survives the approval pause.
    actor = actor or session_id
    if graph is None:
        graph = await create_agent_graph(actor, model, workspace_client_for=workspace_client_for)
    config = thread_config(session_id, actor)
    if invocation_id:
        config["metadata"] = invocation_metadata(invocation_id)

    await _validate_approval_resume(graph, config, agent_input)

    last_update: Any = None
    with start_trace(name="invoke", inputs=agent_input, session_id=session_id) as span:
        async for event in graph.astream(
            input=agent_input,
            config=config,
            stream_mode=["updates", "messages"],
            durability="sync",
        ):
            if event[0] == "updates":
                last_update = event[1]
            yield event
        if span is not None and last_update is not None:
            span.set_outputs(last_update)
