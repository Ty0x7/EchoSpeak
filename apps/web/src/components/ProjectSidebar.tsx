import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  loadStackLayout,
  pairShare,
  resetPair,
  resizePair,
  saveStackLayout,
  sectionFractions,
  toggleSection,
  type SectionKey,
  type StackLayout,
} from "./sidebarSections";
import { SplitHandle, type SplitGeometry } from "./SplitHandle";

type Project = { id: string; name: string; workspace_root?: string; archived?: boolean; git_metadata?: Record<string, any> };
type Session = { id: string; name: string; at: number; projectId?: string };
export type ChatSearchHit = { session_id: string; title: string; snippet: string; role: string; agent: string; created_at: number; matches: number };

type SidebarProps = {
  desktop?: boolean;
  hydrating?: boolean;
  collapsed: boolean;
  projects: Project[];
  sessions: Session[];
  activeProjectId: string;
  activeSessionId: string;
  activeView: string;
  onToggleCollapsed(): void;
  onNewSession(projectId?: string): void;
  onAddFolder(): void;
  onSelectSession(id: string): void;
  onRenameSession(id: string, title: string): void;
  onDeleteSession(id: string): void;
  onDeleteProject(id: string): void;
  onView(view: "chat"): void;
  onSettings(): void;
  settingsOpen?: boolean;
  /** Agents section (lean runtime): compact rows, a count and a + action. */
  agents?: { count: number; list: React.ReactNode; onNew(): void };
  /** Agent faces for the icon-only sidebar. */
  collapsedRoster?: React.ReactNode;
  /** Pages listed under search (Group chats, Projects, Artifacts, Routines). */
  page?: SidebarPage;
  onPage?(page: SidebarPage): void;
  pageCounts?: Partial<Record<SidebarPage, number>>;
  /** Full-text search over past chats. */
  onSearchChats?(query: string): Promise<ChatSearchHit[]>;
};

export type SidebarPage = "chat" | "groups" | "projects" | "artifacts" | "routines";

const SECTION_TITLES: Record<SectionKey, string> = { agents: "Agents", chats: "Chats", projects: "Projects" };

const NAV_ITEMS: { id: Exclude<SidebarPage, "chat">; label: string; hint: string }[] = [
  { id: "groups", label: "Group chats", hint: "Chats with several agents" },
  { id: "projects", label: "Projects", hint: "Your project folders" },
  { id: "artifacts", label: "Artifacts", hint: "Apps, documents and diagrams your agents made" },
  { id: "routines", label: "Routines", hint: "Tasks that run on a schedule" },
];

function NavIcon({ name }: { name: Exclude<SidebarPage, "chat"> }) {
  const common = { width: 15, height: 15, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "groups":
      return (
        <svg {...common}>
          <circle cx="9" cy="9" r="3" />
          <path d="M3.5 18.5c.6-2.8 2.8-4.5 5.5-4.5s4.9 1.7 5.5 4.5" />
          <path d="M15.5 6.3a3 3 0 0 1 0 5.4M17.5 14.3c1.6.6 2.7 2 3 4.2" />
        </svg>
      );
    case "projects":
      return (
        <svg {...common}>
          <path d="M3.5 8V6.5A1.5 1.5 0 0 1 5 5h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5V8z" />
        </svg>
      );
    case "artifacts":
      return (
        <svg {...common}>
          <rect x="4" y="4" width="7" height="7" rx="1.5" />
          <rect x="13" y="4" width="7" height="7" rx="3.5" />
          <path d="M4.5 19.5 7.5 14l3 5.5z" />
          <rect x="13" y="13" width="7" height="7" rx="1.5" />
        </svg>
      );
    default:
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8" />
          <path d="M12 7.5V12l3 2" />
        </svg>
      );
  }
}

/** Snippets mark matches as [word]; render those as <mark>. */
function Snippet({ text }: { text: string }) {
  const parts = text.split(/(\[[^\]]*\])/g);
  return (
    <>
      {parts.map((part, index) =>
        part.startsWith("[") && part.endsWith("]") ? <mark key={index}>{part.slice(1, -1)}</mark> : <React.Fragment key={index}>{part}</React.Fragment>,
      )}
    </>
  );
}

const surface = "#0a0a0a";
const border = "rgba(255,255,255,.10)";
const muted = "rgba(255,255,255,.48)";

