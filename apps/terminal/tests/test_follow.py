"""Cursor reconnect, idempotent submission and non-TTY approval handling, with a fake client."""
import io

import pytest

from echospeak_cli.client import ApiError, ConnectError, follow, submit
from echospeak_cli.render import Renderer
from echospeak_cli.repl import App, Out, decision_choices, parse_answer


def ev(seq, **kw):
    return {"_replay_seq": seq, **kw}


class FakeClient:
    """Serves a journal; the first `drops` streams die after `cut` events."""

    def __init__(self, events, status="completed", drops=1, cut=2):
        self.events, self.status, self.drops, self.cut = events, status, drops, cut
        self.calls = []
        self.decisions = []
        self.pending = []
        self.cancelled = []

    def stream(self, method, path, body=None, params=None):
        self.calls.append((method, path, dict(params or {}), body))
        after = int((params or {}).get("after", 0))
        items = [e for e in self.events if e["_replay_seq"] > after]
        drop = self.drops > 0
        self.drops -= 1

        def gen():
            for i, item in enumerate(items):
                if drop and i == self.cut:
                    raise ConnectError("reset")
                yield {**item, "_recovered": method == "GET"}
            yield {"type": "journal_done", "status": self.status, "_replay_seq": items[-1]["_replay_seq"] if items else after}
        return gen()

    def approvals(self, session_id=""):
        return list(self.pending)

    def decide(self, aid, decision):
        self.decisions.append((aid, decision))
        return {"ok": True}

    def cancel(self, rid, sid):
        self.cancelled.append(rid)
        return {"cancelled": True}


EVENTS = [ev(1, type="run_start"), ev(2, type="agent_start", agent={"name": "Echo"}, message_id="m"),
          ev(3, type="agent_token", data="Hel", message_id="m"), ev(4, type="agent_token", data="lo", message_id="m"),
          ev(5, type="agent_done", success=True, message_id="m"), ev(6, type="final", response="Hello", success=True)]


def test_reconnects_from_cursor_without_duplicates():
    fc = FakeClient(EVENTS, drops=2, cut=2)
    seen, notices = [], []
    status, cursor = follow(fc, "s1", "req-12345678", on_event=seen.append, sleep=lambda _s: None, notice=notices.append)
    assert status == "completed" and cursor == 6
    assert [e["_replay_seq"] for e in seen] == [1, 2, 3, 4, 5, 6]
    assert [c[2]["after"] for c in fc.calls] == [0, 2, 4]
    assert all(c[1] == "/query/runs/req-12345678/events" and c[2]["thread_id"] == "s1" for c in fc.calls)
    assert len(notices) == 2


def test_gives_up_after_retries():
    fc = FakeClient(EVENTS, drops=99, cut=0)
    with pytest.raises(ConnectError):
        follow(fc, "s", "req-12345678", on_event=lambda e: None, retries=3, sleep=lambda _s: None)


def test_skips_events_already_seen_from_first_stream():
    fc = FakeClient(EVENTS, drops=0)
    first = iter([ev(1, type="run_start"), ev(1, type="run_start"), ev(2, type="x")])  # first stream ends early
    seen = []
    follow(fc, "s", "req-12345678", first=first, on_event=seen.append, sleep=lambda _s: None)
    assert [e["_replay_seq"] for e in seen] == [1, 2, 3, 4, 5, 6]
    assert fc.calls[0][2]["after"] == 2


class SubmitClient:
    def __init__(self, fail_post=1, replay_404=True):
        self.fail_post, self.replay_404, self.calls = fail_post, replay_404, []

    def stream(self, method, path, body=None, params=None):
        self.calls.append((method, path, body))
        if method == "POST" and self.fail_post:
            self.fail_post -= 1
            raise ConnectError("refused")
        if method == "GET" and self.replay_404:
            raise ApiError(404, "Run not found")
        return iter([])


def test_submit_retries_same_body_when_run_was_never_claimed():
    sc = SubmitClient()
    submit(sc, "s", "hi", "req-abcdefgh", agent_id="echo")
    posts = [c for c in sc.calls if c[0] == "POST"]
    assert len(posts) == 2 and posts[0][2] == posts[1][2]
    assert posts[0][2] == {"message": "hi", "thread_id": "s", "client_request_id": "req-abcdefgh", "agent_id": "echo"}


