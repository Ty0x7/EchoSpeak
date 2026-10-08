"""Live steps: progress lines, past-tense labels and summaries, and web_search reading its top results."""

from types import SimpleNamespace

from agent.lean import progress, steps
from agent.lean.personas import AgentPersona
from agent.lean.prompt import build_system_prompt
from agent.lean.toolbox import Toolbox, describe_call


def test_labels_switch_to_past_tense():
    assert steps.done_label("Searching “budget mic”") == "Searched “budget mic”"
    assert steps.done_label("Shopping for “mic”") == "Checked prices for “mic”"
    assert steps.done_label("Reading rtings.com/mic") == "Read rtings.com/mic"
    assert steps.done_label("Running `pytest -q`") == "Ran `pytest -q`"
    assert steps.done_label("Searching code for “todo”") == "Searched code for “todo”"
    assert steps.done_label("Stock prices for AAPL") == "Stock prices for AAPL"


def test_page_rows_show_the_site_not_the_whole_url():
    assert describe_call("safe_web_fetch", {"url": "https://www.rtings.com/microphone/reviews/best"}) == "Reading rtings.com/microphone/reviews/best"


def test_summaries_come_from_the_result():
    assert steps.summarize("web_search", {}, True, "", {"results": 14, "pages": 2}) == "14 results · read 2 pages"
    assert steps.summarize("web_search", {}, True, "", {"results": 1}) == "1 result"
    assert steps.summarize("terminal", {}, True, "===== 12 passed in 0.4s =====\n[exit code 0]") == "Tests: 12 passed"
    assert steps.summarize("terminal", {}, True, "built ok\n[exit code 0]") == "Done · built ok"
    assert steps.summarize("terminal", {}, True, "boom\n[exit code 2]") == "Exit code 2 · boom"
    assert steps.summarize("file_write", {"content": "a\nb\nc"}, True, "Wrote x") == "+3 lines"
    assert steps.summarize("file_edit", {"old_text": "a", "new_text": "a\nb"}, True, "ok") == "+2 −1 lines"
    assert steps.summarize("safe_web_fetch", {}, True, "title: Mics\nOne two three four.") == "Read 4 words"
    assert steps.summarize("product_search", {}, True, "", {"products": 6}) == "6 products"
    assert steps.summarize("web_search", {}, False, "Error: provider down") == "Failed: provider down"


def test_reading_defaults_to_comparisons_and_prices():
    assert steps.default_reads("best budget mic for streaming") == 2
    assert steps.default_reads("rtx 4070 vs 7800 xt") == 2
    assert steps.default_reads("edmonton population") == 0
    assert steps.result_urls("1. A\n   URL: https://a.com\n2. B\n   URL: https://b.com\n3. A\n   URL: https://a.com") == ["https://a.com", "https://b.com"]


def test_progress_reports_are_throttled_and_the_last_one_is_kept():
    seen = []
    progress.report("nobody listening")  # outside a scope: ignored
    with progress.reporting(seen.append):
        progress.report("Searching")
        progress.report("Searching")
        progress.report("Reading a.com")
        progress.report("Reading b.com")
    assert seen[0] == "Searching"
    assert seen[-1] == "Reading b.com"
    assert "nobody listening" not in seen


def _box(search_output: str):
    box = Toolbox(toolsets=["none"], session_id="t")
    fetched = []

    def fake_search(query: str = "") -> str:
        return search_output

    def fake_fetch(url: str = "", objective: str = "", max_text_chars: int = 0) -> str:
        fetched.append(url)
        return f"title: page\nText from {url}."

    box.entries["web_search"] = SimpleNamespace(func=fake_search, description="search")
    box.entries["safe_web_fetch"] = SimpleNamespace(func=fake_fetch, description="fetch")
    return box, fetched


RESULTS = "Search provider: ddg\n\n1. One\n   URL: https://one.com/a\n\n2. Two\n   URL: https://two.com/b\n\n3. Three\n   URL: https://three.com/c"


def test_search_reads_the_top_results_for_comparisons_and_reports_progress():
    box, fetched = _box(RESULTS)
    lines = []
    with progress.reporting(lines.append):
        result = box.run("web_search", {"query": "best budget mic"})
    assert result.ok
    assert fetched == ["https://one.com/a", "https://two.com/b"]
    assert result.meta == {"results": 3, "pages": 2}
    assert "## Pages read from the top results" in result.output and "Text from https://two.com/b." in result.output
    assert any(line.startswith("Reading two.com/b") for line in lines)


def test_plain_lookups_only_search_unless_asked():
    box, fetched = _box(RESULTS)
    result = box.run("web_search", {"query": "edmonton population"})
    assert fetched == [] and result.meta == {"results": 3}
    box, fetched = _box(RESULTS)
    box.run("web_search", {"query": "edmonton population", "read": 1})
    assert fetched == ["https://one.com/a"]


def test_search_schema_offers_read_and_the_prompt_asks_for_sources_and_a_heads_up():
    box, _ = _box(RESULTS)
    schema = next(s for s in box.schemas() if s["function"]["name"] == "web_search")
    assert "read" in schema["function"]["parameters"]["properties"]
    prompt = build_system_prompt(persona=AgentPersona(id="echo", name="Echo"), soul_text="")
    assert "Never compare from snippets alone." in prompt
    assert "open with one short sentence on what you're about to do" in prompt
