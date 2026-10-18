import React, { useEffect, useRef, useState } from "react";
import { request, type ProjectDocument } from "./model";

/** Real local images or a static sandboxed HTML preview; never runs report scripts. */
export function ProjectThumbnail({ base, document: doc, revision }: { base: string; document: ProjectDocument; revision: string }) {
  const holder = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [image, setImage] = useState("");
  const [html, setHtml] = useState("");
  useEffect(() => {
    if (!holder.current) return;
    if (!window.IntersectionObserver) { setVisible(true); return; }
    const observer = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) { setVisible(true); observer.disconnect(); } });
    observer.observe(holder.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible) return;
    let active = true; let url = "";
    setImage(""); setHtml("");
    const controller = new AbortController();
    if (doc.thumbnail) {
      fetch(`${base}/media?path=${encodeURIComponent(doc.thumbnail)}`, { signal: controller.signal, cache: "no-store" })
        .then(async res => { if (!res.ok) throw new Error(); return res.blob(); })
        .then(blob => { if (active) { url = URL.createObjectURL(blob); setImage(url); } }).catch(() => undefined);
    } else if (doc.kind === "html") {
      request<{ html_preview: string }>(`${base}/file?path=${encodeURIComponent(doc.path)}`)
        .then(data => { if (active) setHtml(data.html_preview); }).catch(() => undefined);
    }
    return () => { active = false; controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [base, doc.path, doc.thumbnail, doc.kind, revision, visible]);
  return <span className="pw-thumbnail" ref={holder} aria-hidden="true">{image ? <img src={image} alt="" /> : html ? <iframe title={`${doc.title} thumbnail`} tabIndex={-1} sandbox="" referrerPolicy="no-referrer" srcDoc={html} /> : <span className="pw-file-mark">{doc.kind === "html" ? "HTML" : "MD"}</span>}</span>;
}
