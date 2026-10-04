import React, { useState } from "react";
import { ExternalLink } from "./env";

/** Model-authored image URLs load only on click. Even a same-origin URL can
 * proxy a remote image through /lean/media, so only inline data/blob images
 * are safe to show without a network request. */
export function SafeImage({ src, alt }: { src?: string; alt?: string }) {
  const url = String(src || "");
  const inline = /^data:image\/(?:png|jpeg|gif|webp|avif);base64,/i.test(url) || url.startsWith("blob:");
  const [approvedUrl, setApprovedUrl] = useState("");
  if (!/^(https?:|data:image\/|blob:|\/)/i.test(url)) return <span>{alt || ""}</span>;
  const shown = inline || approvedUrl === url;
  if (shown) return <img src={url} alt={alt || ""} loading="lazy" referrerPolicy="no-referrer" style={{ maxWidth: "100%" }} />;
  let host = "";
  try {
    host = url.startsWith("/") && !url.startsWith("//") ? "this app" : new URL(url, "http://localhost").host;
  } catch {
    host = "another site";
  }
  return (
    <button type="button" className="es-btn es-btn-sm" onClick={() => setApprovedUrl(url)} title={url}>
      Show image{alt ? ` “${alt}”` : ""} from {host}
    </button>
  );
}

/** react-markdown overrides for model output: safe links, click-to-load images. */
export const safeMarkdownComponents = {
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => <ExternalLink href={String(href || "")}>{children}</ExternalLink>,
  img: ({ src, alt }: { src?: string; alt?: string }) => <SafeImage src={src} alt={alt} />,
};