def test_submit_reattaches_when_run_was_claimed():
    sc = SubmitClient(replay_404=False)
    submit(sc, "s", "hi", "req-abcdefgh")
    assert [c[0] for c in sc.calls] == ["POST", "GET"]


# ── approvals ────────────────────────────────────────────────────────
APPROVAL = {"type": "approval_request", "id": "ap1", "tool": "terminal", "summary": "rm -rf build",
            "reason": "deletes files", "args": {"command": "rm -rf build"}, "grantable": True,
            "persistable": True, "scope_label": "terminal commands in this project", "message_id": "m"}


def make_app(fc, interactive, answers=()):
    buf = io.StringIO()
    answers = list(answers)

    def ask(_prompt):
        if not answers:
            raise EOFError
        return answers.pop(0)

    app = App(fc, Renderer(), Out(buf), interactive=interactive, ask=ask)
    app.session = {"thread_id": "sess-1"}
    return app, buf


def test_non_tty_never_answers_and_reports_unanswered():
    events = [ev(1, type="agent_start", agent={"name": "Echo"}, message_id="m"), ev(2, **APPROVAL),
              ev(3, type="final", response="", success=True)]
    fc = FakeClient(events, drops=0)
    app, buf = make_app(fc, interactive=False)
    result = app.follow_run("req-12345678", first=fc.stream("POST", "/query/stream"))
    assert fc.decisions == []
    assert result.unanswered == ["ap1"] and not result.ok
    text = buf.getvalue()
    assert "approval needed: terminal" in text and "will not answer" in text and "echospeak attach sess-1" in text


def test_interactive_answer_is_posted_and_garbage_is_reasked():
    events = [ev(1, **APPROVAL), ev(2, type="approval_resolved", id="ap1", decision="deny"),
              ev(3, type="final", response="", success=True)]
    fc = FakeClient(events, drops=0)
    app, buf = make_app(fc, interactive=True, answers=["", "yes please", "n"])
    app._poll_typing = lambda: ""
    result = app.follow_run("req-12345678", first=fc.stream("POST", "/query/stream"))
    assert fc.decisions == [("ap1", "deny")] and result.ok


def test_replayed_approval_that_is_no_longer_pending_is_not_prompted():
    events = [ev(1, **APPROVAL), ev(2, type="final", success=True)]
    fc = FakeClient(events, drops=0)
    app, buf = make_app(fc, interactive=True, answers=["y"])
    app._poll_typing = lambda: ""
    app.follow_run("req-12345678")  # GET replay: events are _recovered
    assert fc.decisions == [] and "already decided" in buf.getvalue()


def test_decision_values_match_the_route():
    assert [v for _k, v, _l in decision_choices(APPROVAL)] == ["allow", "deny", "always", "persist"]
    assert [v for _k, v, _l in decision_choices({**APPROVAL, "grantable": False, "persistable": False})] == ["allow", "deny"]
    assert [v for _k, v, _l in decision_choices({**APPROVAL, "recovered": True})] == ["allow", "deny"]
    assert parse_answer(APPROVAL, "") is None and parse_answer(APPROVAL, "sure") is None
    assert parse_answer(APPROVAL, "Y") == "allow" and parse_answer(APPROVAL, "a") == "persist"
    q = {"kind": "question", "question": "Which?", "options": ["red", "blue"], "allow_other": False}
    assert parse_answer(q, "2") == "blue" and parse_answer(q, "RED") == "RED" and parse_answer(q, "green") is None
    assert parse_answer({**q, "allow_other": True}, "green") == "green"


def test_ctrl_c_cancels_the_exact_run():
    fc = FakeClient([ev(1, type="final", success=True)], drops=0)
    app, buf = make_app(fc, interactive=False)
    app.active = "req-12345678"
    app.cancel("req-12345678")
    assert fc.cancelled == ["req-12345678"] and "stopping" in buf.getvalue()
