import React, { useEffect, useState } from "react";
import type { Message } from "../app/types";

export function ActionIcon({ name }: { name: "copy" | "edit" | "retry" | "sound" | "stop" | "mic" | "voice" | "think" | "more" }) {
  const paths: Record<string, React.ReactNode> = {
    copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h4" /></>,
    edit: <><path d="m15 4 5 5M4 20l4-1L20 7a2 2 0 0 0-4-4L4 15z" /></>,
    retry: <><path d="M3 11a9 9 0 1 1 2 7M3 4v7h7" /></>,
    sound: <><path d="m11 4-6 5H2v6h3l6 5zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14" /></>,
    stop: <rect x="5" y="5" width="14" height="14" rx="2" />,
    mic: <><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8" /></>,
    voice: <><path d="M4 9v6M8 5v14M12 2v20M16 6v12M20 9v6" /></>,
    think: <><path d="M9 18H7a3 3 0 0 1-3-3 4 4 0 0 1 0-7 4 4 0 0 1 8-3 4 4 0 0 1 8 3 4 4 0 0 1 0 7 3 3 0 0 1-3 3h-2M12 5v17M8 9l4 3 4-3" /></>,
    more: <><circle cx="4" cy="12" r="1" /><circle cx="12" cy="12" r="1" /><circle cx="20" cy="12" r="1" /></>,
  };
  return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{paths[name]}</svg>;
}

export type MessageActionProps = {
  onRevise?(message: Message, text: string): Promise<void>;
  onRead?(message: Message): Promise<void>;
  readingId?: string;
  actionBusy?: boolean;
};

export function MessageActions({ message, disabled = false, onRevise, onRead, readingId, actionBusy }: MessageActionProps & { message: Message; disabled?: boolean }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.text);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (notice !== "Copied") return;
    const timer = window.setTimeout(() => setNotice(""), 1600);
    return () => window.clearTimeout(timer);
  }, [notice]);
  const revise = async (text: string) => {
    if (!onRevise || busy || actionBusy) return;
    setBusy(true); setNotice("");
    try { await onRevise(message, text); setEditing(false); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Couldn't retry this prompt."); }
    finally { setBusy(false); }
  };
  const blocked = disabled || busy || actionBusy;
  return <div className="message-actions-wrap" data-role={message.role}>
    {editing ? <div className="message-edit">
      <label htmlFor={`edit-${message.id}`}>Edit prompt</label>
      <textarea id={`edit-${message.id}`} autoFocus value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === "Escape") setEditing(false); }} />
      <small>Your original conversation stays saved. This continues from before this prompt.</small>
      <div><button type="button" className="es-btn es-btn-quiet" onClick={() => setEditing(false)} disabled={busy}>Cancel</button><button type="button" className="es-btn es-btn-primary" disabled={blocked || !draft.trim()} onClick={() => void revise(draft)}>{busy ? "Starting…" : "Save & send"}</button></div>
    </div> : null}
    <div className="message-actions" role="group" aria-label={`${message.role === "user" ? "Prompt" : "Reply"} actions`}>
      <button type="button" title="Copy message" aria-label="Copy message" onClick={async () => {
        try { await navigator.clipboard.writeText(message.text); setNotice("Copied"); }
        catch { setNotice("Clipboard unavailable. Select the message to copy it."); }
      }}><ActionIcon name="copy" /></button>
      {message.role === "user" && onRevise ? <>
        <button type="button" title="Edit prompt" aria-label="Edit prompt" onClick={() => { setDraft(message.text); setEditing(v => !v); setNotice(""); }}><ActionIcon name="edit" /></button>
        <button type="button" title={disabled ? "Wait for this chat to finish, or stop it first" : "Retry from this prompt"} aria-label="Retry prompt" disabled={blocked} onClick={() => void revise(message.text)}><ActionIcon name="retry" /></button>
      </> : null}
      {message.role === "assistant" && onRead ? <button type="button" aria-pressed={readingId === message.id} aria-label={readingId === message.id ? "Stop reading" : "Read aloud"} title={readingId === message.id ? "Stop reading" : "Read aloud"} onClick={() => void onRead(message)}><ActionIcon name={readingId === message.id ? "stop" : "sound"} /></button> : null}
      {notice ? <span role="status">{notice}</span> : null}
    </div>
  </div>;
}
