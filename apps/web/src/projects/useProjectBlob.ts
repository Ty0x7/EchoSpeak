import { useEffect, useState } from "react";

/** Keep the displayed media until its replacement is ready; revoke after commit. */
export function useProjectBlob(source: string, revision: string, enabled: boolean, image = false) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled || !source) return;
    const controller = new AbortController();
    let pending = "";
    setError("");
    void (async () => {
      try {
        const response = await fetch(source, { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error(`Media unavailable (${response.status})`);
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        pending = URL.createObjectURL(blob);
        if (image) {
          const preview = new Image(); preview.src = pending;
          await preview.decode();
        }
        if (controller.signal.aborted) return;
        setUrl(pending); pending = "";
      } catch (err) {
        if (!controller.signal.aborted) setError(String(err));
      } finally {
        if (pending) { URL.revokeObjectURL(pending); pending = ""; }
      }
    })();
    return () => { controller.abort(); if (pending) { URL.revokeObjectURL(pending); pending = ""; } };
  }, [source, revision, enabled, image]);
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);
  return { url, error };
}
