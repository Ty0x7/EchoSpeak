"""Bounded native inference admission. Foreground first; aged background work wins.

All ChatClient consumers, including summaries, reflection and workers, share this
gate. It does not promise preemption of an in-flight provider request.
"""
from __future__ import annotations
import contextvars
import functools
import threading
import time
from contextlib import contextmanager

_PRIORITY = contextvars.ContextVar("echo_inference_priority", default=10)


@contextmanager
def priority(foreground: bool):
    token = _PRIORITY.set(0 if foreground else 10)
    try:
        yield
    finally:
        _PRIORITY.reset(token)


class Admission:
    def __init__(self, slots=1, aging_seconds=2.0):
        self.slots, self.aging_seconds = slots, aging_seconds
        self.condition = threading.Condition()
        self.active = 0
        self.waiters = []
        self.local = threading.local()

    @contextmanager
    def enter(self, *, cancel=None, rank=None):
        if getattr(self.local, "inside", False):
            yield
            return
        waiter = (time.monotonic(), _PRIORITY.get() if rank is None else rank, object())
        with self.condition:
            self.waiters.append(waiter)
            try:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError("Inference admission cancelled")
                    now = time.monotonic()
                    first = min(self.waiters, key=lambda item: (item[1] - (now - item[0]) / self.aging_seconds, item[0]))
                    if self.active < self.slots and first is waiter:
                        self.waiters.remove(waiter)
                        self.active += 1
                        break
                    self.condition.wait(.05)
            except BaseException:
                self.waiters.remove(waiter)
                self.condition.notify_all()
                raise
        self.local.inside = True
        try:
            yield
        finally:
            self.local.inside = False
            with self.condition:
                self.active -= 1
                self.condition.notify_all()


GATE = Admission(slots=4)
LOCAL_GATE = Admission(slots=1)


@contextmanager
def inference(endpoint=None, *, cancel=None):
    from contextlib import nullcontext
    url = str(getattr(endpoint, "base_url", "") or "").lower()
    local = bool(getattr(endpoint, "local", False)) or any(host in url for host in ("localhost", "127.0.0.1", "[::1]"))
    # Acquire the scarce local slot first so waiting GPU jobs do not consume
    # all cloud slots. Both gates are reentrant on the executing thread.
    with LOCAL_GATE.enter(cancel=cancel) if local else nullcontext():
        with GATE.enter(cancel=cancel):
            yield


def admitted(function):
    @functools.wraps(function)
    def call(*args, **kwargs):
        from agent.coordination import current
        context = current()
        if context:
            context.coordinator.check(context)
        with inference(getattr(args[0], "endpoint", None) if args else None, cancel=kwargs.get("cancel")):
            if context:
                context.coordinator.check(context)
            result = function(*args, **kwargs)
            if context:
                usage = getattr(result, "usage", {}) or {}
                context.coordinator.account(context, int(usage.get("total") or (usage.get("prompt", 0) + usage.get("completion", 0))))
            return result
    call._echo_admitted = True
    return call
