"""file_edit recovery for small models, and the focused toolbox for builders on local models.

The fixture is the block Glados (gemma-4-e4b) failed to edit four times on 2026-10-20: it
retyped a 40-line block, dropping a trailing space and "fixing" a 13-space indent.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.lean import coding
from agent.lean.coding import file_edit
from agent.lean.personas import AgentPersona
from agent.lean.runtime import LeanSession
from agent.lean.toolbox import BUILDER_FOCUS, NativeTool, Toolbox
from agent.tools import _tool_execution_context

GAME = (
    "function update(deltaTime) {\n"
    "    // Normalize diagonal movement speed\n"
    "    velocity.normalize(); \n"
    "\n"
    "    // 2. Dog State and Stress Accumulation (t2 implementation)\n"
    "    if (dog.state === 'CHAOS') {\n"
    "        stressLevel += dog.stressIncreaseRate * deltaTime * 60;\n"
    "    } else if (dog.state === 'CALM') {\n"
    "        // Minor stress decay when calm\n"
    "        if (stressLevel > 0) {\n"
    "             stressLevel -= 0.01 * deltaTime * 60;\n"
    "        }\n"
    "    }\n"
    "}\n"
)


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "Game"
    root.mkdir()
    (root / "main.js").write_text(GAME, encoding="utf-8")
    coding._MISSES.clear()
    token = _tool_execution_context.set({"strict_scope": True, "project_root": str(root), "lean_runtime": True})
    yield root / "main.js"
    _tool_execution_context.reset(token)


def test_a_retyped_block_with_tidied_spacing_still_applies(game):
    # What a model retypes: the trailing space gone, 13 spaces "fixed" to 12.
    old = (
        "    velocity.normalize();\n"
        "\n"
        "    // 2. Dog State and Stress Accumulation (t2 implementation)\n"
        "    if (dog.state === 'CHAOS') {\n"
        "        stressLevel += dog.stressIncreaseRate * deltaTime * 60;\n"
        "    } else if (dog.state === 'CALM') {\n"
        "        // Minor stress decay when calm\n"
        "        if (stressLevel > 0) {\n"
        "            stressLevel -= 0.01 * deltaTime * 60;\n"
        "        }\n"
        "    }"
    )
    new = old.replace("stressLevel > 0", "stressLevel > 0 && !dog.calmTimer")
    out = file_edit({"path": "main.js", "old_text": old, "new_text": new})
    assert out.startswith("Edited"), out
    assert "ignoring" in out and "+        if (stressLevel > 0 && !dog.calmTimer) {" in out
    text = game.read_text(encoding="utf-8")
    assert "if (stressLevel > 0 && !dog.calmTimer) {" in text
    assert text.startswith("function update(deltaTime) {\n    // Normalize") and text.endswith("    }\n}\n")


def test_new_text_moves_to_the_file_indentation(game):
    out = file_edit({"path": "main.js",
                     "old_text": "if (dog.state === 'CHAOS') {\n    stressLevel += dog.stressIncreaseRate * deltaTime * 60;",
                     "new_text": "if (dog.state === 'CHAOS' && !dog.calmTimer) {\n    stressLevel += dog.stressIncreaseRate * deltaTime * 90;"})
    assert out.startswith("Edited"), out
    text = game.read_text(encoding="utf-8")
    assert "\n    if (dog.state === 'CHAOS' && !dog.calmTimer) {\n        stressLevel += dog.stressIncreaseRate * deltaTime * 90;\n" in text


def test_typographic_quotes_and_dashes_match_plain_ones(game):
    game.write_text("const title = “Dawn — day one”;\nconst next = 1;\n", encoding="utf-8")
    out = file_edit({"path": "main.js", "old_text": 'const title = "Dawn - day one";',
                     "new_text": 'const title = "Dawn - day two";'})
    assert out.startswith("Edited") and "quote or dash" in out, out
    assert game.read_text(encoding="utf-8") == 'const title = "Dawn - day two";\nconst next = 1;\n'


def test_a_loose_match_in_two_places_is_refused(game):
    game.write_text("a {\n  x();\n}\nb {\n    x();\n}\n", encoding="utf-8")
    out = file_edit({"path": "main.js", "old_text": "x();  ", "new_text": "y();"})
    assert "matches 2 places" in out and "ignoring indentation" in out
    assert game.read_text(encoding="utf-8") == "a {\n  x();\n}\nb {\n    x();\n}\n"


def test_crlf_files_keep_their_line_endings(game):
    game.write_bytes(GAME.replace("\n", "\r\n").encode("utf-8"))
    out = file_edit({"path": "main.js", "old_text": "            stressLevel -= 0.01 * deltaTime * 60;",
                     "new_text": "            stressLevel -= 0.02 * deltaTime * 60;"})
    assert out.startswith("Edited"), out
    data = game.read_bytes().decode("utf-8")
    assert "\r\n             stressLevel -= 0.02 * deltaTime * 60;\r\n" in data and "\n" not in data.replace("\r\n", "")


def test_a_real_miss_shows_the_first_differing_line_with_its_spacing(game):
    old = "    } else if (dog.state === 'CALM') {\n        // Small stress decay when calm\n"
    out = file_edit({"path": "main.js", "old_text": old, "new_text": "x"})
    assert out.startswith("Error: old_text was not found in main.js."), out
    assert "closest match starts at line 8" in out and "first difference is at line 9" in out
    assert "file: ········// Minor stress decay when calm" in out
    assert "yours: ········// Small stress decay when calm" in out
    assert "file_read offset=5" in out and "2–5 lines" in out
    assert game.read_text(encoding="utf-8") == GAME


def test_a_first_line_that_is_nowhere_says_to_search(game):
    out = file_edit({"path": "main.js", "old_text": "renderer.render(scene, camera);", "new_text": "x"})
    assert "isn't in the file" in out and "file_search" in out


def test_after_two_misses_a_short_file_can_be_rewritten_whole(game):
    miss = {"path": "main.js", "old_text": "    } else if (dog.state === 'NAP') {", "new_text": "x"}
    assert "file_write" not in file_edit(miss)
    second = file_edit(miss)
    assert "missed 2 times" in second and "file_write" in second
    assert file_edit({"path": "main.js", "old_text": "// Minor stress decay when calm",
                      "new_text": "// Stress decays when calm"}).startswith("Edited")
    assert "file_write" not in file_edit(miss)  # a success resets the count


def test_the_tool_description_teaches_small_exact_edits():
    tool = next(t for t in coding.coding_tools() if t.name == "file_edit")
    assert "2-5 lines" in tool.description and "indentation" in tool.description and "offset/limit" in tool.description


# ── focused toolbox ─────────────────────────────────────────────────────────
def _tools(*names: str, always: tuple[str, ...] = ()) -> list[NativeTool]:
    schema = {"type": "object", "properties": {}}
    return [NativeTool(name, f"{name.replace('_', ' ')} tool", schema, lambda a, n=name: f"{n} ran", always=name in always)
            for name in names]


def _focused() -> Toolbox:
    names = ("file_read", "file_edit", "terminal", "web_search", "memory_save", "create_artifact", "complete_task")
    box = Toolbox(toolsets=["none"], extra_tools=_tools(*names, always=("complete_task",)))
    box.native = {t.name: t for t in _tools(*names, always=("complete_task",))}
    box.focus(BUILDER_FOCUS)
    return box


def test_focus_lists_only_builder_tools_and_defers_the_rest():
    box = _focused()
    listed = {s["function"]["name"] for s in box.schemas()}
    assert listed == {"file_read", "file_edit", "terminal", "complete_task", "find_tools"}
    assert {"web_search", "memory_save", "create_artifact"} <= box.deferred
    assert set(box.names) >= {"web_search", "memory_save"}  # still callable
    assert any("find_tools" in note for note in box.notes)


def test_find_tools_loads_a_held_builtin_tool():
    box = _focused()
    out = box.run("find_tools", {"query": "web search"}).output
    assert "web_search" in out and "web_search" not in box.deferred
    assert "web_search" in {s["function"]["name"] for s in box.schemas()}


def test_a_held_tool_called_by_name_runs_and_stays_listed():
    box = _focused()
    version = box.schema_version
    result = box.run("memory_save", {})
    assert result.ok and result.output == "memory_save ran"
    assert "memory_save" not in box.deferred and box.schema_version > version


@pytest.mark.parametrize("persona, provider, focused", [
    (AgentPersona(id="forge", name="Glados", title="Builder"), "lmstudio", True),
    (AgentPersona(id="forge", name="Glados", title="Builder"), "anthropic", False),
    (AgentPersona(id="maker", name="Bob", title="Game builder"), "ollama", True),
    (AgentPersona(id="echo", name="Echo", title="Personal agent"), "lmstudio", False),
])
def test_only_builders_on_local_models_are_focused(persona, provider, focused):
    session = SimpleNamespace(_endpoint_for=lambda p: SimpleNamespace(provider=provider))
    assert LeanSession._small_builder(session, persona) is focused


def test_other_agents_on_local_models_get_the_everyday_chat_focus(monkeypatch):
    """Review R12: a local model in ordinary chat gets everyday tools up front; cloud models keep every schema."""
    import threading

    from agent.lean import runtime
    from agent.lean.personas import get_persona_store
    from agent.lean.provider import Endpoint
    from agent.lean.toolbox import CHAT_FOCUS

    monkeypatch.setattr(runtime.LeanSession, "_recall", lambda *a, **k: [])
    sizes = {}
    for provider, local in (("lmstudio", True), ("openai", False)):
        monkeypatch.setattr(runtime.LeanSession, "_endpoint_for", lambda *a, provider=provider, local=local, **k: Endpoint(
            provider=provider, model="m", base_url="http://127.0.0.1:1", api_key="", local=local))
        session = runtime.LeanSession(agent=SimpleNamespace(memory=None), session_id=f"focus-{provider}", request_id="r",
                                      emit=lambda _: None, cancel=threading.Event(), source="web")
        box = session._build_turn(get_persona_store().get("echo"), "What's the capital of Peru?", history=[], depth=0).toolbox
        sent = {s["function"]["name"] for s in box.schemas()}
        sizes[provider] = len(sent)
        if local:
            always = {n for n, t in box.native.items() if t.always or t.hidden}
            assert sent <= CHAT_FOCUS | always | {"find_tools"} and "find_tools" in sent
            assert box.deferred and all(box.entry(n) or n in box.native for n in box.deferred)  # still callable by name
    assert sizes["lmstudio"] < sizes["openai"]
