import React, { useEffect, useState } from "react";
import { creationRequest, type CreationAsset, type CreationJob } from "./api";
import "./creations.css";

export function CreationPreview({ asset, apiBase }: { asset: CreationAsset; apiBase: string }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    setUrl(""); setError("");
    void fetch(`${apiBase}/creations/assets/${encodeURIComponent(asset.id)}/content`, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error("Media file unavailable"); return response.blob(); })
      .then(blob => { if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } })
      .catch(e => { if (!controller.signal.aborted) setError(String(e.message)); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [apiBase, asset.id]);
  return <div className="creation-preview">
    {error ? <p role="alert">{error}</p> : !url ? <p>Loading media…</p> : asset.media_kind === "video"
      ? <video src={url} controls preload="metadata" aria-label={asset.prompt || asset.name} />
      : <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={asset.prompt || asset.name} loading="lazy" /></a>}
    {url && <a className="es-btn es-btn-sm" href={url} download={asset.name.endsWith(asset.media_kind === "video" ? ".mp4" : ".png") ? asset.name : asset.name + (asset.media_kind === "video" ? ".mp4" : ".png")}>Download {asset.media_kind}</a>}
  </div>;
}

export function CreationCard({ id, apiBase }: { id: string; apiBase: string }) {
  const [job, setJob] = useState<CreationJob | null>(null);
  const [assets, setAssets] = useState<CreationAsset[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false, timer = 0;
    const refresh = async () => {
      try {
        const data = await creationRequest(apiBase, `/creations/jobs/${encodeURIComponent(id)}`);
        if (disposed) return;
        setJob(data.job); setAssets(data.assets); setError("");
        if (["queued", "running"].includes(data.job.status)) timer = window.setTimeout(refresh, 3000);
      } catch (e) { if (!disposed) { setError(e instanceof Error ? e.message : String(e)); timer = window.setTimeout(refresh, 10000); } }
    };
    void refresh();
    return () => { disposed = true; clearTimeout(timer); };
  }, [apiBase, id]);
  return <article className="creation-card">
    <strong>Creation · {job?.status || "Loading"}</strong>
    {job && <p>{job.prompt}</p>}
    {(error || job?.error) && <p role="alert">{error || job?.error}</p>}
    {assets.map(asset => <CreationPreview key={asset.id} asset={asset} apiBase={apiBase} />)}
    {job && ["queued", "running"].includes(job.status) && <button className="es-btn es-btn-sm" onClick={() => void creationRequest(apiBase, `/creations/jobs/${encodeURIComponent(id)}/cancel`, "POST").then(data => setError(data.detail)).catch(e => setError(e.message))}>Stop waiting</button>}
    <small>Saved in Creations when complete. Cloud jobs may continue and incur charges after stopping.</small>
  </article>;
}
