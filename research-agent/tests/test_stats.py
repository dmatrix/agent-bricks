"""Behavioural tests for the adoption tool — the first tests here that call a tool's body.

Every other test in this suite is structural: it checks which tools exist and what the compiled
graph looks like, never what a tool returns. This file is different because the tool's whole purpose
is a claim about its output — that every figure is sourced, and that a failed lookup is reported as
unverified rather than guessed. Only invoking it can check that.

Hermetic through one seam: `agent.tools.stats._fetch_json` is the single function that touches the
network, so monkeypatching it covers both APIs without an HTTP mocking library.
"""

import pytest
from agent.tools.stats import _GITHUB_API, _PYPI_API, get_mlflow_adoption

_GITHUB_PAYLOAD = {"stargazers_count": 28694}
_PYPI_PAYLOAD = {"data": {"last_month": 19887934}}


@pytest.fixture(autouse=True)
def _clear_cache():
    """Drop the module's TTL cache around every test.

    `_read_json` consults the cache *before* calling `_fetch_json`, so a reading left by another
    test would bypass the stub entirely and assert against the wrong data.
    """
    from agent.tools.stats import _cache

    _cache.clear()
    yield
    _cache.clear()


def _stub(monkeypatch, payloads: dict):
    """Serve canned JSON per URL; raise for any URL not in `payloads`."""

    def _fetch(url: str) -> dict:
        if url not in payloads:
            raise RuntimeError(f"lookup failed for {url}")
        return payloads[url]

    monkeypatch.setattr("agent.tools.stats._fetch_json", _fetch)


def test_both_figures_are_reported_with_their_sources(monkeypatch):
    _stub(monkeypatch, {_GITHUB_API: _GITHUB_PAYLOAD, _PYPI_API: _PYPI_PAYLOAD})
    result = get_mlflow_adoption.invoke({})

    # The figures, thousands-separated.
    assert "28,694" in result
    assert "19,887,934" in result
    # Each one carries the URL and the field it came from, so a reader can re-run the call. This is
    # the whole point of the tool: a number without its provenance is what this module replaced.
    assert _GITHUB_API in result
    assert _PYPI_API in result
    assert "stargazers_count" in result
    assert "data.last_month" in result
    assert "— read " in result
    assert "Could not verify" not in result


def test_a_failed_lookup_is_reported_not_estimated(monkeypatch):
    # GitHub unavailable (a 403 from the 60/hour unauthenticated limit looks like this), PyPI fine.
    _stub(monkeypatch, {_PYPI_API: _PYPI_PAYLOAD})
    result = get_mlflow_adoption.invoke({})

    # The half that worked is still reported...
    assert "19,887,934" in result
    # ...and the half that did not is named as unverified, with its source, and no number.
    assert "Could not verify" in result
    assert "GitHub stars" in result
    assert "28,694" not in result
    assert "100K" not in result


def test_total_failure_still_returns_a_usable_answer(monkeypatch):
    # Both lookups down: the tool must not raise, because an exception from a tool body ends the
    # turn (ToolNode's default handler re-raises anything that is not a ToolInvocationError).
    _stub(monkeypatch, {})
    result = get_mlflow_adoption.invoke({})

    assert "Could not verify" in result
    assert _GITHUB_API in result
    assert _PYPI_API in result
    # No figure is reported at all: a verified figure is the only thing that emits a "source:" line.
    # (The read-at timestamp contains digits, so counting digits would not express this.)
    assert "source:" not in result
    assert "28,694" not in result and "19,887,934" not in result


def test_no_fabricated_figure_survives_anywhere_in_the_module():
    # Regression guard for the three wrong numbers this module used to hardcode, plus the two
    # categories that were dropped as unverifiable. If any reappears as a constant, this fails.
    import agent.tools.stats as stats

    source = __import__("inspect").getsource(stats)
    body = source.split('"""', 2)[-1]  # skip the module docstring, which documents the removals
    for banned in ("100K+", "1000+ contributors", "20M+", "250ms", "200ms", "$2.50", "$3/1M"):
        assert banned not in body, f"fabricated figure reintroduced: {banned}"


def test_a_reading_is_reused_within_the_ttl(monkeypatch):
    # Deployed, every user shares one egress IP against GitHub's 60 requests/hour, and one research
    # run can call this tool more than once. The second call must not hit the network.
    calls: list[str] = []

    def _counting(url: str) -> dict:
        calls.append(url)
        return _GITHUB_PAYLOAD if url == _GITHUB_API else _PYPI_PAYLOAD

    monkeypatch.setattr("agent.tools.stats._fetch_json", _counting)
    first = get_mlflow_adoption.invoke({})
    second = get_mlflow_adoption.invoke({})

    assert len(calls) == 2, "expected exactly one request per source, then cache hits"
    # And the reused reading reports its original time, never a fresher one — that is what keeps a
    # cached value honest.
    assert first == second


def test_an_expired_reading_is_refetched(monkeypatch):
    calls: list[str] = []

    def _counting(url: str) -> dict:
        calls.append(url)
        return _GITHUB_PAYLOAD if url == _GITHUB_API else _PYPI_PAYLOAD

    monkeypatch.setattr("agent.tools.stats._fetch_json", _counting)
    monkeypatch.setattr("agent.tools.stats._TTL_SECONDS", -1.0)  # everything is already stale
    get_mlflow_adoption.invoke({})
    get_mlflow_adoption.invoke({})

    assert len(calls) == 4, "an expired reading must be fetched again"


def test_a_failure_is_not_cached(monkeypatch):
    # Caching a failure would make one transient blip persist for the whole TTL.
    _stub(monkeypatch, {})
    assert "Could not verify" in get_mlflow_adoption.invoke({})

    _stub(monkeypatch, {_GITHUB_API: _GITHUB_PAYLOAD, _PYPI_API: _PYPI_PAYLOAD})
    recovered = get_mlflow_adoption.invoke({})
    assert "28,694" in recovered and "Could not verify" not in recovered


def test_the_tool_takes_no_arguments():
    # It replaced `get_latest_stats(category)`. A single-category enum would have been pointless,
    # and a free-form `str` category is what returned "No stats available for: X" on a typo.
    assert get_mlflow_adoption.args == {}


def test_an_unexpected_error_cannot_end_the_turn():
    # `@tool` accepts no handle_tool_error kwarg, so this is set on the tool object after creation.
    # Without it, anything the body does not catch would propagate out of the graph.
    assert get_mlflow_adoption.handle_tool_error


@pytest.mark.parametrize("missing", [_GITHUB_API, _PYPI_API])
def test_each_source_fails_independently(monkeypatch, missing):
    payloads = {_GITHUB_API: _GITHUB_PAYLOAD, _PYPI_API: _PYPI_PAYLOAD}
    del payloads[missing]
    _stub(monkeypatch, payloads)
    result = get_mlflow_adoption.invoke({})

    assert "Could not verify" in result
    assert missing in result
    # The surviving source still reports its figure.
    assert len([ln for ln in result.splitlines() if "source:" in ln]) == 1
