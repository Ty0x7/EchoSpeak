import React, { createContext, useContext, useEffect, useState } from "react";
import { isDesktopRuntime, openDesktopExternalUrl } from "../desktop/bridge";
import { safeUrl } from "./validate";

export type WidgetEnv = {
  apiBase: string;
  /** Open an artifact in the side panel. */
  openArtifact?(id: string, version?: number): void;
};

const Ctx = createContext<WidgetEnv>({ apiBase: "" });

export function WidgetEnvProvider({ value, children }: { value: WidgetEnv; children: React.ReactNode }) {
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useWidgetEnv = () => useContext(Ctx);

/** Links from answers open in the system browser, never inside the app's webview. */
export function openExternal(url: string): void {
  const clean = safeUrl(url);
  if (!clean) return;
  if (isDesktopRuntime()) {
    openDesktopExternalUrl(clean).catch(() => window.open(clean, "_blank", "noopener,noreferrer"));
    return;
  }
  window.open(clean, "_blank", "noopener,noreferrer");
}

export function ExternalLink({ href, children, className, title }: { href: string; children: React.ReactNode; className?: string; title?: string }) {
  const clean = safeUrl(href);
  if (!clean) return <span className={className}>{children}</span>;
  return (
    <a
      href={clean}
      className={className}
      title={title}
      target="_blank"
      rel="noreferrer noopener"
      onClick={(event) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey) return;
        event.preventDefault();
        openExternal(clean);
      }}
    >
      {children}
    </a>
  );
}

/** Proxied path for a remote image (fetched by the backend with size/time limits). */
export function proxiedImage(apiBase: string, url: string | undefined): string {
  const clean = safeUrl(url);
  return clean ? `${apiBase}/lean/media?url=${encodeURIComponent(clean)}` : "";
}

/**
 * The desktop backend requires an auth header that <img src> can't send, so
 * there the image is fetched (the app's fetch adds the header) and shown from
 * a blob URL. In the browser the proxy URL is used directly.
 */
function useImageSrc(url: string): { src: string; failed: boolean } {
  const needsFetch = Boolean(url) && isDesktopRuntime();
  const [state, setState] = useState<{ src: string; failed: boolean }>({ src: needsFetch ? "" : url, failed: !url });
  useEffect(() => {
    if (!url) {
      setState({ src: "", failed: true });
      return;
    }
    if (!needsFetch) {
      setState({ src: url, failed: false });
      return;
    }
    let objectUrl = "";
    let live = true;
    fetch(url)
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        if (live) setState({ src: objectUrl, failed: false });
        else URL.revokeObjectURL(objectUrl);
      })
      .catch(() => live && setState({ src: "", failed: true }));
    return () => {
      live = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url, needsFetch]);
  return state;
}

/** A remote image through the proxy, with a neutral placeholder when it is missing or broken. */
export function RemoteImage({ src, alt = "", className, fit = "cover" }: { src?: string; alt?: string; className?: string; fit?: "cover" | "contain" }) {
  const { apiBase } = useWidgetEnv();
  const image = useImageSrc(proxiedImage(apiBase, src));
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [image.src]);
  if (image.failed || broken) {
    return (
      <span className={`wg-img wg-img-missing ${className || ""}`} role="img" aria-label={alt || "Image unavailable"}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden><rect x="3.5" y="4.5" width="17" height="15" rx="2" /><path d="m4 17 5-5 4 4 3-3 4 4" /><circle cx="15.5" cy="9" r="1.4" /></svg>
      </span>
    );
  }
  if (!image.src) return <span className={`wg-img wg-img-loading ${className || ""}`} aria-hidden />;
  return <img className={`wg-img ${className || ""}`} src={image.src} alt={alt} loading="lazy" decoding="async" style={{ objectFit: fit }} onError={() => setBroken(true)} draggable={false} />;
}
