import React, { useCallback, useEffect, useRef, useState } from "react";
import { CodeBlock, CopyButton } from "./CodeBlock";
import { openExternal } from "./env";
import { MermaidDiagram } from "./Mermaid";
import { RichMarkdown } from "./RichMarkdown";

type ArtifactDetail = {
  id: string;
  title: string;
  kind: "html" | "svg" | "mermaid" | "markdown" | "code" | string;
  language: string;
  version: number;
  versions: number;
  history: { n: number; title: string; at: number; note: string }[];
  current: { n: number; title: string; content: string };
};

const EXT: Record<string, string> = { html: "html", svg: "svg", mermaid: "mmd", markdown: "md" };
const CODE_EXT: Record<string, string> = { python: "py", javascript: "js", typescript: "ts", tsx: "tsx", jsx: "jsx", rust: "rs", go: "go", java: "java", csharp: "cs", cpp: "cpp", c: "c", bash: "sh", shell: "sh", powershell: "ps1", json: "json", yaml: "yml", sql: "sql", css: "css", ruby: "rb", php: "php", kotlin: "kt", swift: "swift" };
const SOURCE_LANG: Record<string, string> = { html: "html", svg: "xml", mermaid: "mermaid", markdown: "markdown" };

export function fileNameFor(detail: Pick<ArtifactDetail, "title" | "kind" | "language">): string {
  const slug = (detail.title || "artifact").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "artifact";
  // Only known extensions: a model-chosen ".bat" or ".hta" would run when double-clicked.
  const ext = detail.kind === "code" ? CODE_EXT[detail.language] || "txt" : EXT[detail.kind] || "txt";
  return `${slug}.${ext}`;
}

/** What each artifact kind is called, singular. */
export const ARTIFACT_KIND_LABEL: Record<string, string> = { html: "App", markdown: "Document", code: "Code", mermaid: "Diagram", svg: "Image" };

/** The small line icon for an artifact kind, used in the library and the viewer. */
export function ArtifactKindIcon({ kind, size = 16 }: { kind: string; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (kind) {
    case "html":
      return <svg {...common}><rect x="3" y="4" width="18" height="16" rx="2.5" /><path d="M3 8.5h18M6.5 6.3h.01M9 6.3h.01" /></svg>;
    case "markdown":
      return <svg {...common}><path d="M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></svg>;
    case "mermaid":
      return <svg {...common}><rect x="3" y="3" width="7" height="6" rx="1.5" /><rect x="14" y="15" width="7" height="6" rx="1.5" /><path d="M6.5 9v3.5a2 2 0 0 0 2 2H17v.5" /></svg>;
    case "svg":
      return <svg {...common}><rect x="3" y="3" width="18" height="18" rx="3" /><circle cx="9" cy="9" r="1.6" /><path d="m4 18 5.5-5.5 4 4 2.5-2.5L21 19" /></svg>;
    default:
      return <svg {...common}><path d="m8 7-5 5 5 5M16 7l5 5-5 5M13.5 5l-3 14" /></svg>;
  }
}

const TOOL_ICON = { width: 14, height: 14, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };

const agoSeconds = (seconds: number) => {
  if (!seconds) return "";
  const s = Math.max(0, Date.now() / 1000 - seconds);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
};

function download(name: string, content: string) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** The sandboxed frame for HTML/SVG: served by the backend with a CSP sandbox, loaded with a short-lived token. */
function ArtifactFrame({ apiBase, id, version, title, reloadKey }: { apiBase: string; id: string; version: number; title: string; reloadKey: number }) {
  const [src, setSrc] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setSrc("");
    setError("");
    fetch(`${apiBase}/lean/artifacts/${id}/frame-token`, { method: "POST" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data) => live && setSrc(`${apiBase}/lean/artifacts/${id}/frame?v=${version}&t=${encodeURIComponent(data.token)}`))
      .catch((err) => live && setError(String(err?.message || err)));
    return () => {
      live = false;
    };
  }, [apiBase, id, version, reloadKey]);
  if (error) return <div className="ap-empty">Couldn't load the preview ({error}).</div>;
  if (!src) return <div className="ap-empty" role="status">Loading preview…</div>;
  return (
    <iframe
      key={src}
      className="ap-frame"
      src={src}
      title={title}
      sandbox="allow-scripts allow-modals"
      referrerPolicy="no-referrer"
      allow=""
    />
  );
}

