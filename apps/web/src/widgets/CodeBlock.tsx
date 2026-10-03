import React, { useEffect, useMemo, useRef, useState } from "react";

type Hljs = typeof import("highlight.js").default;
let hljsPromise: Promise<Hljs> | null = null;
/** highlight.js loads on first use (its own chunk), so plain chats don't pay for it. */
const loadHljs = () => (hljsPromise ??= import("highlight.js/lib/common").then((m) => m.default as unknown as Hljs));

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="wg-ghost"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          window.setTimeout(() => setDone(false), 1400);
        } catch {
          // Clipboard blocked: nothing else to do.
        }
      }}
    >
      {done ? "Copied" : label}
    </button>
  );
}

/**
 * Parse the fence info: ```ts title="src/app.ts", ```ts:src/app.ts or ```diff.
 * Returns the language and an optional file name.
 */
export function parseFence(lang: string, meta: string): { language: string; filename: string } {
  let language = (lang || "").trim();
  let filename = "";
  const colon = language.indexOf(":");
  if (colon > 0) {
    filename = language.slice(colon + 1);
    language = language.slice(0, colon);
  }
  const titled = /(?:title|file|filename)=["']?([^"'\s]+)["']?/.exec(meta || "");
  if (titled) filename = titled[1];
  return { language: language.toLowerCase(), filename };
}

function DiffBody({ code }: { code: string }) {
  return (
    <pre className="wg-code-pre is-diff">
      <code>
        {code.split("\n").map((line, i) => {
          const kind = line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : line.startsWith("@@") ? "hunk" : "";
          return (
            <span key={i} className="wg-diff-line" data-kind={kind || undefined}>
              {line || " "}
              {"\n"}
            </span>
          );
        })}
      </code>
    </pre>
  );
}

export function CodeBlock({ code, lang = "", meta = "", streaming = false }: { code: string; lang?: string; meta?: string; streaming?: boolean }) {
  const { language, filename } = useMemo(() => parseFence(lang, meta), [lang, meta]);
  const [html, setHtml] = useState<string>("");
  const ref = useRef(code);
  ref.current = code;
  const isDiff = language === "diff" || language === "patch";
  useEffect(() => {
    if (isDiff || streaming || code.length > 60_000) {
      setHtml("");
      return;
    }
    let live = true;
    loadHljs()
      .then((hljs) => {
        if (!live) return;
        const known = language && hljs.getLanguage(language);
        // highlight.js escapes the source; its output is safe to inject.
        const result = known ? hljs.highlight(ref.current, { language, ignoreIllegals: true }) : hljs.highlightAuto(ref.current);
        setHtml(result.value);
      })
      .catch(() => setHtml(""));
    return () => {
      live = false;
    };
  }, [code, language, isDiff, streaming]);
  const lines = code.split("\n").length;
  return (
    <div className="wg-code" data-language={language || undefined}>
      <div className="wg-code-head">
        <span className="wg-code-name">{filename || language || "code"}</span>
        {filename && language ? <span className="wg-code-lang">{language}</span> : null}
        <span className="wg-code-lines">{lines} line{lines === 1 ? "" : "s"}</span>
        <CopyButton text={code} />
      </div>
      {isDiff ? (
        <DiffBody code={code} />
      ) : html ? (
        <pre className="wg-code-pre"><code className="hljs" dangerouslySetInnerHTML={{ __html: html }} /></pre>
      ) : (
        <pre className="wg-code-pre"><code>{code}</code></pre>
      )}
    </div>
  );
}