/** Minimal monochrome icons — readable in the 50px collapsed rail. */
function Icon({
  name,
  size = 15,
  active = false,
}: {
  name:
    | "chat"
     | "session"
     | "folder"
     | "avatar"
     | "studio"
    | "plus"
    | "more"
    | "trash"
    | "chevron"
    | "expand"
    | "collapse";
  size?: number;
  active?: boolean;
}) {
  const stroke = active ? "#fff" : "rgba(255,255,255,0.72)";
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none" as const,
    stroke,
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true as const,
  };

  switch (name) {
    case "chat":
      return (
        <svg {...common}>
          <path d="M5 6.5h14a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5H10l-4 3v-3H5A1.5 1.5 0 0 1 3.5 15V8A1.5 1.5 0 0 1 5 6.5z" />
        </svg>
      );
    case "session":
      return (
        <svg {...common}>
          <rect x="4.5" y="5.5" width="15" height="13" rx="2" />
          <path d="M8 10h8M8 13.5h5" />
        </svg>
      );
    case "folder":
      return (
        <svg {...common}>
          <path d="M3.5 8.5V7a1.5 1.5 0 0 1 1.5-1.5h4l2 2H19A1.5 1.5 0 0 1 20.5 9v8a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 17V8.5z" />
        </svg>
      );
    case "avatar":
      return (
        <svg {...common}>
          <rect x="4" y="4" width="16" height="16" rx="2.5" />
          <circle cx="9.5" cy="11" r="1.15" fill={stroke} stroke="none" />
          <circle cx="14.5" cy="11" r="1.15" fill={stroke} stroke="none" />
          <path d="M9.2 15c.7 1 1.8 1.5 2.8 1.5s2.1-.5 2.8-1.5" />
        </svg>
      );
    case "studio":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M12 4.5v2.2M12 17.3v2.2M4.5 12h2.2M17.3 12h2.2M6.7 6.7l1.6 1.6M15.7 15.7l1.6 1.6M17.3 6.7l-1.6 1.6M8.3 15.7l-1.6 1.6" />
        </svg>
      );
    case "plus":
      return (
        <svg {...common}>
          <path d="M12 6v12M6 12h12" />
        </svg>
      );
    case "more":
      return (
        <svg {...common}>
          <circle cx="6" cy="12" r="1" fill={stroke} stroke="none" />
          <circle cx="12" cy="12" r="1" fill={stroke} stroke="none" />
          <circle cx="18" cy="12" r="1" fill={stroke} stroke="none" />
        </svg>
      );
    case "trash":
      return (
        <svg {...common}>
          <path d="M5.5 7.5h13M9 7.5V5.7h6v1.8M7.5 7.5l.7 11h7.6l.7-11M10 10.5v5M14 10.5v5" />
        </svg>
      );
    case "chevron":
      return (
        <svg {...common}>
          <path d="m8.5 10 3.5 3.5 3.5-3.5" />
        </svg>
      );
    case "expand":
      return (
        <svg {...common}>
          <path d="M9 6.5 14.5 12 9 17.5" />
        </svg>
      );
    case "collapse":
      return (
        <svg {...common}>
          <path d="M15 6.5 9.5 12 15 17.5" />
        </svg>
      );
    default:
      return null;
  }
}


