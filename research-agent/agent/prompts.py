"""System prompts for the research agent.

Kept out of ``agent/agent.py`` so the graph wiring there stays readable.

``RESEARCH_PROMPT`` is the system prompt from Example 1 of
https://github.com/dmatrix/mlflow-genai-tutorials/blob/main/11_deep_agents_langgraph.ipynb,
reproduced verbatim. ``create_deep_agent`` appends its own ~700-word base prompt (which documents
``write_todos``, the filesystem tools, and ``task``) after this one, so there is no need to explain
those tools here — only when to use them.

Nothing is added for output visibility, unlike the sibling ``fs-agent`` and ``multi-agent``
projects. Those write their deliverable to a file in the ``files`` state channel, which neither the
runtime adapter nor the chat transcript serializes, so their prompts must instruct the agent to echo
the document inline. Here the deliverable *is* the final assistant message, which the chat UI
renders directly.
"""

RESEARCH_PROMPT = (
    "You are a research assistant. When given a research topic:\n"
    "1. Use write_todos to plan your research steps\n"
    "2. Search the knowledge base or web if required for relevant information\n"
    "3. Gather supporting statistics\n"
    "4. Synthesize findings into a structured summary with sections: "
    "Overview, Key Findings, Statistics, and Conclusion"
)
