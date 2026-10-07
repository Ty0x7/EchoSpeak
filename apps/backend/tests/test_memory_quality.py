"""Memory quality: what gets saved, how duplicates and changes are handled, and what reaches the prompt."""

from agent.lean import memory_quality as mq
from agent.lean.prompt import build_system_prompt
from agent.lean.personas import AgentPersona
from agent.memory import AgentMemory


def test_facts_are_tidied_to_one_clean_sentence():
    assert mq.tidy_fact("  remember that   my dog is called Biscuit ") == "My dog is called Biscuit."
    assert mq.tidy_fact("Note: prefers short answers") == "Prefers short answers."
    assert mq.tidy_fact("") == ""


def test_only_lasting_single_facts_are_kept():
    assert mq.check_fact("Prefers short answers.") is None
    assert "too long" in mq.check_fact("Likes " + "very " * 80 + "long things.")
    assert "question" in mq.check_fact("What is my dog called?")
    assert "only matters now" in mq.check_fact("Remind me to call mum at 5.")
    assert "only matters now" in mq.check_fact("Is busy right now.")


def test_rewordings_count_as_the_same_fact():
    assert mq.same_fact("The user's dog is called Biscuit.", "The user's dog is named Biscuit.")
    assert mq.same_fact("Prefers short, direct answers.", "Prefers direct, short answers.")
    assert not mq.same_fact("The user's dog is called Biscuit.", "The user's cat is called Biscuit and is old.")
    records = [
        {"text": "Lives in Leeds.", "active": False},
        {"text": "User: hi AI: hello", "memory_type": "conversation"},
        {"text": "Works night shifts at the hospital.", "active": True},
    ]
    assert mq.find_duplicate("Works night shifts at the hospital", records)["text"].startswith("Works night")
    assert mq.find_duplicate("Lives in Leeds.", records) is None  # inactive memories don't count


def test_keys_are_normalised():
    assert mq.normalize_key("Home City") == "home_city"
    assert mq.normalize_key("  ") == ""


def test_prompt_section_is_compact_pinned_first_and_capped():
    memories = [
        {"content": "The user works night shifts.", "pinned": False},
        {"content": "The user's name is Ty.", "pinned": True},
        {"content": "User: hi\nAI: hello", "type": "conversation"},
        {"content": "The user works night-shifts.", "pinned": False},
    ] + [{"content": f"Fact number {i} about something different entirely here.", "pinned": False} for i in range(30)]
    section = mq.render(memories)
    lines = [line for line in section.splitlines() if line.startswith("- ")]
    assert lines[0] == "- Their name is Ty."
    assert "- Works night shifts." in lines
    assert sum("night" in line for line in lines) == 1  # the near-duplicate is folded away
    assert not any("AI: hello" in line for line in lines)
    assert len(lines) <= mq.PROMPT_ROWS
    prompt = build_system_prompt(persona=AgentPersona(id="echo", name="Echo"), soul_text="", memories=memories[:2])
    assert "## What you know about the user" in prompt and "Their name is Ty." in prompt


def test_recall_leaves_out_transcripts_and_caps_pinned():
    class Store:
        PINNED_ALWAYS = 2
        _records = {
            "c": {"id": "c", "text": "User: my dog\nAI: nice dog", "memory_type": "conversation", "active": True,
                  "owner_id": "local-owner", "scope": "account", "metadata": {}},
            **{f"p{i}": {"id": f"p{i}", "text": f"Pinned fact {i} about gardening.", "active": True, "owner_id": "local-owner",
                         "scope": "account", "metadata": {"pinned": True}} for i in range(4)},
            "d": {"id": "d", "text": "The user's dog is called Biscuit.", "active": True, "owner_id": "local-owner",
                  "scope": "account", "metadata": {}},
        }
        _owner_id = AgentMemory._owner_id

    rows = AgentMemory.runtime_memory_projection.__wrapped__(Store(), "what is my dog called?", session_id="s")
    ids = [row["memory_id"] for row in rows]
    assert "c" not in ids and "d" in ids
    assert sum(i.startswith("p") for i in ids) == 2
