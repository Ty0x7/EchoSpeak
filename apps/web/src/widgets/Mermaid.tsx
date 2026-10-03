import React, { useEffect, useId, useState } from "react";
import { CodeBlock } from "./CodeBlock";

type MermaidApi = typeof import("mermaid").default;
let mermaidPromise: Promise<MermaidApi> | null = null;

/** Mermaid is large, so it loads on the first diagram. Strict mode: no HTML labels, no click handlers. */
function loadMermaid(): Promise<MermaidApi> {
  mermaidPromise ??= import("mermaid").then((m) => {
    const mermaid = m.default;
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "dark",
      fontFamily: "Inter, Segoe UI, system-ui, sans-serif",
      themeVariables: { background: "transparent", primaryColor: "#1d1d20", primaryTextColor: "#f4f4f5", lineColor: "#8a8a90", primaryBorderColor: "#3a3a40" },
    });
    return mermaid;
  });
  return mermaidPromise;
}

/** Render Mermaid source to SVG markup (mermaid sanitises it in strict mode). */
export async function renderMermaid(id: string, source: string): Promise<string> {
  const mermaid = await loadMermaid();
  const { svg } = await mermaid.render(id, source);
  return svg;
}

export function MermaidDiagram({ code, streaming = false }: { code: string; streaming?: boolean }) {
  const rawId = useId();
  const id = `mmd-${rawId.replace(/[^a-zA-Z0-9]/g, "")}`;
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [showSource, setShowSource] = useState(false);
  useEffect(() => {
    if (streaming) return; // half-written diagrams don't parse; draw once the reply is done
    let live = true;
    const timer = window.setTimeout(() => {
      renderMermaid(id, code.trim())
        .then((out) => {
          if (!live) return;
          setSvg(out);
          setError("");
        })
        .catch((err) => {
          if (!live) return;
          setSvg("");
          setError(String(err?.message || err || "Could not draw this diagram").split("\n")[0].slice(0, 160));
        })
        .finally(() => document.getElementById(`d${id}`)?.remove());
    }, 120);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [code, id, streaming]);
  if (error) {
    return (
      <div className="wg-diagram-error">
        <small>Couldn't draw this diagram ({error}). Here is its source:</small>
        <CodeBlock code={code} lang="mermaid" />
      </div>
    );
  }
  return (
    <figure className="wg wg-diagram">
      <header className="wg-head">
        <figcaption>Diagram</figcaption>
        <button type="button" className="wg-ghost" onClick={() => setShowSource((v) => !v)} aria-pressed={showSource}>{showSource ? "Diagram" : "Source"}</button>
      </header>
      {showSource ? (
        <CodeBlock code={code} lang="mermaid" />
      ) : svg ? (
        <div className="wg-diagram-svg" dangerouslySetInnerHTML={{ __html: svg }} />
      ) : (
        <div className="wg-diagram-wait" role="status">{streaming ? "Writing diagram…" : "Drawing diagram…"}</div>
      )}
    </figure>
  );
}
