import React, { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { CodeBlock, CopyButton } from "./CodeBlock";
import { mermaidErrorDetail, repairMermaid } from "./mermaidRepair";

type MermaidApi = typeof import("mermaid").default;
type Theme = "dark" | "light";
let mermaidPromise: Promise<MermaidApi> | null = null;
let initializedTheme: Theme | null = null;
// mermaid.render keeps global state (config, a temporary DOM node): one render at a time.
let queue: Promise<unknown> = Promise.resolve();
// Each render gets a fresh element id. Rendering under an id already on the page (a diagram
// drawn earlier) makes Mermaid measure and edit that visible SVG instead of its scratch copy.
let renderCount = 0;
// Drawn results by theme and source. A diagram that remounts (a reply finishing, scrolling back,
// reopening a chat) shows at once instead of drawing again. Failures are kept too.
type Drawn = { svg: string; repaired: boolean } | { error: { summary: string; detail: string; kind?: "syntax" | "render" } };
const drawn = new Map<string, Drawn>();
const DRAWN_LIMIT = 60;
const drawnKey = (theme: Theme, code: string) => `${theme}\u0000${code.trim()}`;
function remember(key: string, value: Drawn) {
  drawn.delete(key);
  drawn.set(key, value);
  if (drawn.size > DRAWN_LIMIT) drawn.delete(drawn.keys().next().value as string);
}

const THEMES: Record<Theme, Record<string, string>> = {
  dark: { background: "transparent", primaryColor: "#1d1d20", primaryTextColor: "#f4f4f5", lineColor: "#8a8a90", primaryBorderColor: "#3a3a40",
    secondaryColor: "#25252a", tertiaryColor: "#18181b", noteBkgColor: "#2a2a30", noteTextColor: "#f4f4f5" },
  light: { background: "transparent", primaryColor: "#f4f4f6", primaryTextColor: "#1f1f24", lineColor: "#6b6b75", primaryBorderColor: "#c9c9d1",
    secondaryColor: "#eceef3", tertiaryColor: "#fafafb", noteBkgColor: "#fff7e0", noteTextColor: "#1f1f24" },
};

export function currentTheme(): Theme {
  return typeof document !== "undefined" && document.documentElement.dataset.esTheme === "light" ? "light" : "dark";
}

/** Mermaid is large, so it loads on the first diagram. Strict mode: no HTML labels, no click handlers. */
function loadMermaid(): Promise<MermaidApi> {
  mermaidPromise ??= import("mermaid").then((m) => m.default);
  return mermaidPromise;
}

function configure(mermaid: MermaidApi, theme: Theme) {
  if (initializedTheme === theme) return;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict",
    // Only the "base" theme honours themeVariables.
    theme: "base",
    fontFamily: "Inter, Segoe UI, system-ui, sans-serif",
    themeVariables: { ...THEMES[theme], darkMode: theme === "dark" },
    flowchart: { useMaxWidth: true },
  });
  initializedTheme = theme;
}

/** Render Mermaid source to SVG markup (mermaid sanitises it in strict mode). Renders run one at a time. */
export function renderMermaid(id: string, source: string, theme: Theme = currentTheme()): Promise<string> {
  const job = queue.then(async () => {
    const mermaid = await loadMermaid();
    configure(mermaid, theme);
    const renderId = `${id}-${++renderCount}`;
    // Parse first: a syntax mistake is the model's; an exception after a clean parse (the live test's
    // "Cannot read properties of undefined (reading 'valueOf')") is Mermaid failing to draw valid input.
    try {
      await mermaid.parse(source);
    } catch (error) {
      throw Object.assign(error instanceof Error ? error : new Error(String(error)), { mermaidKind: "syntax" });
    }
    try {
      const { svg } = await mermaid.render(renderId, source);
      return svg;
    } catch (error) {
      throw Object.assign(error instanceof Error ? error : new Error(String(error)), { mermaidKind: "render" });
    } finally {
      // A failed render can leave its scratch element behind.
      document.getElementById(`d${renderId}`)?.remove();
    }
  });
  queue = job.catch(() => undefined);
  return job;
}

/**
 * Draw the diagram as written; if Mermaid rejects it, try a conservatively repaired copy
 * (quoted labels, edge labels, arrows: widgets/mermaidRepair.ts). Returns which one drew.
 */
export async function drawMermaid(id: string, source: string, theme: Theme = currentTheme()): Promise<{ svg: string; repaired: boolean }> {
  const original = source.trim();
  try {
    return { svg: await renderMermaid(id, original, theme), repaired: false };
  } catch (error) {
    const fixed = repairMermaid(original);
    // Repair addresses syntax only; rewriting valid input doesn't fix a renderer crash.
    if (fixed === original || (error as { mermaidKind?: string })?.mermaidKind === "render") throw error;
    try {
      return { svg: await renderMermaid(`${id}r`, fixed, theme), repaired: true };
    } catch {
      throw error; // report the problem in what the model wrote
    }
  }
}

