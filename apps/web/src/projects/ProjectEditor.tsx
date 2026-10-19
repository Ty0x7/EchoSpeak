import React, { useEffect, useRef, useState } from "react";
import { Modal } from "../lean/Dialogs";
import "./editor.css";

export type ProjectIdentity = { id: string; name: string; logo_data_url?: string };

export function ProjectEditor({ project, onSave, onClose }: {
  project: ProjectIdentity;
  onSave(value: { name: string; logo_data_url: string }): Promise<void>;
  onClose(): void;
}) {
  const [name, setName] = useState(project.name);
  const [logo, setLogo] = useState(project.logo_data_url || "");
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  const body = useRef<HTMLFormElement>(null);
  const upload = useRef(0);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = body.current?.closest<HTMLElement>("[role=dialog]");
    body.current?.querySelector<HTMLInputElement>("input")?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !dialog) return;
      const items = Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),[tabindex="0"]'));
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    dialog?.addEventListener("keydown", trap);
    return () => { upload.current++; dialog?.removeEventListener("keydown", trap); previous?.focus(); };
  }, []);
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy || reading || !name.trim()) return;
    setBusy(true); setError("");
    try { await onSave({ name: name.trim(), logo_data_url: logo }); onClose(); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };
  return <Modal title="Edit project" subtitle="Choose the name and logo shown in EchoSpeak." onClose={() => { if (!busy) onClose(); }} footer={<>
    <span className="st-muted">Your folder stays in place.</span><div className="es-modal-actions"><button type="button" className="es-btn" disabled={busy} onClick={onClose}>Cancel</button><button type="submit" form="project-identity" className="es-btn es-btn-primary" disabled={busy || reading || !name.trim()}>{busy ? "Saving…" : "Save changes"}</button></div>
  </>}>
    <form id="project-identity" ref={body} className="pw-editor" onSubmit={event => void save(event)} onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); } }}>
      <label>Project name<input className="st-input" value={name} maxLength={120} required disabled={busy} onChange={event => setName(event.target.value)} /></label>
      <div className="pw-editor-logo"><span className="pw-logo-preview">{logo ? <img src={logo} alt="Project logo preview" /> : <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.2l2 2h8.8A1.5 1.5 0 0 1 21 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/></svg>}</span><div><label className="pw-logo-picker">Project logo<input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy || reading} onChange={event => {
        const file = event.target.files?.[0]; event.target.value = "";
        if (!file) return;
        if (file.size > 2 * 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) { setError("Choose a PNG, JPEG or WebP smaller than 2 MB."); return; }
        const token = ++upload.current; setReading(true); setError("");
        const reader = new FileReader();
        reader.onload = () => { if (token === upload.current) { setLogo(String(reader.result)); setReading(false); } };
        reader.onerror = () => { if (token === upload.current) { setError("The image could not be opened."); setReading(false); } };
        reader.readAsDataURL(file);
      }} /></label><small>PNG, JPEG or WebP · up to 2 MB</small>{logo ? <button type="button" className="pw-logo-remove" disabled={busy || reading} onClick={() => setLogo("")}>Use folder icon</button> : null}</div></div>
      {error ? <p role="alert">{error}</p> : null}
    </form>
  </Modal>;
}
