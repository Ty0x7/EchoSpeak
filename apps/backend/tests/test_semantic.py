"""Search by meaning (agent/semantic.py) for tools and setup guides, with a keyword fallback."""
from __future__ import annotations

from types import SimpleNamespace

from agent import semantic


def test_without_the_local_model_nothing_changes(monkeypatch):
    import agent.embeddings as embeddings

    semantic.reset()
    monkeypatch.setattr(embeddings, "local_embeddings", lambda: (None, {"available": False}))
    try:
        assert semantic.similarities("make my video brighter", ["adjust lumetri"]) is None
        assert not semantic.available()
    finally:
        semantic.reset()


def test_vectors_are_cached_and_compared(monkeypatch):
    calls = []

    class Fake:
        def embed_documents(self, texts):
            calls.append(list(texts))
            table = {"brighter video": [1.0, 0.0], "colour and exposure": [0.8, 0.6], "send a message": [0.0, 1.0]}
            return [table[t] for t in texts]

    semantic.reset()
    monkeypatch.setattr(semantic, "_model", lambda: Fake())
    try:
        sims = semantic.similarities("brighter video", ["colour and exposure", "send a message"])
        assert [round(s, 2) for s in sims] == [0.8, 0.0]
        semantic.similarities("brighter video", ["colour and exposure"])
        assert len(calls) == 1  # the second search reuses every vector
    finally:
        semantic.reset()


def test_find_tools_finds_a_tool_by_meaning(monkeypatch):
    from agent.lean.toolbox import Toolbox
    from agent.tool_registry import ToolRegistry

    entries = {f"mcp__premiere__tool_{i}": SimpleNamespace(
        name=f"mcp__premiere__tool_{i}", origin="mcp", mcp_server="premiere", available=True, policy_flags=(),
        description=("Apply Lumetri colour correction to a clip" if i == 3 else f"Premiere operation {i}"),
        input_schema={"type": "object", "properties": {}}, func=None, is_action=True) for i in range(20)}
    monkeypatch.setattr(ToolRegistry, "get_all", classmethod(lambda cls: dict(entries)))
    monkeypatch.setattr(ToolRegistry, "get", classmethod(lambda cls, name: entries.get(name)))
    monkeypatch.setattr(ToolRegistry, "get_names", classmethod(lambda cls: list(entries)))
    # No shared word between "make my video brighter" and the Lumetri tool: only meaning links them.
    monkeypatch.setattr(semantic, "similarities",
                        lambda query, texts: [0.62 if "Lumetri" in t else 0.05 for t in texts])
    box = Toolbox(toolsets=["skills"])
    out = box.run("find_tools", {"query": "make my video brighter"}).output
    assert out.splitlines()[1].startswith("- mcp__premiere__tool_3")


def test_guides_match_a_request_that_names_no_app(monkeypatch):
    from agent import integrations

    monkeypatch.setattr(integrations, "_get_json", lambda url, **kw: {"servers": []})
    monkeypatch.setattr(semantic, "similarities",
                        lambda query, texts: [0.55 if t.startswith(("OBS", "Adobe Premiere")) else 0.1 for t in texts])
    ids = [c["id"] for c in integrations.search("cut highlights from last night's recording")["results"]]
    assert set(ids) == {"guide:obs", "guide:premiere"}
