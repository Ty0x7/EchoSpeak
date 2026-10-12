import React, { useEffect, useState } from "react";
import { useWidgetEnv } from "./env";
import type { ObservationRef } from "./types";
import "./observation.css";

/** Fetch with the desktop's auth header; never expose a data-directory path or key in an image URL. */
export function ObservationView({ data, apiBase, expanded = false }: { data: ObservationRef; apiBase: string; expanded?: boolean }) {
  const { openObservation } = useWidgetEnv();
  const [image, setImage] = useState<{ url: string; src: string; error: string }>({ url: "", src: "", error: "" });
  const url = `${apiBase}/lean/observations/${encodeURIComponent(data.id)}/image?session_id=${encodeURIComponent(data.session_id)}`;
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    setImage({ url, src: "", error: "" });
    void fetch(url, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error(response.status === 404 ? "This screenshot has expired or is no longer available." : "This screenshot could not be opened.");
      if (!response.headers.get("content-type")?.startsWith("image/png")) throw new Error("Invalid screenshot response.");
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob);
      setImage({ url, src: objectUrl, error: "" });
    }).catch(error => {
      if (!controller.signal.aborted) setImage({ url, src: "", error: error instanceof Error ? error.message : "Screenshot unavailable." });
    });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [url]);
  const src = image.url === url ? image.src : "";
  const error = image.url === url ? image.error : "";
  const picture = src ? <img src={src} alt={`Screenshot: ${data.source}`} width={data.width} height={data.height} /> : <p role="status">{error || "Loading screenshot…"}</p>;
  return <figure className={`wg es-observation${expanded ? " is-expanded" : ""}`}>
    <figcaption>
      <span><strong>{data.source || "Screenshot"}</strong><small>{data.width} × {data.height} · {new Date(data.at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</small></span>
      {src && <a href={src} download={`${data.id}.png`} aria-label="Save screenshot">Save</a>}
    </figcaption>
    {!expanded && openObservation && src ? <button type="button" className="es-observation-preview" onClick={() => openObservation(data)} aria-label="Open screenshot in side panel">{picture}<span>Open screenshot</span></button> : <div className="es-observation-preview">{picture}</div>}
  </figure>;
}
