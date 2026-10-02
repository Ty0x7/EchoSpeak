"""Research malformed tool calls get one bounded structured retry."""

from __future__ import annotations

from types import SimpleNamespace


def test_web_search_args_accept_aliases():
    from agent.tools import WebSearchArgs

    m = WebSearchArgs.model_validate({"q": "Edmonton Oilers founded"})
    assert m.query == "Edmonton Oilers founded"
    m2 = WebSearchArgs.model_validate(
        {"query": "local-first AI", "objective": "summary", "local_first": False, "freshness": "latest"}
    )
    assert m2.objective == "summary"
    assert m2.freshness == "latest"


