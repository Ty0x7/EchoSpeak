export async function creationRequest(base: string, path: string, method = "GET", body?: unknown) {
  const response = await fetch(`${base}${path}`, { method, headers: body === undefined ? undefined : { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === "string" ? error.detail : `Request failed (${response.status})`);
  }
  return response.json();
}
export type CreationAsset = { id: string; name: string; media_kind: "image" | "video"; prompt: string; provider: string; model: string; session_id: string; archived: boolean };
export type CreationJob = { id: string; status: string; prompt: string; error: string; provider_id: string; cancellation_requested: boolean };
