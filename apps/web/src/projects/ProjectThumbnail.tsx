import React, { useEffect, useRef, useState } from "react";
import { request, type ProjectDocument } from "./model";
import { useProjectBlob } from "./useProjectBlob";

/** Real local images or a static sandboxed HTML preview; never runs report scripts. */
export function ProjectThumbnail({ base, document: doc, revision }: { base: string; document: ProjectDocument; revision: string }) {
  const holder = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);
  const [html, setHtml] = useState("");
  const [size, setSize] = useState({ width: 300, height: 180 });
  const { url: image } = useProjectBlob(doc.thumbnail ? `${base}/media?path=${encodeURIComponent(doc.thumbnail)}` : "", revision, visible, true);
  useEffect(() => {
    const node = holder.current;
    if (!node) return;
    const resize = () => { if (node.clientWidth) setSize({ width: node.clientWidth, height: node.clientHeight }); };
    resize();
    const observer = new ResizeObserver(resize); observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!holder.current) return;
    if (!window.IntersectionObserver) { setVisible(true); return; }
    const observer = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) { setVisible(true); observer.disconnect(); } });
    observer.observe(holder.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible) return;
    let active = true;
    if (!doc.thumbnail && doc.kind === "html") {
      request<{ html_preview: string }>(`${base}/file?path=${encodeURIComponent(doc.path)}`)
        .then(data => { if (active) setHtml(data.html_preview); }).catch(() => undefined);
    }
    return () => { active = false; };
  }, [base, doc.path, doc.thumbnail, doc.kind, revision, visible]);
  return <span className="pw-thumbnail" ref={holder} aria-hidden="true">{image ? <img src={image} alt="" /> : html ? <iframe title={`${doc.title} thumbnail`} tabIndex={-1} sandbox="" referrerPolicy="no-referrer" srcDoc={html} style={{ width: 1000, height: Math.round(1000 * size.height / size.width), transform: `scale(${size.width / 1000})` }} /> : <span className="pw-file-mark">HTML</span>}</span>;
}
