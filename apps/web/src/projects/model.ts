export type WorkRecord = {
  id: string; project_id: string; title: string; summary: string; agent: string; status: string; revision: number;
  artifact_id: string; artifact_version: number; report: string; echo_session_id: string; session_id: string; source_commit?: string;
  files_changed: string[]; media: { path: string; caption: string }[]; artifact_ids: string[]; related_records: string[];
  verification: { label: string; result: string; evidence: string }[]; remaining: string[]; imported_at: number; history?: WorkRecord[];
};
export type WorkspaceData = {
  documents: ProjectDocument[]; file_revision: string; scan_limited: boolean; scan_error: string;
  project: { id: string; name: string; description: string; workspace_root: string; sharing_enabled: boolean; brief: string };
  records: WorkRecord[]; artifacts: { id: string; title: string; kind: string; versions: number; excerpt: string }[];
  sessions: { id: string; title: string; at: number }[];
  research: { notes?: string; sources?: { title: string; url: string; text?: string; excerpt?: string }[]; session_id?: string }[];
  routines: { id: string; name: string; enabled: boolean; last_result_status: string }[];
  media: { id: string; name: string; media_kind: string; session_id: string }[];
};
export type ProjectDocument = { path: string; title: string; kind: "html" | "markdown"; markdown: string; thumbnail: string; size: number; modified_at: number; revision?: string };
export const SECTIONS = ["Work", "Files", "Conversations", "Research", "Routines"] as const;
export type Section = typeof SECTIONS[number];

/** Local links never become filesystem URLs or arbitrary API requests. */
export function localReference(value: string, documentPath = ""): string | null {
  if (!value || /^(?:[a-z][a-z\d+.-]*:|[\\/#])/i.test(value)) return null;
  let raw: string;
  try { raw = decodeURIComponent(value.split(/[?#]/)[0]); } catch { return null; }
  if (/[:\\\u0000-\u001f]/.test(raw) || raw.startsWith("/")) return null;
  const parts = documentPath.split("/").slice(0, -1);
  for (const part of raw.split("/")) {
    if (part === "..") { if (!parts.length) return null; parts.pop(); }
    else if (part && part !== ".") parts.push(part);
  }
  return parts.length ? parts.join("/") : null;
}

export async function request<T>(url: string, body?: unknown): Promise<T> {
  const response = await fetch(url, body === undefined ? { cache: "no-store" } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
  return data;
}
