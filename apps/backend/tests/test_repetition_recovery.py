"""Repeated visible prose from a local model (#160), replayed in the shapes of the two recorded Gemma 4 12B failures.

Both recorded responses (21,248 and 8,347 characters, 594 s and 317 s, temperature 0.7) open with deliberation
that drifts without repeating, then settle into a strict cycle of a few paragraphs until the output limit. The
fixtures below keep that shape with synthetic wording: a drifting preamble, then the cycle. Each is served as
Ollama-style SSE at about three characters per chunk, the recorded token granularity.

Measured against the recorded responses themselves (evaluations/2026-11-24-issue-160-targeted/): detection at
25.7 % and 69.8 % of the stream, about 6,086 and 1,121 completion tokens never generated, zero detections in
2,201 other saved responses, and a final answer of 176 characters instead of the whole loop.
"""
from __future__ import annotations

import json
import threading

import httpx
import pytest

from agent.lean.provider import ChatClient, Endpoint
from tests.test_lean_runtime import _toolbox, _turn

DRIFT = "".join(
    f"Actually, option {i} could work if the helper keeps its current signature and the tests stay as written.\n\n"
    f"Wait, case {i} changes the order of checks, so I should look at `step_{i}()` before deciding.\n\n"
    for i in range(14)
)
SHAPE_LONG = DRIFT + (
    "Actually, I'll try to run it and see if I can get the output of the assertion.\n\n"
    "I'll try `python3 -m unittest test_calc.py` to see if it gives more info.\n\n"
    "Actually, I'll just fix it.\n\n"
) * 120
SHAPE_DENSE = DRIFT[:1800] + (
    "I'll start.\nI'll do `word_count` first.\nI'll use `re.findall(r\"[a-zA-Z0-9']+\", text)`.\n\n"
    "Wait, I'll just use `re.findall(r\"[a-zA-Z0-9']+\", text)`.\n\n"
    "Actually, I'll just use `re.findall(r\"[a-zA-Z0-9']+\", text)`.\n\n"
) * 30
RECOVERY = "word_count.py is open. It splits on spaces, so apostrophes and punctuation are miscounted."