export function ArtifactPanel({
  apiBase,
  artifactId,
  version,
  overlay = false,
  embedded = false,
  onClose,
  onEdit,
}: {
  apiBase: string;
  artifactId: string;
  /** Version to show; undefined follows the latest. */
  version?: number;
  overlay?: boolean;
  /** Inside the right panel, which has its own close button and placement. */
  embedded?: boolean;
  onClose(): void;
  onEdit?(id: string, version: number, passage?: string): void;
}) {
  const [detail, setDetail] = useState<ArtifactDetail | null>(null);
  const [error, setError] = useState("");
  const [shown, setShown] = useState<number | undefined>(version);
  const [view, setView] = useState<"preview" | "source">("preview");
  const [fullscreen, setFullscreen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [selection, setSelection] = useState("");
  const bodyRef = useRef<HTMLDivElement>(null);
  const loadSequence = useRef(0);

  useEffect(() => { setShown(version); setSelection(""); }, [artifactId, version]);

  const load = useCallback(
    async (n?: number) => {
      const sequence = ++loadSequence.current;
      try {
        const response = await fetch(`${apiBase}/lean/artifacts/${artifactId}${n ? `?version=${n}` : ""}`);
        if (!response.ok) throw new Error(response.status === 404 ? "This artifact no longer exists." : `HTTP ${response.status}`);
        const next = await response.json();
        if (sequence !== loadSequence.current) return;
        setDetail(next);
        setError("");
      } catch (err: any) {
        if (sequence !== loadSequence.current) return;
        setError(String(err?.message || err));
      }
    },
    [apiBase, artifactId],
  );
  useEffect(() => {
    void load(shown);
  }, [load, shown]);
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && setFullscreen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  const current = detail?.current;
  const latest = detail?.version || 1;
  const n = current?.n || 1;
  const runnable = detail?.kind === "html" || detail?.kind === "svg";
  const hasPreview = detail ? detail.kind !== "code" : false;
  const restore = async () => {
    const response = await fetch(`${apiBase}/lean/artifacts/${artifactId}/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version: n }),
    });
    if (response.ok) setShown(undefined);
    await load(undefined);
  };
  const openInBrowser = async () => {
    const response = await fetch(`${apiBase}/lean/artifacts/${artifactId}/frame-token`, { method: "POST" });
    if (!response.ok) return;
    const { token } = await response.json();
    openExternal(`${apiBase}/lean/artifacts/${artifactId}/frame?v=${n}&t=${encodeURIComponent(token)}`);
  };

  return (
    <section className={`ap${embedded ? " is-embedded" : ""}${overlay ? " is-overlay" : ""}${fullscreen ? " is-fullscreen" : ""}`} aria-label="Artifact">
      <header className="ap-head">
        {detail ? <span className={`ap-kind is-${detail.kind}`}><ArtifactKindIcon kind={detail.kind} /></span> : null}
        <div className="ap-title">
          <strong title={current?.title || detail?.title}>{current?.title || detail?.title || "Artifact"}</strong>
          <small>
            {detail ? [
              detail.kind === "code" ? (detail.language || "code") : ARTIFACT_KIND_LABEL[detail.kind] || detail.kind,
              `v${n}${detail.versions > 1 ? ` of ${detail.versions}` : ""}`,
              agoSeconds(detail.history.find((item) => item.n === n)?.at || 0),
              current ? `${current.content.split("\n").length} lines` : "",
            ].filter(Boolean).join(" · ") : "…"}
          </small>
        </div>
        {detail && detail.versions > 1 ? <div className="ap-versions" role="group" aria-label="Versions">
          {detail ? <select aria-label="Artifact version" value={n} onChange={event => { setShown(Number(event.target.value) === latest ? undefined : Number(event.target.value)); setSelection(""); }}>
            {detail.history.map(item => <option key={item.n} value={item.n}>Version {item.n}{item.n === latest ? " · latest" : ""}{item.note ? ` · ${item.note}` : ""}</option>)}
          </select> : null}
          <button type="button" className="wg-ghost" disabled={!detail || n <= (detail.history[0]?.n || 1)} onClick={() => setShown(Math.max(1, n - 1))} aria-label="Previous version">‹</button>
          <button type="button" className="wg-ghost" disabled={!detail || n >= latest} onClick={() => setShown(n + 1 >= latest ? undefined : n + 1)} aria-label="Next version">›</button>
        </div> : null}
        {!embedded ? <button type="button" className="wg-ghost ap-close" onClick={onClose} aria-label="Close artifact">✕</button> : null}
      </header>
      <div className="ap-tools">
        {hasPreview ? (
          <div className="ap-seg" role="radiogroup" aria-label="View">
            <button type="button" role="radio" aria-checked={view === "preview"} className={view === "preview" ? "is-on" : ""} onClick={() => setView("preview")}>Preview</button>
            <button type="button" role="radio" aria-checked={view === "source"} className={view === "source" ? "is-on" : ""} onClick={() => setView("source")}>Code</button>
          </div>
        ) : null}
        <span className="ap-spacer" />
        {current ? <CopyButton text={current.content} /> : null}
        {current && detail ? <button type="button" className="ap-icon" onClick={() => download(fileNameFor(detail), current.content)} title={`Download ${fileNameFor(detail)}`} aria-label="Download"><svg {...TOOL_ICON}><path d="M12 4v11M7 10l5 5 5-5M5 20h14" /></svg></button> : null}
        {runnable ? <button type="button" className="ap-icon" onClick={() => void openInBrowser()} title="Open in your browser (still sandboxed)" aria-label="Open in your browser"><svg {...TOOL_ICON}><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></svg></button> : null}
        {runnable && view === "preview" ? <button type="button" className="ap-icon" onClick={() => setReloadKey((k) => k + 1)} title="Restart the app" aria-label="Reload"><svg {...TOOL_ICON}><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" /></svg></button> : null}
        {!embedded && <button type="button" className="ap-icon" onClick={() => setFullscreen((v) => !v)} aria-pressed={fullscreen} title={fullscreen ? "Exit full screen" : "Full screen"} aria-label={fullscreen ? "Exit full screen" : "Full screen"}><svg {...TOOL_ICON}>{fullscreen ? <path d="M8 3v5H3M16 3v5h5M8 21v-5H3M16 21v-5h5" /> : <path d="M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5" />}</svg></button>}
        {onEdit && current ? <button type="button" className="es-btn es-btn-sm es-btn-primary" onClick={() => onEdit(artifactId, n, selection || undefined)} title="Ask Echo to change this; select text first to change just that part">{selection ? "Edit selection" : "Edit with Echo"}</button> : null}
      </div>
      {n < latest && detail ? <div className="ap-context-bar"><span>Viewing version {n} of {latest}</span><button type="button" className="es-btn es-btn-sm" onClick={() => void restore()}>Restore this version</button></div> : null}
      <div className="ap-body" ref={bodyRef} onMouseUp={() => {
        const selected = window.getSelection();
        setSelection(selected?.anchorNode && bodyRef.current?.contains(selected.anchorNode) ? selected.toString().slice(0, 2000) : "");
      }}>
        {error ? (
          <div className="ap-empty">{error}</div>
        ) : !detail || !current ? (
          <div className="ap-empty" role="status">Loading…</div>
        ) : view === "source" || detail.kind === "code" ? (
          <div className="ap-scroll">
            <CodeBlock code={current.content} lang={detail.kind === "code" ? detail.language : SOURCE_LANG[detail.kind] || ""} meta={`title="${fileNameFor(detail)}"`} />
          </div>
        ) : runnable ? (
          <ArtifactFrame apiBase={apiBase} id={artifactId} version={n} title={current.title} reloadKey={reloadKey} />
        ) : detail.kind === "mermaid" ? (
          <div className="ap-scroll"><MermaidDiagram code={current.content} /></div>
        ) : (
          <div className="ap-scroll ap-doc"><article className="ap-page"><RichMarkdown text={current.content} /></article></div>
        )}
      </div>
    </section>
  );
}
