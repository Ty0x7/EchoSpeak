import React, { useCallback, useEffect, useRef, useState } from "react";

/**
 * Talking to an agent while it works (agent/lean/steering.py, StateStore.queued_turns).
 *
 * Queue: the message waits and runs as its own turn when the current one ends.
 * Steer: the message joins the running turn at its next safe checkpoint, so the
 * work done so far is kept. A steer that arrives too late becomes a queued item.
 * After Stop the queue pauses instead of starting the next message by itself.
 */
export type QueuedItem = { message: string; client_request_id: string; created_at: number };

export function useFollowUps(apiBase: string, threadId: string) {
  const [items, setItems] = useState<QueuedItem[]>([]);
  const [paused, setPaused] = useState<Set<string>>(() => new Set());
  const [notice, setNotice] = useState("");
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  const refresh = useCallback(async (session: string = threadId) => {
    if (!session) return;
    try {
      const response = await fetch(`${apiBase}/query/queue?thread_id=${encodeURIComponent(session)}`);
      if (!response.ok) return;
      const data = await response.json();
      if (session === threadId) setItems(Array.isArray(data.queue) ? data.queue : []);
    } catch {
      // The queue is durable on the backend; the list catches up on the next refresh.
    }
  }, [apiBase, threadId]);

  useEffect(() => { setItems([]); setNotice(""); void refresh(threadId); }, [threadId, refresh]);
  // Confirmations are brief: they fade instead of sitting above the composer.
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 6000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const queue = useCallback(async (text: string, session: string = threadId) => {
    const message = text.trim();
    if (!message || !session) return false;
    const response = await fetch(`${apiBase}/query/queue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: session, message, client_request_id: crypto.randomUUID() }),
    });
    if (!response.ok) return false;
    setNotice("");
    await refresh(session);
    return true;
  }, [apiBase, threadId, refresh]);

  /** Returns "steered", or "queued" when nothing was running any more. */
  const steer = useCallback(async (text: string, session: string = threadId): Promise<"steered" | "queued" | "failed"> => {
    const message = text.trim();
    if (!message || !session) return "failed";
    const response = await fetch(`${apiBase}/query/steer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: session, message, client_request_id: crypto.randomUUID() }),
    });
    if (response.ok) {
      setNotice("Sent to the agent. It reads it at its next step.");
      return "steered";
    }
    if (response.status === 409) {
      const queued = await queue(message, session);
      if (queued) setNotice("The agent had already finished, so it's queued instead.");
      return queued ? "queued" : "failed";
    }
    return "failed";
  }, [apiBase, threadId, queue]);

  const edit = useCallback(async (id: string, message: string) => {
    const response = await fetch(`${apiBase}/query/queue/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: threadId, message }),
    });
    if (!response.ok) setNotice("That message already started, so it couldn't be changed.");
    await refresh();
  }, [apiBase, threadId, refresh]);

  const remove = useCallback(async (id: string) => {
    await fetch(`${apiBase}/query/queue/${encodeURIComponent(id)}?thread_id=${encodeURIComponent(threadId)}`, { method: "DELETE" });
    await refresh();
  }, [apiBase, threadId, refresh]);

  const pause = useCallback((session: string) => {
    setPaused((current) => new Set(current).add(session));
  }, []);
  const resume = useCallback((session: string) => {
    setPaused((current) => {
      const next = new Set(current);
      next.delete(session);
      return next;
    });
  }, []);
  const isPaused = useCallback((session: string) => pausedRef.current.has(session), []);

  return { items, notice, refresh, queue, steer, edit, remove, pause, resume, isPaused, paused: paused.has(threadId) };
}

/** Queued messages above the composer: edit or remove before they run; resume after Stop. */
export function FollowUpQueue({
  items, paused, notice, busy, onEdit, onRemove, onResume,
}: {
  items: QueuedItem[];
  paused: boolean;
  notice: string;
  busy: boolean;
  onEdit(id: string, message: string): void;
  onRemove(id: string): void;
  onResume(): void;
}) {
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  if (!items.length && !notice) return null;
  return (
    <div className="lm-queue" aria-label="Messages waiting to run">
      {notice ? <div className="lm-queue-note" role="status">{notice}</div> : null}
      {items.length ? (
        <div className="lm-queue-head">
          <span>{paused ? "Paused after Stop" : busy ? "Up next, after this reply" : "Starting next"}</span>
          {paused && !busy ? <button type="button" className="es-btn es-btn-sm" onClick={onResume}>Run next</button> : null}
        </div>
      ) : null}
      {items.map((item, index) => (
        <div key={item.client_request_id} className="lm-queue-item">
          <span className="lm-queue-index" aria-hidden>{index + 1}</span>
          {editing?.id === item.client_request_id ? (
            <form className="lm-queue-edit" onSubmit={(e) => { e.preventDefault(); if (editing.text.trim()) onEdit(item.client_request_id, editing.text); setEditing(null); }}>
              <input autoFocus value={editing.text} onChange={(e) => setEditing({ id: item.client_request_id, text: e.target.value })} aria-label="Edit queued message" />
              <button type="submit" className="es-btn es-btn-sm es-btn-primary" disabled={!editing.text.trim()}>Save</button>
              <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => setEditing(null)}>Cancel</button>
            </form>
          ) : (
            <>
              <span className="lm-queue-text" title={item.message}>{item.message}</span>
              <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => setEditing({ id: item.client_request_id, text: item.message })}>Edit</button>
              <button type="button" className="es-btn es-btn-sm es-btn-quiet" onClick={() => onRemove(item.client_request_id)} aria-label="Remove queued message">Remove</button>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
