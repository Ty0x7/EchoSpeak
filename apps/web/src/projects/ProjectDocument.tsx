import React, { useEffect, useRef, useState } from "react";
import { RichMarkdown } from "../widgets/RichMarkdown";
import { SafeImage } from "../widgets/SafeImage";
import { ExternalLink } from "../widgets/env";
import { CodeBlock } from "../widgets/CodeBlock";
import { localReference, request } from "./model";

export function ProjectMedia({ base, path, caption = "", assetId, mediaKind }: { base: string; path: string; caption?: string; assetId?: string; mediaKind?: string }) {
  const holder = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const video = mediaKind === "video" || /\.(mp4|webm)$/i.test(path);
  const audio = mediaKind === "audio" || /\.(mp3|wav|ogg)$/i.test(path);
  useEffect(() => {
    if (!holder.current) return;
    if (!window.IntersectionObserver) { setVisible(true); return; }
    const observer = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) { setVisible(true); observer.disconnect(); } });
    observer.observe(holder.current);
    return () => observer.disconnect();
  }, [base, path]);
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    let objectUrl = "";
    setUrl(""); setError("");
    const source = assetId ? `${base.slice(0, base.indexOf("/projects/"))}/creations/${encodeURIComponent(assetId)}/content` : `${base}/media?path=${encodeURIComponent(path)}`;
    fetch(source, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`Media unavailable (${response.status})`); return response.blob(); })
      .then(blob => { if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } })
      .catch(err => { if (!controller.signal.aborted) setError(err.message); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [base, path, visible, assetId]);
  useEffect(() => {
    if (!expanded) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setExpanded(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [expanded]);
  return <span ref={holder} className="pw-media">
    {error ? <span role="status">{error}</span> : !url ? <span className="st-muted">Loading media…</span>
      : video ? <video src={url} controls preload="metadata" aria-label={caption || path} />
      : audio ? <audio src={url} controls preload="metadata" aria-label={caption || path} />
      : <button type="button" className="pw-image" onClick={() => setExpanded(true)} title="View original"><img src={url} alt={caption || path} loading="lazy" /></button>}
    <span className="pw-media-caption">{caption || path}{url ? <a href={url} download={path.split("/").pop()}>Save original</a> : null}</span>
    {expanded && url ? <span className="pw-lightbox" role="dialog" aria-modal="true" aria-label={caption || "Project image"}>
      <button autoFocus type="button" className="es-btn" onClick={() => setExpanded(false)}>Close image</button>
      <img src={url} alt={caption || path} /><a className="es-btn" href={url} download={path.split("/").pop()}>Save original</a>
    </span> : null}
  </span>;
}

export function ProjectMarkdown({ text, path, base, onFile }: { text: string; path: string; base: string; onFile(path: string): void }) {
  return <RichMarkdown text={text}
    renderImage={(src, alt) => { const local = localReference(src, path); return local ? <ProjectMedia key={local} base={base} path={local} caption={alt} /> : <SafeImage src={src} alt={alt} />; }}
    renderLink={(href, children) => { const local = localReference(href, path); return local ? <button type="button" className="pw-text-link" onClick={() => onFile(local)}>{children}</button> : <ExternalLink href={href}>{children}</ExternalLink>; }} />;
}

export function ProjectFile({ base, path, onFile, onClose }: { base: string; path: string; onFile(path: string): void; onClose(): void }) {
  const [data, setData] = useState<{ text: string; html_preview?: string } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true; setData(null); setError("");
    if (!/\.(png|jpe?g|webp|gif|avif|mp4|webm|mp3|wav|ogg)$/i.test(path)) {
      request<{ text: string; html_preview?: string }>(`${base}/file?path=${encodeURIComponent(path)}`).then(value => { if (live) setData(value); }).catch(err => { if (live) setError(err.message); });
    }
    return () => { live = false; };
  }, [base, path]);
  const media = /\.(png|jpe?g|webp|gif|avif|mp4|webm|mp3|wav|ogg)$/i.test(path);
  return <section className="pw-document" aria-label="Project file preview">
    <header className="pw-document-head"><strong>{path}</strong><button type="button" className="es-btn es-btn-sm" onClick={onClose}>Close file</button></header>
    {error ? <p role="alert">{error}</p> : media ? <ProjectMedia key={path} base={base} path={path} /> : !data ? <p role="status">Opening file…</p>
      : /\.(md|txt)$/i.test(path) ? <ProjectMarkdown text={data.text} path={path} base={base} onFile={onFile} />
      : data.html_preview ? <iframe className="pw-html" title={path} sandbox="allow-scripts" referrerPolicy="no-referrer" srcDoc={data.html_preview} />
      : <CodeBlock code={data.text} lang={path.split(".").pop() || "text"} />}
  </section>;
}
