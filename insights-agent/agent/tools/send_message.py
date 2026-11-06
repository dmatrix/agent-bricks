"""A side-effecting sample tool, gated by human approval.

Unlike ``get_current_time`` (a harmless read), this tool stands in for an action with real
consequences: sending a message. ``needs_approval=True`` makes the Agents SDK pause before it runs
and surface a pending approval; the runtime relays that as an ``interrupt`` event and resumes on an
approve/reject decision. Its name is also listed in ``REQUIRE_APPROVAL`` in ``agent/agent.py``,
which is what gates CLI-declared MCP tools of the same name.

**It delivers nothing.** It logs the call and returns a string that says so. The return value is
deliberately not phrased as a success: a stub that returns "Message sent" gets relayed by the model
as a completed send, and the user has no way to tell the difference. Point it at a real client
(Slack's Web API, an SMTP relay, a webhook) before anyone relies on a message arriving.

**Delete this module in the same change that binds a managed MCP messaging service**
(``system.ai.slack``, ``system.ai.gmail``, ``system.ai.microsoft_365``). The sibling
``research-agent`` project had to remove its equivalent stub for exactly this reason: a broad
description like "use when the user asks to notify or message someone" out-competed the real Slack
MCP tool, so the model picked the stub for every send and reported deliveries that never happened.
The description below is deliberately narrow to reduce that pull, but narrowing is a mitigation, not
a fix. Add the real write tool's name to ``REQUIRE_APPROVAL`` at the same time, because this
framework gates approval by tool *name*, not by verb.
"""

import logging

from agents import function_tool

_log = logging.getLogger(__name__)


@function_tool(needs_approval=True)
def send_message(recipient: str, body: str) -> str:
    """Draft a message to a recipient for human review before it is sent.

    Use this only when the user explicitly asks to message, DM, or notify a named person or channel.
    ``recipient`` is a person, @handle, or #channel; ``body`` is the message text. The call pauses
    for human approval, and the reviewer may edit the recipient or body before allowing it.
    """
    _log.info("send_message to=%r body=%r", recipient, body)
    return (
        f"Recorded a message to {recipient}: {body!r}. "
        "No message was delivered — send_message is a local stub with no messaging backend."
    )
