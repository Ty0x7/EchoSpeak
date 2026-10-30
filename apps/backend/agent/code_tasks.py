"""Owner-submitted background turns using the existing /query harness and queue.

No model router, scheduler, provider adapter or automatic restart replay lives here.
"""
import asyncio
import hashlib
import threading

_lock = threading.RLock()
_active = {}
_receipts = {}
_cancelled = set()


def cancel(thread_id):
    with _lock:
        _cancelled.update(key for key, r in _receipts.items() if r["thread_id"] == thread_id and r["status"] in {"accepted", "queued"})


def was_cancelled(request_id):
    from agent.lean.stop import paused
    with _lock:
        return request_id in _cancelled or request_id in _receipts and paused()


def submit(thread_id, message, request_id, effort="medium"):
    from agent.state import get_state_store
    from agent.threads import get_thread_manager
    from agent.lean.stop import paused
    if paused():
        raise PermissionError("Resume after Stop everything before sending background work")
    if not get_thread_manager().get_thread(thread_id):
        raise KeyError("Session unavailable")
    if not message.strip() or len(message) > 50000 or not 8 <= len(request_id) <= 128:
        raise ValueError("Enter a bounded message and stable request identity")
    fingerprint = hashlib.sha256((thread_id + "\0" + message + "\0" + effort).encode()).hexdigest()
    store = get_state_store()
    with _lock:
        previous = _receipts.get(request_id)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise ValueError("Request identity was already used for another task")
            return {k: v for k, v in previous.items() if k != "fingerprint"}
        # Durable executions prevent replay even after this process restarts.
        recorded = next((row for row in store.list_executions(thread_id, limit=2000) if row.request_id == request_id), None)
        if recorded:
            if recorded.query != message:
                raise ValueError("Request identity was already recorded for another task")
            return {"request_id": request_id, "thread_id": thread_id, "status": "already_recorded"}
        state = store.get_thread_state(thread_id)
        busy = bool(thread_id in _active or state.active_turn_id or state.current_execution_id)
        if thread_id not in _active and len(_active) >= 4:
            raise ValueError("Four background sessions are already active")
        if not busy:
            # Native Code shares its project's folder. Separate vendor worktrees may run in parallel.
            from agent.echo_code import is_code_thread
            if is_code_thread(thread_id) and state.active_project_id:
                if any(s.thread_id != thread_id and s.active_project_id == state.active_project_id
                       and (s.active_turn_id or s.current_execution_id or s.thread_id in _active)
                       for s in store.list_thread_states()):
                    raise ValueError("Another native session is using this project. Use an isolated external worktree for parallel edits.")
            if len(_active) >= 4:
                raise ValueError("Four background sessions are already active")
        receipt = {"request_id": request_id, "thread_id": thread_id, "status": "queued" if busy else "accepted", "fingerprint": fingerprint}
        _receipts[request_id] = receipt
        while len(_receipts) > 2000:
            evicted = next(iter(_receipts))
            _receipts.pop(evicted)
            _cancelled.discard(evicted)
        if busy:
            store.enqueue_turn(thread_id, message=message, client_request_id=request_id)
            if thread_id not in _active:
                task = threading.Thread(target=_drain_after_turn, args=(thread_id, effort), daemon=True, name="EchoCodeQueue")
                _active[thread_id] = task
                task.start()
        else:
            task = threading.Thread(target=_run, args=(thread_id, message, request_id, effort), daemon=True, name="EchoCodeBackground")
            _active[thread_id] = task
            task.start()
        return {k: v for k, v in receipt.items() if k != "fingerprint"}


def _run(thread_id, message, request_id, effort):
    from api.routes.chat import query, QueryRequest
    from agent.state import get_state_store
    try:
        while True:
            try:
                result = asyncio.run(query(QueryRequest(thread_id=thread_id, message=message,
                                           client_request_id=request_id, reasoning_effort=effort)))
                status = "completed" if result.success else "failed"
            except Exception:
                status = "failed"
            with _lock:
                if request_id in _receipts:
                    _receipts[request_id]["status"] = status
            with _lock:
                if status != "completed" or was_cancelled(request_id):
                    break
                item = get_state_store().claim_queued_turn(thread_id)
                if not item:
                    _active.pop(thread_id, None)
                    break
            message, request_id = item["message"], item["client_request_id"]
    finally:
        with _lock:
            if _active.get(thread_id) is threading.current_thread():
                _active.pop(thread_id, None)


def _drain_after_turn(thread_id, effort):
    from agent.state import get_state_store
    from agent.lean.stop import paused
    store = get_state_store()
    try:
        # Wait only for this existing turn; never reconstruct or replay a lost turn.
        for _ in range(1800):
            if paused() or store.queue_paused(thread_id):
                return
            state = store.get_thread_state(thread_id)
            if not state.active_turn_id and not state.current_execution_id:
                with _lock:
                    item = store.claim_queued_turn(thread_id)
                    if not item:
                        _active.pop(thread_id, None)
                if item:
                    _run(thread_id, item["message"], item["client_request_id"], effort)
                return
            threading.Event().wait(1)
    finally:
        with _lock:
            if _active.get(thread_id) is threading.current_thread():
                _active.pop(thread_id, None)


def active(thread_id):
    with _lock:
        return thread_id in _active
