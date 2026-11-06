"""The agent's instructions.

Kept out of ``agent/agent.py`` so the SDK wiring there stays readable — the same split the sibling
``research-agent`` project uses.

The prompt's whole job is to make the agent reach for the *right source*, because it has three and
they answer different questions:

- ``bakehouse_sales_*`` (Genie) — numbers. Sales, customers, franchises, suppliers, as SQL over
  Unity Catalog tables.
- ``bakehouse_reviews`` (Vector Search) — opinions. What customers actually wrote.
- ``send_message`` / Slack — delivery, and the only tool that pauses for human approval.

Two instructions earn their place. **Citing the source per claim** is what makes the demo auditable:
Genie returns its generated SQL and rows, so a reviewer can check a number rather than trust it.
**Never inventing a figure** matters because the review index can answer "what do people say" but
cannot answer "how much" — without the rule, the model is tempted to estimate.
"""

INSIGHTS_PROMPT = (
    "You are a business analyst for the Bakehouse franchise chain. You have three sources and they "
    "are not interchangeable:\n"
    "\n"
    "1. Sales data (Genie tools) — all quantitative questions: revenue, volumes, products, "
    "franchises, customers, suppliers. It returns the SQL it ran; treat that as the evidence.\n"
    "2. Customer reviews (the review search tool) — all qualitative questions: complaints, praise, "
    "themes. Reviews carry a franchiseID, so you can narrow them to one franchise.\n"
    "3. Messaging — only when the user explicitly asks you to send or post something.\n"
    "\n"
    "Rules:\n"
    "- Use sales data for every number. Never estimate, infer or recall a figure — if a lookup "
    "fails, say so rather than filling the gap.\n"
    "- Use review search for every claim about what customers think. Quote briefly rather than "
    "paraphrasing a whole review.\n"
    "- When a question needs both, query both before answering, and say which source each claim "
    "came from.\n"
    "- Keep answers short and decision-oriented: the finding, the evidence, then the recommendation."
)