/** The app's light/dark theme, following changes (Settings › Appearance or the system). */
function useTheme(): Theme {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  useEffect(() => {
    if (typeof MutationObserver === "undefined") return;
    const observer = new MutationObserver(() => setTheme(currentTheme()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-es-theme"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

export function MermaidDiagram({ code, streaming = false }: { code: string; streaming?: boolean }) {
  const rawId = useId();
  const id = `mmd-${rawId.replace(/[^a-zA-Z0-9]/g, "")}`;
  const theme = useTheme();
  const cached = streaming ? undefined : drawn.get(drawnKey(theme, code));
  const [svg, setSvg] = useState(cached && "svg" in cached ? cached.svg : "");
  const [repaired, setRepaired] = useState(Boolean(cached && "svg" in cached && cached.repaired));
  const [error, setError] = useState<{ summary: string; detail: string; kind?: "syntax" | "render" } | null>(cached && "error" in cached ? cached.error : null);
  const [showSource, setShowSource] = useState(false);
  useEffect(() => {
    if (streaming) return; // a half-written diagram doesn't parse; draw once its fence closes
    const key = drawnKey(theme, code);
    const known = drawn.get(key);
    if (known) {
      setSvg("svg" in known ? known.svg : "");
      setRepaired("svg" in known && known.repaired);
      setError("error" in known ? known.error : null);
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      drawMermaid(id, code, theme)
        .then((out) => {
          remember(key, out);
          if (!live) return;
          setSvg(out.svg);
          setRepaired(out.repaired);
          setError(null);
        })
        .catch((err) => {
          const error = mermaidErrorDetail(err);
          remember(key, { error });
          if (!live) return;
          setSvg("");
          setError(error);
        });
    }, 60);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [code, id, streaming, theme]);
  if (error) {
    return (
      <figure className="wg wg-diagram wg-diagram-failed" role="group" aria-label="Diagram that couldn't be drawn">
        <header className="wg-head">
          <figcaption>Diagram couldn't be drawn</figcaption>
          <CopyButton text={code} label="Copy source" />
        </header>
        <div className="wg-diagram-error" role="note">
          <strong>{error.summary}</strong>
          {error.detail ? <pre>{error.detail}</pre> : null}
          <small>{error.kind === "render"
            ? "The source is valid Mermaid, but Mermaid hit an internal error drawing it. Ask for a simpler diagram, or copy the source into a Mermaid editor."
            : "The Mermaid source below has a syntax error. Ask to fix the diagram, or copy it into a Mermaid editor."}</small>
        </div>
        <CodeBlock code={code} lang="mermaid" />
      </figure>
    );
  }
  return (
    <figure className="wg wg-diagram" data-theme={theme}>
      <header className="wg-head">
        <figcaption>Diagram{repaired ? <small className="wg-diagram-note" title="The model's Mermaid had a small syntax mistake (for example an unquoted label); EchoSpeak quoted it to draw it. Source shows the original."> · syntax fixed</small> : null}</figcaption>
        <button type="button" className="wg-ghost" onClick={() => setShowSource((v) => !v)} aria-pressed={showSource}>{showSource ? "Diagram" : "Source"}</button>
      </header>
      {showSource ? (
        <CodeBlock code={code} lang="mermaid" />
      ) : svg ? (
        <DiagramSvg svg={svg} />
      ) : (
        <div className="wg-diagram-wait" role="status">{streaming ? "Writing diagram…" : "Drawing diagram…"}</div>
      )}
    </figure>
  );
}

/** Below this share of its natural size a diagram's labels get too small to read. */
const MIN_SCALE = 0.7;

/**
 * Fit the diagram to its column, unless that would shrink it past legibility (a wide chart on a
 * phone): then show it at a readable size and let it scroll sideways inside its card.
 */
function DiagramSvg({ svg }: { svg: string }) {
  const host = useRef<HTMLDivElement>(null);
  const [scroll, setScroll] = useState(false);
  useLayoutEffect(() => {
    const element = host.current;
    const drawing = element?.querySelector("svg");
    if (!element || !drawing) return;
    const natural = drawing.viewBox?.baseVal?.width || 0;
    const fit = () => setScroll(natural > 0 && element.clientWidth > 0 && element.clientWidth < natural * MIN_SCALE);
    fit();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(fit);
    observer.observe(element);
    return () => observer.disconnect();
  }, [svg]);
  useLayoutEffect(() => {
    const drawing = host.current?.querySelector("svg");
    if (!drawing) return;
    const natural = drawing.viewBox?.baseVal?.width || 0;
    drawing.style.width = scroll && natural ? `${Math.round(natural * MIN_SCALE)}px` : "";
    drawing.style.maxWidth = scroll ? "none" : "";
    drawing.style.maxHeight = scroll ? "none" : "";
  }, [scroll, svg]);
  return <div ref={host} className={`wg-diagram-svg${scroll ? " is-scroll" : ""}`} role="img" aria-label="Diagram"
    tabIndex={scroll ? 0 : undefined} dangerouslySetInnerHTML={{ __html: svg }} />;
}
