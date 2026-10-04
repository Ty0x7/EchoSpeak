import React, { useCallback, useEffect, useState } from "react";
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
}: {
  apiBase: string;
  artifactId: string;
  /** Version to show; undefined follows the latest. */
  version?: number;
  overlay?: boolean;
  /** Inside the right panel, which has its own close button and placement. */
  embedded?: boolean;
  onClose(): void;
}) {
  const [detail, setDetail] = useState<ArtifactDetail | null>(null);
  const [error, setError] = useState("");
  const [shown, setShown] = useState<number | undefined>(version);
  const [view, setView] = useState<"preview" | "source">("preview");
  const [fullscreen, setFullscreen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => setShown(version), [artifactId, version]);

  const load = useCallback(
    async (n?: number) => {
      try {
        const response = await fetch(`${apiBase}/lean/artifacts/${artifactId}${n ? `?version=${n}` : ""}`);
        if (!response.ok) throw new Error(response.status === 404 ? "This artifact no longer exists." : `HTTP ${response.status}`);
        setDetail(await response.json());
        setError("");
      } catch (err: any) {
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
        <div className="ap-title">
          <strong title={current?.title || detail?.title}>{current?.title || detail?.title || "Artifact"}</strong>
          <small>
            {detail ? `${detail.kind === "code" ? detail.language || "code" : detail.kind.toUpperCase()} · v${n}${detail.versions > 1 ? ` of ${detail.versions}` : ""}` : "…"}
          </small>
        </div>
        <div className="ap-versions" role="group" aria-label="Versions">
          <button type="button" className="wg-ghost" disabled={!detail || n <= (detail.history[0]?.n || 1)} onClick={() => setShown(Math.max(1, n - 1))} aria-label="Previous version">‹</button>
          <button type="button" className="wg-ghost" disabled={!detail || n >= latest} onClick={() => setShown(n + 1 >= latest ? undefined : n + 1)} aria-label="Next version">›</button>
        </div>
        {!embedded ? <button type="button" className="wg-ghost ap-close" onClick={onClose} aria-label="Close artifact">✕</button> : null}
      </header>
      <div className="ap-tools">
        {hasPreview ? (
          <div className="ap-seg" role="radiogroup" aria-label="View">
            <button type="button" role="radio" aria-checked={view === "preview"} className={view === "preview" ? "is-on" : ""} onClick={() => setView("preview")}>Preview</button>
            <button type="button" role="radio" aria-checked={view === "source"} className={view === "source" ? "is-on" : ""} onClick={() => setView("source")}>Source</button>
          </div>
        ) : null}
        <span className="ap-spacer" />
        {n < latest && detail ? <button type="button" className="wg-btn" onClick={() => void restore()}>Restore v{n}</button> : null}
        {current ? <CopyButton text={current.content} /> : null}
        {current && detail ? <button type="button" className="wg-ghost" onClick={() => download(fileNameFor(detail), current.content)}>Download</button> : null}
        {runnable ? <button type="button" className="wg-ghost" onClick={() => void openInBrowser()} title="Open in your browser (still sandboxed)">Open ↗</button> : null}
        {runnable && view === "preview" ? <button type="button" className="wg-ghost" onClick={() => setReloadKey((k) => k + 1)} title="Restart the app">Reload</button> : null}
        <button type="button" className="wg-ghost" onClick={() => setFullscreen((v) => !v)} aria-pressed={fullscreen}>{fullscreen ? "Exit full screen" : "Full screen"}</button>
      </div>
      <div className="ap-body">
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
          <div className="ap-scroll ap-doc"><RichMarkdown text={current.content} /></div>
        )}
      </div>
    </section>
  );
}