export function ProjectSidebar(props: SidebarProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [projectsOpen, setProjectsOpen] = useState(true);
  const [layout, setLayout] = useState<StackLayout>(() => loadStackLayout());
  const [dragging, setDragging] = useState(false);
  const splitRef = useRef<HTMLDivElement | null>(null);
  const sectionRefs = useRef<Partial<Record<SectionKey, HTMLElement | null>>>({});
  useEffect(() => saveStackLayout(layout), [layout]);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<ChatSearchHit[] | null>(null);
  const searching = query.trim().length >= 2;
  const onSearchChats = props.onSearchChats;
  useEffect(() => {
    if (!searching || !onSearchChats) {
      setHits(null);
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      onSearchChats(query.trim())
        .then((items) => live && setHits(items))
        .catch(() => live && setHits([]));
    }, 180);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [query, searching, onSearchChats]);
  const iconOnly = props.collapsed;
  const projects = useMemo(() => props.projects.filter((project) => !project.archived), [props.projects]);
  const sessions = props.sessions;
  const looseSessions = sessions.filter((session) => !session.projectId);

  const railButton = (active = false): React.CSSProperties => ({
    // Do not force width:100% here — row items share space with fixed action buttons.
    width: iconOnly ? "100%" : undefined,
    maxWidth: "100%",
    minHeight: iconOnly ? 38 : 36,
    border: 0,
    borderRadius: 2,
    background: active
      ? "linear-gradient(90deg, rgba(255,255,255,.10), rgba(255,255,255,.025) 62%, transparent)"
      : "transparent",
    color: active ? "#fff" : "rgba(255,255,255,.62)",
    display: "flex",
    alignItems: "center",
    justifyContent: iconOnly ? "center" : "flex-start",
    gap: 8,
    padding: iconOnly ? 0 : "0 8px",
    cursor: "pointer",
    fontFamily: "'Inter', 'Segoe UI Variable', 'Segoe UI', sans-serif",
    fontSize: 12,
    textAlign: "left",
    minWidth: 0,
    boxSizing: "border-box",
  });

  const iconSlot = (active = false): React.CSSProperties => ({
    width: iconOnly ? 23 : 21,
    height: iconOnly ? 23 : 21,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    opacity: active ? 1 : 0.88,
  });

  /** Title text: always truncates; never steals space from trailing action buttons. */
  const titleEllipsis: React.CSSProperties = {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    minWidth: 0,
    flex: "1 1 auto",
  };

  const sessionRow = (session: Session, nested = false) => (
    <div
      key={session.id}
      className="echo-side-row"
      style={{
        display: "flex",
        alignItems: "center",
        width: "100%",
        maxWidth: "100%",
        minWidth: 0,
        // Indent title only via padding — marginLeft + width 100% was clipping × off the right.
        paddingLeft: !iconOnly && nested ? 13 : 0,
        boxSizing: "border-box",
      }}
    >
      <button
        className={`echo-side-button ${props.activeSessionId === session.id ? "is-active" : ""}`}
        type="button"
        style={{
          ...railButton(props.activeSessionId === session.id),
          flex: "1 1 auto",
          minWidth: 0,
          width: "auto",
          // Nested: slightly less left pad so label still reads as indented under project
          paddingLeft: !iconOnly && nested ? 4 : undefined,
        }}
        onClick={() => props.onSelectSession(session.id)}
        title={session.name}
        aria-label={`Session: ${session.name}`}
      >
        <span style={iconSlot(props.activeSessionId === session.id)}>
          <Icon name="session" size={iconOnly ? 16 : 15} active={props.activeSessionId === session.id} />
        </span>
        {!iconOnly && <span style={titleEllipsis}>{session.name}</span>}
      </button>
      {!iconOnly && (
        <div className="echo-row-actions" aria-label="Session actions">
          <button
            type="button"
            title="Rename Session"
            aria-label={`Rename ${session.name}`}
            onClick={() => {
              const title = window.prompt("Rename Session", session.name)?.trim();
              if (title && title !== session.name) props.onRenameSession(session.id, title);
            }}
          >
            <Icon name="more" size={15} />
          </button>
          <button
            type="button"
            title="Delete Session"
            aria-label={`Delete ${session.name}`}
            onClick={() => {
              if (window.confirm(`Delete “${session.name}”?`)) props.onDeleteSession(session.id);
            }}
          >
            <Icon name="trash" size={14} />
          </button>
        </div>
      )}
    </div>
  );

  // Agents / Chats / Projects stack (full sidebar only). Sections share the
  // space by flex-grow, so the browser sizes them and changes animate; only a
  // drag needs to measure.
  const SECTION_HEAD_PX = 30;
  const present: SectionKey[] = props.agents ? ["agents", "chats", "projects"] : ["chats", "projects"];
  const fractions = sectionFractions(layout, present);
  const sectionFlex = (key: SectionKey): React.CSSProperties =>
    layout.open[key] ? { flex: `${fractions[key] ?? 1} 1 0px` } : { flex: `0 0 ${SECTION_HEAD_PX}px` };
  /** Where the upper list starts and how tall both lists are, for pointer/keyboard resizing. */
  const measurePair = (a: SectionKey, b: SectionKey): SplitGeometry => {
    const elA = sectionRefs.current[a];
    const elB = sectionRefs.current[b];
    if (!elA || !elB) return { top: 0, area: 0, scale: 1 };
    const rect = elA.getBoundingClientRect();
    // The app shell may be zoomed: convert screen px to layout px.
    const scale = rect.height / (elA.clientHeight || 1) || 1;
    const area = Math.max(0, elA.clientHeight + elB.clientHeight - SECTION_HEAD_PX * 2);
    return { top: rect.top + SECTION_HEAD_PX * scale, area, scale };
  };
  const sectionHead = (key: SectionKey, count: number, actions: React.ReactNode) => (
    <div className="es-sec-head">
      <button
        type="button"
        className="es-sec-toggle"
        aria-expanded={layout.open[key]}
        aria-controls={`es-sec-${key}`}
        onClick={() => setLayout((value) => toggleSection(value, key))}
      >
        <span className="es-sec-chev" aria-hidden><Icon name="chevron" size={12} /></span>
        <span className="es-sec-title">{SECTION_TITLES[key]}</span>
        <span className="es-sec-count">{count}</span>
      </button>
      {actions}
    </div>
  );
  const renderSection = (key: SectionKey) => {
    const sectionProps = {
      className: "es-sec",
      "aria-label": SECTION_TITLES[key],
      "data-open": layout.open[key] ? "true" : "false",
      "data-section": key,
      style: sectionFlex(key),
      ref: (el: HTMLElement | null) => {
        sectionRefs.current[key] = el;
      },
    };
    if (key === "agents" && props.agents) {
      return (
        <section {...sectionProps}>
          {sectionHead("agents", props.agents.count, (
            <button className="es-sec-action" type="button" onClick={props.agents.onNew} title="New agent" aria-label="New agent">
              <Icon name="plus" size={14} />
            </button>
          ))}
          <div className="es-sec-list es-agent-list" id="es-sec-agents" hidden={!layout.open.agents}>
            {props.agents.list}
          </div>
        </section>
      );
    }
    if (key === "chats") {
      return (
        <section {...sectionProps}>
          {sectionHead("chats", looseSessions.length, (
            <>
              <button className="es-sec-action" type="button" onClick={() => { props.onPage?.("chat"); props.onNewSession(); }} title="Start new chat" aria-label="Start new chat">
                <Icon name="plus" size={14} />
              </button>
              {props.desktop ? (
                <button className="es-sec-action" type="button" onClick={props.onToggleCollapsed} title="Collapse sidebar" aria-label="Collapse sidebar">
                  <Icon name="collapse" size={14} />
                </button>
              ) : null}
            </>
          ))}
          <div className="es-sec-list" id="es-sec-chats" hidden={!layout.open.chats}>
            {props.hydrating ? (
              <div role="status" className="es-sec-empty">Restoring chats…</div>
            ) : looseSessions.length ? (
              looseSessions.map((session) => sessionRow(session))
            ) : (
              <div className="es-sec-empty">No chats yet. Start one with +.</div>
            )}
          </div>
        </section>
      );
    }
    return (
      <section {...sectionProps}>
        {sectionHead("projects", projects.length, (
          <button className="es-sec-action" type="button" onClick={props.onAddFolder} title="Add Project folder" aria-label="Add Project folder">
            <Icon name="plus" size={14} />
          </button>
        ))}
        <div className="es-sec-list" id="es-sec-projects" hidden={!layout.open.projects}>
          {props.hydrating ? (
            <div role="status" className="es-sec-empty">Restoring projects…</div>
          ) : projects.length ? (
            projects.map((project) => projectRow(project))
          ) : (
            <button className="echo-footer-action es-sec-cta" type="button" onClick={props.onAddFolder}>
              <span>Add your first Project</span>
              <small>Attach a local folder</small>
            </button>
          )}
        </div>
      </section>
    );
  };

  const projectRow = (project: Project) => {
    const childSessions = sessions.filter((session) => session.projectId === project.id);
    const open = expanded[project.id] ?? props.activeProjectId === project.id;
    const isActive = props.activeProjectId === project.id;
    return (
      <div key={project.id} style={{ minWidth: 0, maxWidth: "100%" }}>
        <div
          className="echo-side-row"
          style={{
            display: "flex",
            alignItems: "center",
            width: "100%",
            maxWidth: "100%",
            minWidth: 0,
            boxSizing: "border-box",
          }}
        >
          <button
            className={`echo-side-button ${isActive ? "is-active" : ""}`}
            type="button"
            style={{
              ...railButton(isActive),
              flex: "1 1 auto",
              minWidth: 0,
              width: "auto",
            }}
            onClick={() => {
              setExpanded((value) => ({ ...value, [project.id]: !open }));
              // A Project row is navigation, never Session creation.
              // Select an existing child only; the adjacent + owns creation.
              if (!isActive && childSessions[0]) props.onSelectSession(childSessions[0].id);
            }}
            title={project.workspace_root || project.name}
            aria-label={`Project: ${project.name}`}
          >
            <span style={iconSlot(isActive)}>
              <Icon name="folder" size={iconOnly ? 16 : 15} active={isActive} />
            </span>
            {!iconOnly && <span style={titleEllipsis}>{project.name}</span>}
          </button>
          {!iconOnly && (
            <div className="echo-row-actions" aria-label="Project actions">
              <button type="button" title="New Session in Project" aria-label={`New Session in ${project.name}`} onClick={() => props.onNewSession(project.id)}>
                <Icon name="plus" size={14} />
              </button>
              <button
                type="button"
                title="Delete Project"
                aria-label={`Delete ${project.name}`}
                onClick={() => {
                  if (
                    window.confirm(
                      `Delete Project “${project.name}”? Its Sessions will become independent.`,
                    )
                  )
                    props.onDeleteProject(project.id);
                }}
              >
                <Icon name="trash" size={14} />
              </button>
            </div>
          )}
        </div>
        {/* Sessions under a project: hide in rail to keep icons clear; open sidebar to manage */}
        {!props.hydrating && !iconOnly && open && childSessions.map((session) => sessionRow(session, true))}
        {!iconOnly && open && !childSessions.length && (
          <button
            type="button"
            className="echo-side-button"
            onClick={() => props.onNewSession(project.id)}
            style={{
              ...railButton(),
              width: "100%",
              maxWidth: "100%",
              boxSizing: "border-box",
              paddingLeft: 21,
              color: muted,
            }}
          >
            + Session in Project
          </button>
        )}
      </div>
    );
  };

  return (
    <aside
      className="echo-sidebar"
      aria-label="Project and Session sidebar"
      style={{
        minWidth: 0,
        width: "100%",
        height: "100%",
        overflow: "hidden",
        borderRight: `1px solid ${border}`,
        background: `linear-gradient(180deg, rgba(255,255,255,.018) 0%, transparent 18%), ${surface}`,
        /* No right padding — scroll track must sit on the sidebar edge */
        padding: iconOnly ? "6px 0 6px 6px" : "10px 0 10px 10px",
        display: "flex",
        flexDirection: "column",
        gap: iconOnly ? 8 : 12,
      }}
    >
      <style>{`
      .echo-side-button { transition: background .14s ease, color .14s ease, opacity .14s ease; }
      .echo-side-button:hover { background: linear-gradient(90deg, rgba(255,255,255,.065), rgba(255,255,255,.018) 68%, transparent) !important; color: #fff !important; }
      .echo-side-button:focus-visible, .echo-row-actions button:focus-visible { outline: 1px solid rgba(255,255,255,.72); outline-offset: 2px; }
      .echo-side-button.is-active { font-weight: 600; position: relative; }
      .echo-side-button.is-active::before {
        content: "";
        position: absolute;
        left: 0;
        top: 9px;
        bottom: 9px;
        width: 2px;
        border-radius: 2px;
        background: rgba(255,255,255,.9);
        box-shadow: 0 0 12px rgba(255,255,255,.22);
      }
      .echo-side-button:active { background: linear-gradient(90deg, rgba(255,255,255,.09), rgba(255,255,255,.025) 68%, transparent) !important; }
      /* Fixed trailing slot — never shrinks when titles are long */
      .echo-row-actions {
        display: flex;
        flex: 0 0 auto;
        flex-shrink: 0;
        align-items: center;
        opacity: 0.55;
        transition: opacity .14s ease;
      }
      .echo-side-row:hover .echo-row-actions,
      .echo-side-row:focus-within .echo-row-actions { opacity: 1; }
      .echo-row-actions button {
        width: 24px;
        height: 30px;
        border: 0;
        background: transparent;
        color: rgba(255,255,255,.7);
        cursor: pointer;
        padding: 0;
        font-size: 13px;
        flex-shrink: 0;
        display: grid;
        place-items: center;
        border-radius: 2px;
      }
      .echo-row-actions button:hover { color: #fff; background: #181818; }
      .echo-side-row {
        min-width: 0;
        max-width: 100%;
        overflow: hidden;
        box-sizing: border-box;
      }
      /* Keep action cluster flush to content edge (padding clears the scrollbar) */
      .echo-side-row .echo-row-actions {
        margin-left: auto;
        margin-right: 0;
      }
      .echo-sidebar-scroll {
        flex: 1 1 auto;
        min-height: 0;
        overflow-x: hidden;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: ${iconOnly ? 8 : 12}px;
        /* Content inset only — scrollbar stays on the sidebar's right edge */
        padding-right: ${iconOnly ? 2 : 6}px;
        scrollbar-width: thin;
        scrollbar-color: rgba(255,255,255,.12) transparent;
        scrollbar-gutter: auto;
      }
      .echo-sidebar-scroll::-webkit-scrollbar {
        width: 4px;
      }
      .echo-sidebar-scroll::-webkit-scrollbar-track {
        background: transparent;
        margin: 0;
      }
      .echo-sidebar-scroll::-webkit-scrollbar-thumb {
        background: rgba(255,255,255,.12);
        border-radius: 0;
      }
      .echo-sidebar-scroll::-webkit-scrollbar-thumb:hover {
        background: rgba(255,255,255,.2);
      }
      /* Brand / footer keep the previous right inset since they sit outside the scroller */
      .echo-sidebar-edge-pad {
        padding-right: ${iconOnly ? 6 : 10}px;
        box-sizing: border-box;
      }
      .echo-sidebar-footer {
        flex: 0 0 auto;
        border: 0;
        background: transparent;
        box-shadow: none;
      }
      .echo-footer-action {
        border: 1px solid rgba(255,255,255,.09);
        background: #0b0b0b;
        color: rgba(255,255,255,.72);
        transition: background .14s ease, border-color .14s ease, color .14s ease;
      }
      .echo-footer-action:hover {
        background: #121212;
        border-color: rgba(255,255,255,.16);
        color: #fff;
      }
      .echo-footer-action:focus-visible { outline: 1px solid rgba(255,255,255,.72); outline-offset: 2px; }
      .echo-rail-divider {
        height: 1px;
        background: rgba(255,255,255,.07);
        margin: ${iconOnly ? "4px 6px" : "5px 3px"};
      }
    `}</style>

      {/* Browser branding remains here. Desktop identity belongs to the native title bar. */}
      {!props.desktop ? <div
        className="echo-sidebar-edge-pad"
        style={{
          height: 38,
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: iconOnly ? "center" : "space-between",
          paddingLeft: iconOnly ? 0 : 4,
          gap: 6,
        }}
      >
        {iconOnly ? (
          <button
            className="echo-side-button"
            type="button"
            title="Expand sidebar"
            aria-label="Expand sidebar"
            onClick={props.onToggleCollapsed}
            style={{
              width: 34,
              height: 34,
              borderRadius: 2,
              border: 0,
              background: "transparent",
              cursor: "pointer",
              display: "grid",
              placeItems: "center",
              padding: 0,
            }}
          >
            <img src="/logo.png" alt="EchoSpeak" style={{ width: 18, height: 18, borderRadius: 2, display: "block" }} />
          </button>
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
              <div style={{ width: 24, height: 24, display: "grid", placeItems: "center", border: "1px solid rgba(255,255,255,.1)", background: "rgba(255,255,255,.035)", borderRadius: 3 }}>
                <img src="/logo.png" alt="" style={{ width: 15, height: 15, borderRadius: 2 }} />
              </div>
              <div style={{ minWidth: 0, lineHeight: 1.05 }}>
                <strong style={{ display: "block", fontFamily: "'Space Grotesk', sans-serif", fontSize: 15.5, letterSpacing: "-.01em" }}>EchoSpeak</strong>
                <span style={{ display: "block", marginTop: 4, color: "rgba(255,255,255,.35)", fontSize: 8.5, letterSpacing: ".13em", textTransform: "uppercase" }}>Local workspace</span>
              </div>
            </div>
            <button
              className="echo-side-button"
              type="button"
              title="Collapse sidebar"
              aria-label="Collapse sidebar"
              onClick={props.onToggleCollapsed}
              style={{
                width: 28,
                height: 28,
                borderRadius: 2,
                border: 0,
                color: "rgba(255,255,255,.72)",
                background: "transparent",
                cursor: "pointer",
                display: "grid",
                placeItems: "center",
                padding: 0,
              }}
            >
              <Icon name="collapse" size={15} />
            </button>
          </>
        )}
      </div> : null}

      {!iconOnly ? (
        <div className="es-side-body">
          <div className="es-side-top">
            <button
              className="echo-side-button es-new-chat"
              type="button"
              onClick={() => {
                props.onView("chat");
                props.onPage?.("chat");
                props.onNewSession();
              }}
              title="New chat"
              aria-label="New chat"
            >
              <span style={iconSlot(false)}>
                <svg width={15} height={15} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
                </svg>
              </span>
              <span style={titleEllipsis}>New chat</span>
            </button>
            {props.onSearchChats ? (
              <label className="es-chat-search">
                <svg width={13} height={13} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
                  <circle cx="11" cy="11" r="7" />
                  <path d="m20 20-3.5-3.5" />
                </svg>
                <input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setQuery("");
                  }}
                  placeholder="Search chats"
                  aria-label="Search chats"
                />
              </label>
            ) : null}
            {props.onPage ? (
              <nav className="es-nav" aria-label="Pages">
                {NAV_ITEMS.map((item) => {
                  const active = props.page === item.id;
                  const count = props.pageCounts?.[item.id];
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className={`es-nav-item${active ? " is-active" : ""}`}
                      aria-current={active ? "page" : undefined}
                      onClick={() => props.onPage?.(active ? "chat" : item.id)}
                      title={item.hint}
                    >
                      <NavIcon name={item.id} />
                      <span>{item.label}</span>
                      {count ? <small>{count}</small> : null}
                    </button>
                  );
                })}
              </nav>
            ) : null}
          </div>

          {searching ? (
            <section className="es-search-results" aria-label="Search results">
              {hits === null ? (
                <div role="status" className="es-sec-empty">Searching…</div>
              ) : hits.length ? (
                hits.map((hit) => (
                  <button
                    key={hit.session_id}
                    type="button"
                    className={`es-search-hit${props.activeSessionId === hit.session_id ? " is-active" : ""}`}
                    onClick={() => {
                      props.onView("chat");
                      props.onPage?.("chat");
                      props.onSelectSession(hit.session_id);
                      setQuery("");
                    }}
                  >
                    <span className="es-search-title">
                      {hit.title}
                      {hit.matches > 1 ? <small>{hit.matches} matches</small> : null}
                    </span>
                    <span className="es-search-snippet">
                      <b>{hit.role === "user" ? "You" : hit.agent || "Echo"}:</b> <Snippet text={hit.snippet} />
                    </span>
                  </button>
                ))
              ) : (
                <div className="es-sec-empty">No chats mention “{query.trim()}”.</div>
              )}
            </section>
          ) : null}
          <div className="es-split" ref={splitRef} data-dragging={dragging ? "true" : "false"} hidden={searching}>
            {present.map((key, index) => {
              const next = present[index + 1];
              const handle = next && layout.open[key] && layout.open[next] ? (
                <SplitHandle
                  key={`h-${key}`}
                  label={`Resize ${SECTION_TITLES[key]} and ${SECTION_TITLES[next]}`}
                  share={pairShare(layout, key, next)}
                  measure={() => measurePair(key, next)}
                  onShare={(share) => setLayout((value) => resizePair(value, key, next, share))}
                  onReset={() => setLayout((value) => resetPair(value, key, next))}
                  onDragging={setDragging}
                />
              ) : null;
              return (
                <React.Fragment key={key}>
                  {renderSection(key)}
                  {handle}
                </React.Fragment>
              );
            })}
          </div>
        </div>
      ) : (
      <div className="echo-sidebar-scroll">
        <nav
          aria-label="Primary views"
          style={{
            display: "grid",
            gridTemplateColumns: "1fr",
            gap: 3,
            padding: iconOnly ? "0 1px 5px" : "0 1px 7px",
            flex: "0 0 auto",
          }}
        >
          <button
            className="echo-side-button es-new-chat"
            type="button"
            onClick={() => {
              props.onView("chat");
              props.onNewSession();
            }}
            title="New chat"
            aria-label="New chat"
          >
            <span style={iconSlot(false)}>
              <svg width={iconOnly ? 16 : 15} height={iconOnly ? 16 : 15} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
              </svg>
            </span>
            {!iconOnly && <span style={titleEllipsis}>New chat</span>}
          </button>
        </nav>

        <div className="echo-rail-divider" />

        {props.collapsedRoster ? (
          <>
            {props.collapsedRoster}
            <div className="echo-rail-divider" />
          </>
        ) : null}

        <section aria-label="Workspace" style={{ padding: iconOnly ? "0 1px" : 0, display: "grid", gap: 7, flex: "0 0 auto" }}>
          <div style={{ display: "grid", gap: 2 }}>
            {props.desktop && iconOnly ? (
              <button
                className="echo-side-button"
                type="button"
                style={railButton()}
                onClick={props.onToggleCollapsed}
                title="Expand sidebar"
                aria-label="Expand sidebar"
              >
                <span style={iconSlot()}><Icon name="expand" size={16} /></span>
              </button>
            ) : null}
            {!iconOnly && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 4px 2px",
                  minWidth: 0,
                  width: "100%",
                  boxSizing: "border-box",
                }}
              >
                <span
                  style={{
                    fontSize: 10,
                    color: muted,
                    letterSpacing: ".1em",
                    textTransform: "uppercase",
                    minWidth: 0,
                    flex: "1 1 auto",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  Chats
                </span>
                <button
                  className="echo-side-button"
                  type="button"
                  onClick={() => props.onNewSession()}
                  title="Start new chat"
                  aria-label="Start new chat"
                  style={{
                    width: 26,
                    height: 26,
                    border: "1px solid rgba(255,255,255,.09)",
                    background: "rgba(255,255,255,.025)",
                    color: "rgba(255,255,255,.78)",
                    borderRadius: 3,
                    cursor: "pointer",
                    display: "grid",
                    placeItems: "center",
                    padding: 0,
                    flex: "0 0 auto",
                    flexShrink: 0,
                  }}
                >
                  <Icon name="plus" size={14} />
                </button>
                {props.desktop ? (
                  <button
                    className="echo-side-button"
                    type="button"
                    onClick={props.onToggleCollapsed}
                    title="Collapse sidebar"
                    aria-label="Collapse sidebar"
                    style={{
                      width: 26,
                      height: 24,
                      border: 0,
                      background: "transparent",
                      color: "rgba(255,255,255,.7)",
                      borderRadius: 2,
                      cursor: "pointer",
                      display: "grid",
                      placeItems: "center",
                      padding: 0,
                      flex: "0 0 auto",
                    }}
                  >
                    <Icon name="collapse" size={14} />
                  </button>
                ) : null}
              </div>
            )}

            {iconOnly && (
              <button
                className="echo-side-button"
                type="button"
                style={railButton()}
                onClick={() => props.onNewSession()}
                title="New chat"
                aria-label="New chat"
              >
                <span style={iconSlot()}>
                  <Icon name="plus" size={16} />
                </span>
              </button>
            )}

            {!props.hydrating && looseSessions.map((session) => sessionRow(session))}

            <div className="echo-rail-divider" />

            {!iconOnly && (
              <div style={{ display: "flex", alignItems: "center", minWidth: 0 }}>
                <button
                  className="echo-side-button"
                  type="button"
                  onClick={() => setProjectsOpen((value) => !value)}
                  aria-expanded={projectsOpen}
                  style={{ ...railButton(), flex: "1 1 auto", minWidth: 0, minHeight: 28, padding: "0 4px", color: muted }}
                >
                  <span style={{ flex: 1, fontSize: 10, letterSpacing: ".1em", textTransform: "uppercase" }}>Projects</span>
                  <span style={{ minWidth: 18, height: 18, padding: "0 5px", display: "inline-grid", placeItems: "center", borderRadius: 9, background: "rgba(255,255,255,.045)", color: "rgba(255,255,255,.45)", fontSize: 9 }}>{projects.length}</span>
                  <span style={{ display: "grid", placeItems: "center", transform: projectsOpen ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform .15s ease" }}><Icon name="chevron" size={13} /></span>
                </button>
                <button
                  className="echo-side-button"
                  type="button"
                  onClick={props.onAddFolder}
                  title="Add Project folder"
                  aria-label="Add Project folder"
                  style={{ ...railButton(), width: "auto", minHeight: 28, padding: "0 5px", justifyContent: "center", flex: "0 0 auto", fontSize: 10.5 }}
                >
                  <Icon name="plus" size={14} />
                  <span>New Project</span>
                </button>
              </div>
            )}

            {!iconOnly && projectsOpen && !projects.length && !props.hydrating && (
              <button
                className="echo-footer-action"
                type="button"
                onClick={props.onAddFolder}
                style={{ margin: "2px 3px 4px", minHeight: 46, borderRadius: 3, padding: "8px 10px", cursor: "pointer", textAlign: "left", fontFamily: "'Inter', 'Segoe UI', sans-serif" }}
              >
                <span style={{ display: "block", fontSize: 10.5, color: "rgba(255,255,255,.72)" }}>Add your first Project</span>
                <span style={{ display: "block", marginTop: 4, fontSize: 9, color: "rgba(255,255,255,.36)" }}>Attach a local folder</span>
              </button>
            )}

            {!iconOnly && props.hydrating ? (
              <div role="status" style={{ padding: "7px 7px 5px", color: muted, fontSize: 9.5 }}>
                Restoring Projects and Sessions…
              </div>
            ) : null}

            {(iconOnly || projectsOpen) && projects.map((project) => projectRow(project))}

          </div>
        </section>

      </div>
      )}

      {!iconOnly ? (
        <footer className="echo-sidebar-footer" style={{ padding: "6px 10px 0 0" }}>
          <button
            className={`echo-footer-action ${props.settingsOpen ? "is-active" : ""}`}
            type="button"
            onClick={props.onSettings}
            aria-pressed={props.settingsOpen}
            style={{ width: "100%", minHeight: 36, borderRadius: 4, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "flex-start", gap: 9, padding: "0 11px", fontFamily: "'Inter', 'Segoe UI', sans-serif", fontSize: 11.5 }}
          >
            <Icon name="studio" size={14} active={props.settingsOpen} /> Settings
          </button>
        </footer>
      ) : (
        <div className="echo-sidebar-edge-pad" style={{ display: "grid", gap: 4, flexShrink: 0, paddingTop: 5, borderTop: "1px solid rgba(255,255,255,.07)" }}>
          <button className="echo-side-button" type="button" title="Add Project folder" aria-label="Add Project folder" onClick={props.onAddFolder} style={railButton()}>
            <span style={iconSlot()}><Icon name="folder" size={16} /></span>
          </button>
          <button className="echo-side-button" type="button" title="Settings" aria-label="Settings" aria-pressed={props.settingsOpen} onClick={props.onSettings} style={railButton(Boolean(props.settingsOpen))}>
            <span style={iconSlot(Boolean(props.settingsOpen))}><Icon name="studio" size={16} active={Boolean(props.settingsOpen)} /></span>
          </button>
        </div>
      )}
    </aside>
  );
}
