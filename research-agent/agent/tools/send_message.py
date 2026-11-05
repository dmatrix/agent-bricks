"""A local message-sending stub — the subject of the human-approval gate.

**It does not deliver anything.** It records the call and returns a string saying so. Its only job
is to give ``REQUIRE_APPROVAL`` in ``agent/agent.py`` something to gate, so the approve / edit /
reject flow is demonstrable without a managed MCP service that resolves. Point it at a real client
(Slack's Web API, an SMTP relay, a webhook) before anyone relies on a message arriving.

The return value says "no message was delivered" on purpose. A tool that claimed success would have
the model report a delivery that never happened, and the user has no way to tell the difference.

**If you later bind a managed MCP messaging service** (``system.ai.slack``, ``system.ai.gmail``,
``system.ai.microsoft_365``), delete this module in the same change. This tool previously existed,
and was removed, precisely because its description — "use when the user asks to notify or message
someone" — shadowed the real Slack MCP tool: the model picked this stub for every "post this to
Slack" request and silently sent nothing. ``tests/test_agent.py`` guards the one-sender rule.
"""

import logging

from langchain_core.tools import tool

_log = logging.getLogger(__name__)


@tool
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
