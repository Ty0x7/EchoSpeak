import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { LeanMessageData, LeanSegment } from "../lean/types";
import { ArtifactPanel } from "./ArtifactPanel";
import { ResearchPanel } from "./ResearchPanel";

/** One tool run shown in the Activity tab. */
export type ActivityItem = {
  id: string;
  agent: string;
  name: string;
  label: string;
  status: "running" | "done" | "failed";
  output: string;
  startedAt: number;
  durationMs?: number;
};

export const TERMINAL_TOOLS = new Set(["terminal", "process_start", "process_output", "process_stop"]);
const TERMINAL = TERMINAL_TOOLS;
const FILES = /^(file_|artifact_|checkpoint_undo|project_)/;
const WEB = new Set(["web_search", "safe_web_fetch", "browse_task", "youtube_transcript", "weather_live", "sports_live", "stock_history", "product_search", "video_search", "image_search"]);

export type ActivityFilter = "all" | "terminal" | "files" | "web";
export const activityKind = (name: string): Exclude<ActivityFilter, "all"> | "other" =>
  TERMINAL.has(name) ? "terminal" : FILES.test(name) ? "files" : WEB.has(name) ? "web" : "other";

/** Tool runs from a chat's messages (history + the live reply), oldest first. */
export function collectActivity(messages: (LeanMessageData | undefined)[]): ActivityItem[] {
  const items: ActivityItem[] = [];
  const seen = new Set<string>();
  for (const message of messages) {
    if (!message) continue;
    for (const seg of message.segments as LeanSegment[]) {
      if (seg.kind !== "tool") continue;
      const key = seg.id || `${message.messageId}-${seg.startedAt}-${seg.name}`;
      if (seen.has(key)) continue;
      seen.add(key);
      items.push({ id: key, agent: message.agent.name || "Echo", name: seg.name, label: seg.label, status: seg.status, output: seg.output, startedAt: seg.startedAt, durationMs: seg.durationMs });
    }
  }
  return items.sort((a, b) => a.startedAt - b.startedAt);
}

