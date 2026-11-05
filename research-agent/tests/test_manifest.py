"""The manifest declares the MCP services a deploy is expected to carry.

`tests/conftest.py` stubs MCP discovery so the suite stays offline, which means no other test would
notice if these bindings disappeared from `agent.toml`. This file is that guard: the bindings are
the whole mechanism by which a deployed agent gets these tools, and there is no manual step at
deploy time to fall back on.

Service names only. The auth mode is a separate decision — `user` puts the call on the signed-in
user, `app` on the service principal — and switching it should not fail a test about *which*
services are bound.

`system.ai.slack` was bound here and is not any more. It could not resolve under `auth = "app"`
(built-in SaaS MCP services need a per-user OAuth sign-in a service principal cannot complete) and
`auth = "user"` makes a discovery failure fatal, so no graph builds locally. The local
`send_message` stub carries the approval-gate demo instead — see
`test_no_mcp_messaging_service_shadows_the_local_sender`.
"""

import pathlib
import tomllib

EXPECTED_MCP_SERVICES = {
    "system.ai.web_search",
}

# Binding any of these while `agent/tools/send_message.py` exists recreates the shadowing bug that
# got the stub deleted the first time: the model picks the local tool, which delivers nothing.
MESSAGING_MCP_SERVICES = {
    "system.ai.slack",
    "system.ai.gmail",
    "system.ai.microsoft_365",
}

_MANIFEST = pathlib.Path(__file__).resolve().parent.parent / "agent.toml"


def _manifest() -> dict:
    return tomllib.loads(_MANIFEST.read_text())


def test_manifest_parses():
    # `agentbricks tools add` has been seen to emit invalid TOML when a stray top-level `tools` key
    # is present, which silently breaks every later CLI read. Fail here rather than at deploy.
    assert _manifest()["schema_version"] == 1


def test_declared_mcp_services_are_the_ones_a_deploy_needs():
    bound = {
        tool["source"]["service"]
        for tool in _manifest().get("tools", [])
        if tool.get("source", {}).get("kind") == "mcp"
    }
    assert EXPECTED_MCP_SERVICES <= bound


def test_no_mcp_messaging_service_shadows_the_local_sender():
    # The one-sender rule. `send_message`'s description ("use when the user asks to message, DM, or
    # notify") beat the real Slack MCP tool every time, so the agent reported sends that never
    # happened. Bind a messaging service and delete the stub in the same change, not separately.
    from agent.tools import all_tools

    bound = {
        tool["source"]["service"]
        for tool in _manifest().get("tools", [])
        if tool.get("source", {}).get("kind") == "mcp"
    }
    if "send_message" in {tool.name for tool in all_tools()}:
        assert not (bound & MESSAGING_MCP_SERVICES), (
            "a managed MCP messaging service is bound alongside the local send_message stub; "
            "delete agent/tools/send_message.py and drop send_message from REQUIRE_APPROVAL"
        )


def test_declared_tools_are_well_formed():
    for tool in _manifest().get("tools", []):
        assert tool.get("id")
        assert tool.get("auth") in {"user", "app"}
        assert tool.get("source", {}).get("kind")