def _packet(delta, finish=None):
    return ("data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}) + "\n\n").encode()


class _Stream(httpx.SyncByteStream):
    def __init__(self, text: str = "", reasoning: str = "", finish: str = "length", chunk: int = 3):
        self.text, self.reasoning, self.finish, self.chunk = text, reasoning, finish, chunk
        self.served = 0
        self.closed = False

    def __iter__(self):
        for i in range(0, len(self.reasoning), self.chunk):
            yield _packet({"reasoning_content": self.reasoning[i:i + self.chunk]})
        for i in range(0, len(self.text), self.chunk):
            self.served = i + self.chunk
            yield _packet({"content": self.text[i:i + self.chunk]})
        self.served = len(self.text)
        yield _packet({}, self.finish)

    def close(self):
        self.closed = True


def _client(respond) -> ChatClient:
    client = ChatClient(Endpoint("http://127.0.0.1:11434/v1", "", "fixture", "ollama", True))
    client._http.close()
    client._http = httpx.Client(base_url=client.endpoint.base_url, transport=httpx.MockTransport(respond))
    return client


@pytest.mark.parametrize("text", [SHAPE_LONG, SHAPE_DENSE], ids=["long-cycle", "dense-cycle"])
def test_recorded_shapes_are_detected_early_and_the_stream_is_closed(text):
    stream = _Stream(text)
    turn = _client(lambda _: httpx.Response(200, stream=stream)).stream_turn(
        [{"role": "user", "content": "x"}], on_content=lambda _: None, cancel=threading.Event())
    assert turn.finish_reason == "repeated_prose"
    assert stream.closed
    assert stream.served < 0.75 * len(text)  # what the server never had to generate
    assert len(DRIFT) < stream.served  # the drifting preamble alone is never enough


@pytest.mark.parametrize("text", [SHAPE_LONG, SHAPE_DENSE], ids=["long-cycle", "dense-cycle"])
def test_the_loop_is_retracted_and_the_retry_keeps_the_tool_results(text):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(200, content=_packet({"tool_calls": [{"id": "r1", "index": 0, "function": {
                "name": "lookup", "arguments": "{}"}}]}, "tool_calls"))
        if len(requests) == 2:
            return httpx.Response(200, stream=_Stream(text))
        return httpx.Response(200, content=_packet({"content": RECOVERY}, "stop"))

    events = []
    result = _turn(_client(respond), _toolbox({"lookup": lambda _: "word_count.py and its test are open."}),
                   events, threading.Event()).run("Fix the text helpers so their tests pass.")
    assert result.success and result.text == RECOVERY
    assert any(e["type"] == "text_replace" and e["text"] == "" for e in events)
    assert [e["type"] for e in events].count("model_repetition") == 1
    retry = json.dumps(requests[2]["messages"])
    assert "word_count.py and its test are open." in retry  # the tool history is kept
    assert "I'll just fix it." not in retry and "I'll start." not in retry  # the stalled draft is not
    assert "Your last draft stalled" in retry


def test_without_the_detector_the_whole_loop_reaches_the_answer(monkeypatch):
    """The baseline behavior this replaces: the loop runs to the output limit and becomes the reply."""
    from agent.lean import provider

    monkeypatch.setattr(provider, "_RepeatedProse", lambda: type("Off", (), {"feed": lambda self, _: False})())
    stream = _Stream(SHAPE_LONG)
    turn = _client(lambda _: httpx.Response(200, stream=stream)).stream_turn(
        [{"role": "user", "content": "x"}], on_content=lambda _: None, cancel=threading.Event())
    assert turn.finish_reason == "length" and stream.served == len(SHAPE_LONG)


def test_a_reasoning_stall_is_a_different_failure_and_is_left_alone():
    """Repeated *reasoning* with no visible text is not visible prose: this detector doesn't act on it.

    In the saved evidence, 4 of 5,290 responses with reasoning spent the whole budget in reasoning (3 Qwen) and
    none repeated by this measure; that failure belongs to the existing thought-only handling.
    """
    loop = "Let me reconsider the order of the checks and whether the helper needs a guard.\n\n" * 200
    stream = _Stream(text="", reasoning=loop)
    thoughts = []
    turn = _client(lambda _: httpx.Response(200, stream=stream)).stream_turn(
        [{"role": "user", "content": "x"}], on_content=lambda _: None, on_reasoning=thoughts.append,
        cancel=threading.Event())
    assert turn.finish_reason == "length" and stream.served == 0 and "".join(thoughts)


# ── legitimate repetition is left alone ────────────────────────────────

LINES = "I will not talk in class.\n\n" * 20


def _single_turn(text: str, request: str):
    requests, events = [], []

    def respond(request_):
        requests.append(json.loads(request_.content))
        if len(requests) == 1:
            return httpx.Response(200, stream=_Stream(text, finish="stop"))
        return httpx.Response(200, content=_packet({"content": RECOVERY}, "stop"))

    result = _turn(_client(respond), _toolbox({}), events, threading.Event()).run(request)
    return result, requests, events


def test_repetition_the_user_asked_for_is_the_answer():
    result, requests, events = _single_turn(LINES, "Write 'I will not talk in class.' 20 times, one per paragraph.")
    assert len(requests) == 1 and result.success and result.text.count("I will not talk in class.") == 20
    assert not any(e["type"] in {"model_repetition", "text_replace"} for e in events)


def test_the_same_output_without_such_a_request_is_treated_as_a_stall():
    """The trade-off, pinned: identical paragraphs nobody asked for are a stall."""
    result, requests, events = _single_turn(LINES, "Say something about classroom rules.")
    assert len(requests) == 2 and result.text == RECOVERY
    assert any(e["type"] == "model_repetition" for e in events)


@pytest.mark.parametrize("text", [
    "".join(f"Verse {i} walks a different road tonight.\n\nAnd the chorus rings out loud and clear.\n\n"
            for i in range(10)),
    "".join(f"Step {i}. Run the build again.\n\n" for i in range(30)),
    "- Run the build again.\n\n" * 30,
    "```\nretry()\n```\n\n" * 30,
    "> Keep going.\n\n" * 30,
], ids=["song-refrain", "numbered-steps", "list-items", "code-blocks", "quotes"])
def test_structured_or_varied_repetition_is_not_a_stall(text):
    stream = _Stream(text, finish="stop")
    turn = _client(lambda _: httpx.Response(200, stream=stream)).stream_turn(
        [{"role": "user", "content": "x"}], on_content=lambda _: None, cancel=threading.Event())
    assert turn.finish_reason == "stop" and stream.served == len(text)


# ── the bounded retry ends in an accurate outcome ──────────────────────

def test_a_second_stall_stops_with_an_accurate_outcome_and_no_loop_text():
    requests, events = [], []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=_Stream(SHAPE_DENSE))

    result = _turn(_client(respond), _toolbox({}), events, threading.Event()).run("Fix the text helpers.")
    assert len(requests) == 2  # one retry, then stop
    assert not result.success and result.stop_reason == "model_repetition"
    assert result.text == "The model got stuck repeating itself. I stopped this attempt."
    assert "I'll start." not in result.text
    assert [e.get("retry") for e in events if e["type"] == "model_repetition"] == [True, False]