const fmt = (ms?: number) => (!ms ? "" : ms < 1000 ? `${ms} ms` : ms < 60000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`);

function ActivityRow({ item, defaultOpen }: { item: ActivityItem; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const kind = activityKind(item.name);
  return (
    <li className="rp-act" data-status={item.status} data-kind={kind}>
      <button type="button" className="rp-act-head" onClick={() => setOpen((v) => !v)} aria-expanded={open} disabled={!item.output}>
        <span className="rp-act-state" aria-hidden>{item.status === "running" ? <span className="lm-spin" /> : item.status === "done" ? "✓" : "✕"}</span>
        <span className="rp-act-label">{item.label}</span>
        <small>{item.agent}{item.durationMs ? ` · ${fmt(item.durationMs)}` : ""}</small>
      </button>
      {open && item.output ? <pre className={`rp-act-out${kind === "terminal" ? " is-terminal" : ""}`}>{item.output}</pre> : null}
    </li>
  );
}

export function ActivityView({ items, active = true }: { items: ActivityItem[]; active?: boolean }) {
  const [filter, setFilter] = useState<ActivityFilter>("all");
  const listRef = useRef<HTMLDivElement>(null);
  const shown = useMemo(() => (filter === "all" ? items : items.filter((i) => activityKind(i.name) === filter)), [items, filter]);
  const running = items.some((i) => i.status === "running");
  useLayoutEffect(() => {
    const el = listRef.current;
    if (el && running && active) el.scrollTop = el.scrollHeight;
  }, [items, running, active]);
  const count = (f: ActivityFilter) => (f === "all" ? items.length : items.filter((i) => activityKind(i.name) === f).length);
  return (
    <div className="rp-activity">
      <div className="rp-filters" role="radiogroup" aria-label="Show">
        {(["all", "terminal", "files", "web"] as ActivityFilter[]).map((f) => (
          <button key={f} type="button" role="radio" aria-checked={filter === f} className={filter === f ? "is-on" : ""} onClick={() => setFilter(f)}>
            {f === "all" ? "All" : f === "terminal" ? "Terminal" : f === "files" ? "Files" : "Web"}
            <small>{count(f)}</small>
          </button>
        ))}
      </div>
      <div className="rp-act-list" ref={listRef}>
        {shown.length === 0 ? (
          <div className="ap-empty">{items.length ? "Nothing of this kind in this chat." : "When agents run commands, edit files or search, each step shows up here."}</div>
        ) : (
          <ol>
            {shown.map((item, i) => <ActivityRow key={item.id} item={item} defaultOpen={activityKind(item.name) === "terminal" && i >= shown.length - 2} />)}
          </ol>
        )}
      </div>
    </div>
  );
}

export const RIGHT_PANEL_DEFAULT = 440;
export const RIGHT_PANEL_MIN = 320;
/** The chat keeps at least this much room next to the panel. */
export const CHAT_MIN = 460;
const WIDTH_KEY = "echospeak.rightpanel.width.v1";

export function loadPanelWidth(): number {
  try {
    const value = Number(window.localStorage.getItem(WIDTH_KEY));
    return Number.isFinite(value) && value >= RIGHT_PANEL_MIN ? value : RIGHT_PANEL_DEFAULT;
  } catch {
    return RIGHT_PANEL_DEFAULT;
  }
}

export function savePanelWidth(width: number): void {
  try {
    window.localStorage.setItem(WIDTH_KEY, String(Math.round(width)));
  } catch {
    // Storage blocked: the width just won't be remembered.
  }
}

/** Panel width kept between its minimum and what leaves the chat CHAT_MIN wide. */
export function clampPanelWidth(width: number, room: number): number {
  const max = Math.max(RIGHT_PANEL_MIN, room - CHAT_MIN);
  return Math.round(Math.min(max, Math.max(RIGHT_PANEL_MIN, width)));
}

export type RightTab = "artifact" | "activity" | "research";

/**
 * One right sidebar for artifacts, research and activity. Its views share
 * tabs, expansion, close controls and remembered width.
 */
export function RightPanel({
  apiBase,
  sessionId,
  tab,
  onTab,
  artifact,
  activity,
  width,
  onWidth,
  measureRoom,
  overlay,
  onClose,
}: {
  apiBase: string;
  sessionId: string;
  tab: RightTab;
  onTab(tab: RightTab): void;
  artifact: { id: string; version?: number } | null;
  activity: ActivityItem[];
  width: number;
  onWidth(width: number): void;
  /** Layout px available to chat + panel together, and screen px per layout px (the desktop shell can be zoomed). */
  measureRoom(): { room: number; scale: number };
  overlay: boolean;
  onClose(): void;
}) {
  const [dragging, setDragging] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [visitedArtifactId, setVisitedArtifactId] = useState("");
  const panelId = useId();
  const startRef = useRef({ x: 0, width });
  const active: RightTab = tab === "artifact" && !artifact ? "activity" : tab;
  const running = activity.filter((a) => a.status === "running").length;
  useEffect(() => {
    if (active === "artifact" && artifact) setVisitedArtifactId(artifact.id);
  }, [active, artifact?.id]);
  useEffect(() => {
    if (!dragging) return;
    document.body.classList.add("rp-resizing");
    return () => document.body.classList.remove("rp-resizing");
  }, [dragging]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (expanded) { event.preventDefault(); setExpanded(false); }
      else if (overlay) { event.preventDefault(); onClose(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded, overlay, onClose]);
  const tabs: RightTab[] = artifact ? ["artifact", "activity", "research"] : ["activity", "research"];
  return (
    <aside className={`rp${overlay ? " is-overlay" : ""}${expanded ? " is-expanded" : ""}`} aria-label="Side panel" data-dragging={dragging ? "true" : undefined}>
      {!overlay && !expanded ? (
        <div
          className="rp-resize"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize side panel"
          aria-valuenow={Math.round(width)}
          aria-valuemin={RIGHT_PANEL_MIN}
          tabIndex={0}
          title="Drag to resize · double-click to reset"
          onPointerDown={(event) => {
            event.preventDefault();
            event.currentTarget.setPointerCapture(event.pointerId);
            startRef.current = { x: event.clientX, width };
            setDragging(true);
          }}
          onPointerMove={(event) => {
            if (!dragging) return;
            const { room, scale } = measureRoom();
            onWidth(clampPanelWidth(startRef.current.width + (startRef.current.x - event.clientX) / (scale || 1), room));
          }}
          onPointerUp={(event) => {
            event.currentTarget.releasePointerCapture(event.pointerId);
            setDragging(false);
          }}
          onPointerCancel={() => setDragging(false)}
          onDoubleClick={() => onWidth(clampPanelWidth(RIGHT_PANEL_DEFAULT, measureRoom().room))}
          onKeyDown={(event) => {
            const step = event.shiftKey ? 80 : 24;
            if (event.key === "ArrowLeft") onWidth(clampPanelWidth(width + step, measureRoom().room));
            else if (event.key === "ArrowRight") onWidth(clampPanelWidth(width - step, measureRoom().room));
            else return;
            event.preventDefault();
          }}
        >
          <span aria-hidden />
        </div>
      ) : null}
      <div className="rp-tabs" role="tablist" aria-label="Side panel" onKeyDown={event => {
        const current = (event.target as HTMLElement).closest<HTMLButtonElement>("[role=tab]");
        if (!current) return;
        const index = tabs.indexOf(current.dataset.tab as RightTab);
        const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
          : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
          : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
        if (next < 0) return;
        event.preventDefault();
        onTab(tabs[next]);
        event.currentTarget.querySelector<HTMLButtonElement>(`[data-tab="${tabs[next]}"]`)?.focus();
      }}>
        {artifact ? (
          <button type="button" role="tab" data-tab="artifact" id={`${panelId}-artifact-tab`} aria-controls={`${panelId}-artifact`} tabIndex={active === "artifact" ? 0 : -1} aria-selected={active === "artifact"} className={active === "artifact" ? "is-on" : ""} onClick={() => onTab("artifact")}>
            Artifact
          </button>
        ) : null}
        <button type="button" role="tab" data-tab="activity" id={`${panelId}-activity-tab`} aria-controls={`${panelId}-activity`} tabIndex={active === "activity" ? 0 : -1} aria-selected={active === "activity"} className={active === "activity" ? "is-on" : ""} onClick={() => onTab("activity")}>
          Activity
          {running ? <span className="rp-live" aria-label={`${running} running`} /> : activity.length ? <small>{activity.length}</small> : null}
        </button>
        <button type="button" role="tab" data-tab="research" id={`${panelId}-research-tab`} aria-controls={`${panelId}-research`} tabIndex={active === "research" ? 0 : -1} aria-selected={active === "research"} className={active === "research" ? "is-on" : ""} onClick={() => onTab("research")}>Research</button>
        <span className="rp-spacer" />
        {!overlay && <button type="button" className="rp-close" onClick={() => setExpanded(value => !value)} aria-label={expanded ? "Restore side panel" : "Expand side panel"} aria-pressed={expanded} title={expanded ? "Restore panel width" : "Expand panel"}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{expanded ? <path d="M8 3v5H3M16 3v5h5M8 21v-5H3M16 21v-5h5" /> : <path d="M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5" />}</svg>
        </button>}
        <button type="button" className="rp-close" onClick={onClose} aria-label="Close side panel" title="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden><path d="M6 6l12 12M18 6 6 18" /></svg>
        </button>
      </div>
      <div className="rp-body">
        {artifact && <div className="rp-tab-panel" role="tabpanel" id={`${panelId}-artifact`} aria-labelledby={`${panelId}-artifact-tab`} hidden={active !== "artifact"}>
          {(active === "artifact" || visitedArtifactId === artifact.id) && <ArtifactPanel key={artifact.id} embedded apiBase={apiBase} artifactId={artifact.id} version={artifact.version} onClose={onClose} />}
        </div>}
        <div className="rp-tab-panel" role="tabpanel" id={`${panelId}-research`} aria-labelledby={`${panelId}-research-tab`} hidden={active !== "research"}>
          <ResearchPanel key={sessionId} apiBase={apiBase} sessionId={sessionId} active={active === "research"} />
        </div>
        <div className="rp-tab-panel" role="tabpanel" id={`${panelId}-activity`} aria-labelledby={`${panelId}-activity-tab`} hidden={active !== "activity"}>
          <ActivityView key={sessionId} items={activity} active={active === "activity"} />
        </div>
      </div>
    </aside>
  );
}
