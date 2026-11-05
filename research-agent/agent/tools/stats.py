"""MLflow adoption figures, read from their primary sources at call time.

Every number this tool returns is verifiable: it comes from a named field of a public API, and the
return value carries the source URL and the UTC time of the read so anyone can re-run the call and
get the same answer.

**Nothing here is a constant, deliberately.** What this module replaced held six figures copied from
the tutorial notebook. Three were wrong when checked on 2026-11-05 — GitHub stars were reported as
"100K+" against an actual 28,694, and contributors as "1000+" against the 448 GitHub's API tracks.
A number baked into source gives a reader no way to tell a current figure from a stale one, which is
exactly how the stars claim survived.

Two whole categories were dropped rather than corrected, because neither can be made verifiable:

- **performance** (first-token latency) — varies by region, load, prompt length and streaming
  config. No canonical published number exists for any model, so there is no honest literal *and* no
  source to fetch.
- **cost** (per-token pricing) — no public, no-auth API. Pricing also moves: figures naming GPT-4o
  and an unversioned "Claude Sonnet" were two model generations stale, and this project defaults to
  ``system.ai.gpt-5-5`` anyway.

Do not re-add either as a constant. If a demo needs them, fetch them from a source the caller can
check, and carry that source in the return value as this tool does.

Sync rather than ``async def`` on purpose: a sync tool works on both LangGraph tool paths, while a
coroutine-only tool raises ``NotImplementedError`` under a sync ``graph.invoke``. ``BaseTool.ainvoke``
runs this in a thread, so the blocking call never holds the event loop.
"""

from datetime import datetime, timezone
from time import monotonic

import httpx
from langchain_core.tools import tool

_GITHUB_API = "https://api.github.com/repos/mlflow/mlflow"
_PYPI_API = "https://pypistats.org/api/packages/mlflow/recent"

# Both APIs are unauthenticated. GitHub allows 60 requests/hour per IP, so a repeated demo can
# exhaust it — that surfaces as a 403 and is reported as unverified, never as a guess.
_TIMEOUT = 10.0
_HEADERS = {"Accept": "application/json", "User-Agent": "research-agent (Agent Bricks demo)"}


# Deployed, every user of the app shares one egress IP, so GitHub's 60 requests/hour is a shared
# budget — and a single research run can call this tool more than once. A short TTL collapses those
# into one request without ever reporting a figure as fresher than it is: the cached read time rides
# along with the value and is what the tool prints. Only successes are cached, so a failure is
# retried rather than remembered. Per process, so replicas each keep their own.
_TTL_SECONDS = 300.0
_cache: dict[str, tuple[float, datetime, dict]] = {}


def _fetch_json(url: str) -> dict:
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        # trust_env defaults to True, so HTTPS_PROXY / NO_PROXY are honoured automatically. That is
        # what lets this work unchanged behind a Databricks Apps egress proxy.
        response = client.get(url, headers=_HEADERS)
        response.raise_for_status()
        return response.json()


def _read_json(url: str) -> tuple[datetime, dict]:
    """Return ``(read_at, payload)``, reusing a cached read for up to ``_TTL_SECONDS``."""
    now = monotonic()
    if (entry := _cache.get(url)) and now - entry[0] < _TTL_SECONDS:
        return entry[1], entry[2]
    payload = _fetch_json(url)
    read_at = datetime.now(timezone.utc)
    _cache[url] = (now, read_at, payload)
    return read_at, payload


@tool
def get_mlflow_adoption() -> str:
    """Get verified MLflow adoption figures: GitHub stars and monthly PyPI downloads.

    Reads each number from its primary API and reports the figure, its source URL, the field it
    came from, and when it was read. A reading is reused for up to five minutes, so the reported
    read time may be slightly behind now — quote the time as given rather than saying "currently". If a lookup fails it says so explicitly and returns
    the source URL instead — it never returns a stale, cached, or estimated number. Cite the sources
    when you use these figures, and do not substitute your own estimate for anything reported as
    unverified.
    """
    verified: list[str] = []
    unverified: list[str] = []

    try:
        read_at, payload = _read_json(_GITHUB_API)
        verified.append(
            f"GitHub stars: {payload['stargazers_count']:,} "
            f"— read {read_at.isoformat(timespec='seconds')} "
            f"(source: {_GITHUB_API}, field stargazers_count)"
        )
    except Exception as error:  # noqa: BLE001 - a failed lookup must not end the turn
        unverified.append(f"GitHub stars — {type(error).__name__}; check {_GITHUB_API}")

    try:
        read_at, payload = _read_json(_PYPI_API)
        verified.append(
            f"PyPI downloads, last 30 days: {payload['data']['last_month']:,} "
            f"— read {read_at.isoformat(timespec='seconds')} "
            f"(source: {_PYPI_API}, field data.last_month)"
        )
    except Exception as error:  # noqa: BLE001
        unverified.append(f"PyPI monthly downloads — {type(error).__name__}; check {_PYPI_API}")

    lines = ["MLflow adoption:"]
    lines.extend(verified)
    if unverified:
        lines.append("Could not verify this call (report as unavailable, do not estimate):")
        lines.extend(unverified)
    return "\n".join(lines)


# `@tool` takes no `handle_tool_error` kwarg, and ToolNode's default handler re-raises anything that
# is not a ToolInvocationError — so without this an unexpected exception would end the turn. The
# body already catches lookup failures; this covers everything else.
get_mlflow_adoption.handle_tool_error = (
    "The adoption lookup failed unexpectedly. No figures were verified — say so rather than "
    "estimating."
)
