import { create } from "zustand";

export type SurfaceKind = "surfaces" | "files" | "file" | "diff" | "terminal" | "browser" | "pull_requests" | "linked_prs" | "device" | "usage" | "research" | "activity" | "artifact" | "screen";
export type Surface = { id: string; kind: SurfaceKind; title: string; path?: string; terminalId?: string };
export type WorkbenchContext = { project_id: string; external_session_id: string };
export const surfaceTitle: Record<SurfaceKind, string> = { surfaces: "Open a surface", files: "Files", file: "File", diff: "Changes", terminal: "Terminal", browser: "Browser", pull_requests: "Pull requests", linked_prs: "Linked pull requests", device: "Device", usage: "Usage", research: "Research", activity: "Activity", artifact: "Artifact", screen: "Screen" };
export const contextKey = (context: WorkbenchContext) => `${context.project_id}:${context.external_session_id}`;

type State = {
  context: WorkbenchContext; tabs: Surface[]; active: string; rightOpen: boolean; bottomOpen: boolean;
  detailsOpen: boolean; bottomHeight: number; terminalIds: Record<string, string[]>; revision: number;
  setContext(context: WorkbenchContext): void; open(kind: SurfaceKind, props?: Partial<Surface>): void;
  select(id: string): void; close(id: string): void; toggleRight(): void; toggleBottom(): void;
  toggleDetails(): void; setBottomHeight(value: number): void; refresh(): void;
  addTerminal(id: string, context?: WorkbenchContext): void; removeTerminal(id: string, context?: WorkbenchContext): void;
};

function height(): number {
  try { return Math.max(150, Math.min(600, Number(localStorage.getItem("echospeak.workbench.height")) || 260)); }
  catch { return 260; }
}

export const useWorkbench = create<State>((set, get) => ({
  context: { project_id: "", external_session_id: "" }, tabs: [], active: "", rightOpen: false,
  bottomOpen: false, detailsOpen: false, bottomHeight: height(), terminalIds: {}, revision: 0,
  setContext(context) {
    if (contextKey(context) === contextKey(get().context)) return;
    // Resource tabs cannot silently follow a different repository or agent.
    set({ context, tabs: [], active: "", detailsOpen: false, revision: get().revision + 1 });
  },
  open(kind, props = {}) {
    const id = props.id || (props.path ? `${kind}:${props.path}` : kind);
    const tab = { id, kind, title: props.title || surfaceTitle[kind], ...props };
    set(state => { const tabs = state.tabs.filter(item => kind === "surfaces" || item.kind !== "surfaces"); return { tabs: tabs.some(item => item.id === id) ? tabs : [...tabs, tab], active: id, rightOpen: true }; });
  },
  select: active => set({ active, rightOpen: true }),
  close(id) {
    set(state => {
      const tabs = state.tabs.filter(tab => tab.id !== id);
      return { tabs, active: state.active === id ? tabs[tabs.length - 1]?.id || "" : state.active };
    });
  },
  toggleRight() {
    if (!get().rightOpen && !get().tabs.length) get().open("surfaces");
    else set({ rightOpen: !get().rightOpen });
  },
  toggleBottom: () => set(state => ({ bottomOpen: !state.bottomOpen })),
  toggleDetails: () => set(state => ({ detailsOpen: !state.detailsOpen })),
  setBottomHeight(value) {
    const bottomHeight = Math.round(Math.max(150, Math.min(innerHeight * .65, value)));
    set({ bottomHeight });
    try { localStorage.setItem("echospeak.workbench.height", String(bottomHeight)); } catch { /* optional persistence */ }
  },
  refresh: () => set(state => ({ revision: state.revision + 1 })),
  addTerminal(id, context) { const key = contextKey(context || get().context); set(state => ({ terminalIds: { ...state.terminalIds, [key]: [...(state.terminalIds[key] || []), id] } })); },
  removeTerminal(id, context) { const key = contextKey(context || get().context); set(state => ({ terminalIds: { ...state.terminalIds, [key]: (state.terminalIds[key] || []).filter(item => item !== id) } })); },
}));

export function workbenchUrl(apiBase: string, context: WorkbenchContext, resource: string, extra: Record<string, string> = {}) {
  const query = new URLSearchParams({ external_session_id: context.external_session_id, ...extra });
  return `${apiBase}/workbench/${encodeURIComponent(context.project_id)}/${resource}?${query}`;
}

/** Only sandboxed loopback previews are embedded; backend and credential URLs are refused. */
export function previewUrl(value: string, apiBase: string): string | null {
  try {
    const url = new URL(value);
    const backend = new URL(apiBase, typeof location === "undefined" ? "http://127.0.0.1" : location.href);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
    if (!["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) return null;
    if ((url.port || (url.protocol === "https:" ? "443" : "80")) === (backend.port || (backend.protocol === "https:" ? "443" : "80"))) return null;
    return url.href;
  } catch { return null; }
}
