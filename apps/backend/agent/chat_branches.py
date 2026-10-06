"""Continue before a prompt without deleting history or replaying past actions."""
from __future__ import annotations


def branch_before_prompt(session_id: str, *, execution_id: str = "", request_id: str = ""):
    from agent.state import get_state_store
    from agent.threads import get_thread_manager
    from agent.lean.rooms import get_room_store
    store, manager = get_state_store(), get_thread_manager()
    source = manager.get_thread(session_id)
    if source is None:
        raise KeyError("Chat not found")
    # Snapshot under the store lock; an execution must belong to the source chat.
    with store._lock:
        turns = list(reversed(store.list_executions(session_id, limit=10000)))
        anchor = next((i for i, t in enumerate(turns) if
                       (execution_id and t.id == execution_id) or
                       (not execution_id and request_id and t.request_id == request_id)), None)
        if execution_id and anchor is None:
            raise ValueError("That prompt no longer belongs to this chat. Reload its history.")
        if anchor is None and request_id:
            from agent.query_journal import get_query_journal
            run = get_query_journal().get(request_id, session_id)
            if run and run["status"] == "running":
                raise ValueError("This request is still starting. Stop it or wait before retrying.")
        previous = turns[:anchor] if anchor is not None else turns
        if any(t.status not in {"completed", "failed", "canceled", "superseded"} for t in previous):
            raise ValueError("A previous turn is still running. Wait for it before branching.")
        room = get_room_store().by_thread(session_id)
        if room:
            target_room = get_room_store().create(name=f"{room.name} · branch", agent_ids=room.agent_ids,
                                                 kind=room.kind, mode=room.mode, max_messages=room.max_messages)
            target = manager.get_thread(target_room.thread_id)
        else:
            target = manager.create_thread(title=f"{source.title} · branch", source=source.source,
                                           workspace_id=source.workspace_id)
        state = store.get_thread_state(session_id)
        binding = state.model_binding
        if binding:
            store.ensure_session_model_binding(target.thread_id, provider_id=binding.provider_id,
                model_id=binding.model_id, provider_configuration_id=binding.provider_configuration_id)
        count = 0
        for old in previous:
            new = store.create_execution(thread_id=target.thread_id, source="chat_branch", kind="query",
                query=old.query, runtime_provider=old.runtime_provider, model_id=old.model_id,
                workspace_id=state.workspace_id, active_project_id=state.active_project_id,
                record_user_message=False, created_at=old.created_at,
                metadata={"branched_from_session": session_id, "branched_from_execution": old.id})
            for item in store.list_items(old.id):
                if item.item_type not in {"user_message", "assistant_message"}:
                    continue
                payload = dict(item.payload)
                # Historical messages only. No approval records, permissions or runnable tools are copied.
                payload["branched_from_item"] = item.id
                store.add_item(turn_id=new.id, item_type=item.item_type, status="complete", payload=payload,
                               session_id=target.thread_id, project_id=state.active_project_id, model_id=old.model_id)
                count += item.item_type == "user_message"
            store.update_execution(new.id, status=old.status, success=old.success,
                                   response_preview=old.response_preview, error=old.error)
        store.update_thread_state(target.thread_id, workspace_id=state.workspace_id,
            active_project_id=state.active_project_id, execution_status="ready",
            current_execution_id="", active_turn_id="", pending_approval_id="")
        manager.touch_thread(target.thread_id, increment_messages=False)
        # Metadata count prevents a copied conversation being mistaken for an empty draft.
        with manager._lock:
            manager._threads[target.thread_id].message_count = count
            manager._save()
        return manager.get_thread(target.thread_id)
