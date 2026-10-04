import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useLocation, useNavigate } from "react-router-dom";
import { ProjectSidebar, type SidebarPage } from "./components/ProjectSidebar";
import { MediaLibraryView } from "./features/media/MediaLibraryView.tsx";
import { loadRuntimeLayout, runtimeGridColumns, saveRuntimeLayout } from "./runtimeLayout";
import {
  canApplyFinalToChat,
  shouldIncludeChatActivity,
} from "./chatPresentation";
import { useResearchStore } from "./features/research/store";
import { OperationalStateCard } from "./features/operations/OperationalStateCard";
import { controlDesktopWindow, getEchoSpeakApiBase, isDesktopRuntime, openDesktopSettingsWindow, pickDesktopProjectFolder } from "./desktop/bridge";
import type {
  VoiceTranscript,
} from "./voiceTransport";
import { canApplySessionHistory, ownsStreamCleanup } from "./desktop/sessionProjection";
import leanCss from "./lean/lean.css?inline";
import settingsCss from "./settings/settings.css?inline";
import { SettingsPanel } from "./settings/SettingsPanel";
import { LeanMessage } from "./lean/LeanMessage";
import { ChatFollower } from "./app/chatFollow";
import { isLeanEvent } from "./lean/liveReducer";
import { useLeanLive } from "./lean/useLeanLive";
import { LiveStatusPill } from "./lean/LiveStatus";
import { leanApi } from "./lean/api";
import { AgentRows, CollapsedRoster } from "./lean/Roster";
import { WidgetEnvProvider, type WidgetEnv } from "./widgets/env";
import { RightPanel, TERMINAL_TOOLS, clampPanelWidth, collectActivity, loadPanelWidth, savePanelWidth, type RightTab } from "./widgets/RightPanel";
import { ArtifactsPage, GroupChatsPage, PageCloseContext, ProjectsPage, RoutinesPage, type ArtifactSummary } from "./lean/Pages";
import { AgentEditor, MentionMenu, RoomDialog, RoomHeader, activeMention, mentionMatches } from "./lean/Dialogs";
import type { LeanEvent, LeanPersona, LeanRoom } from "./lean/types";
import {
  desktopExecutionProfile,
  type DesktopWorkspaceSurface,
} from "./desktop/workspaceState";
import { isStreamThreadCurrent } from "./agentActivity";
import { type ActivityItem, type AgentStreamEvent, type ApprovalDecisionEnvelope, type Message, type PendingActionEnvelope, type Role, type ThreadSessionState, type TimelineItem, type VisionAnalyzeResponse } from "./app/types";
import { buildMessageUsage } from "./app/toolDisplay";
import { colors, fallbackProviders, fetchWithTimeout, geminiModelOptions, isEmptySessionDraft, normalizeTimestampMs, openaiModelOptions, stopTts, useAppStore } from "./app/runtime";
import { globalCss } from "./app/globalCss";
import { useProviderSettings } from "./dashboard/useProviderSettings";
import { useVoice } from "./dashboard/useVoice";
import { projectSessionHistory } from "./dashboard/historyProjection";
import { ActivityCard, ChatBubble, ContextMeter } from "./app/chatComponents";

type DashboardTab = "chat" | "research" | "overview" | "skills" | "memory" | "docs" | "settings" | "search_settings" | "mcp_settings" | "advanced_settings" | "system_services" | "capabilities" | "approvals" | "executions" | "projects" | "automations" | "connections" | "soul" | "services" | "avatar_editor";

export const Dashboard: React.FC<{
  initialView?: DashboardTab;
  desktopSettingsWindow?: boolean;
}> = ({ initialView = "chat", desktopSettingsWindow = false }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const desktopMode = useMemo(() => isDesktopRuntime(), []);
  const apiBase = useMemo(() => getEchoSpeakApiBase(), []);
  const workspaceRoute = location.pathname.replace(/\/+$/, "");
  const mediaRouteActive = workspaceRoute === "/app/media";
  const desktopSurface: DesktopWorkspaceSurface = "chat";
  const [desktopSettingsOpen, setDesktopSettingsOpen] = useState(desktopSettingsWindow);
  const [desktopStudioHost, setDesktopStudioHost] = useState<HTMLDivElement | null>(null);
  const {
    messages,
    addMessage,
    streaming,
    setStreaming,
    listening,
    setListening,
    speaking,
    speechEnabled,
    setSpeechEnabled,
  } = useAppStore();

  const [input, setInput] = useState("");
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const updateComposerInput = useCallback((value: string) => {
    setInput(value);
  }, []);
  const replaceResearchRuns = useResearchStore((state) => state.replaceRuns);
  const clearResearchRuns = useResearchStore((state) => state.clearRuns);
  const [leftTab, setLeftTab] = useState<DashboardTab>(
    desktopSettingsWindow ? "settings" : initialView
  );

  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const activeGroupButtonRef = useRef<HTMLButtonElement | null>(null);
  const activeGroupMenuRef = useRef<HTMLDivElement | null>(null);
  const [activeGroupPos, setActiveGroupPos] = useState<{ top: number; left: number } | null>(null);
  const [showSidebar, setShowSidebar] = useState<boolean>(() => loadRuntimeLayout(typeof window !== "undefined" ? window.localStorage : null).sidebarVisible);
  /** Which page the main area shows: the chat, or one of the sidebar nav pages. */
  const [mainPage, setMainPage] = useState<SidebarPage>("chat");
  /** The artifact shown in the side panel. */
  const [openArtifact, setOpenArtifact] = useState<{ id: string; version?: number } | null>(null);
  /** Right side panel: which tab, whether Activity was opened, and its width. */
  const [rightTab, setRightTab] = useState<RightTab>("artifact");
  const [activityOpen, setActivityOpen] = useState(false);
  const [panelWidth, setPanelWidth] = useState<number>(() => loadPanelWidth());
  const shellRef = useRef<HTMLDivElement | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => loadRuntimeLayout(typeof window !== "undefined" ? window.localStorage : null).sidebarCollapsed);
  const [narrowLayout, setNarrowLayout] = useState<boolean>(() => typeof window !== "undefined" && window.innerWidth < 900);
  useEffect(() => {
    if (mediaRouteActive) {
      setLeftTab("chat");
      return;
    }
  }, [desktopMode, mediaRouteActive]);
  const [monitoring, setMonitoring] = useState<boolean>(false);
  const [monitorText, setMonitorText] = useState<string>("");
  const desktopBootstrap = typeof window !== "undefined" ? window.__ECHOSPEAK_DESKTOP_BOOTSTRAP__ : undefined;
  const [projects, setProjects] = useState<{
    id: string; name: string; description?: string; context_prompt?: string; tags?: string[];
    workspace_root?: string; archived?: boolean; git_metadata?: Record<string, any>;
  }[]>(() => (desktopBootstrap?.projects || []) as any[]);
  const [activeProjectId, setActiveProjectId] = useState<string>(() => desktopBootstrap?.active_project_id || "");
  const activeProjectIdRef = useRef<string>(desktopBootstrap?.active_project_id || "");
  const [folderDropActive, setFolderDropActive] = useState(false);
  // Bootstrap data is only a startup hint.  Do not paint it as authoritative
  // chat history: the first scoped /threads read reconciles the durable list.
  // Keeping this false until that read completes prevents transient sessions,
  // welcome messages, and activity rows from flashing and then disappearing.
  const [initialHydrationComplete, setInitialHydrationComplete] = useState(false);
  const [threadState, setThreadState] = useState<ThreadSessionState | null>(() => (desktopBootstrap?.thread_state || null) as ThreadSessionState | null);
  const [pendingApproval, setPendingApproval] = useState<PendingActionEnvelope | null>(null);
  const [approvalDecisionBusy, setApprovalDecisionBusy] = useState<boolean>(false);

  const [threads, setThreads] = useState<{ id: string; name: string; at: number; projectId?: string; messageCount?: number }[]>(() =>
    (desktopBootstrap?.threads || []).map((item: any) => ({
      id: String(item.thread_id || item.id || ""),
      name: String(item.title || item.name || "Session"),
      at: normalizeTimestampMs(item.last_active_at || item.created_at || Date.now()),
      projectId: String(item.project_id || ""),
      messageCount: Number(item.message_count || 0),
    })),
  );
  const [activeThreadId, setActiveThreadId] = useState<string>(() => desktopBootstrap?.active_session_id || "");
  // ── Lean runtime: live turn, agent roster, rooms ──
  const lean = useLeanLive();
  const leanClient = useMemo(() => leanApi(apiBase), [apiBase]);
  const searchChats = useCallback((query: string) => leanClient.searchChats(query), [leanClient]);
  const [agents, setAgents] = useState<LeanPersona[]>([]);
  const [rooms, setRooms] = useState<LeanRoom[]>([]);
  const [toolsetIds, setToolsetIds] = useState<string[]>([]);
  const [agentEditor, setAgentEditor] = useState<{ open: boolean; agent: LeanPersona | null }>({ open: false, agent: null });
  const [roomDialog, setRoomDialog] = useState<{ open: boolean; room: LeanRoom | null }>({ open: false, room: null });
  const [mention, setMention] = useState<{ start: number; query: string; index: number } | null>(null);
  const refreshRoster = useCallback(async () => {
    try {
      const [nextAgents, nextRooms] = await Promise.all([leanClient.agents(), leanClient.rooms()]);
      setAgents(nextAgents);
      setRooms(nextRooms);
    } catch {
      // Older backends without the lean runtime simply show no roster.
    }
  }, [leanClient]);
  useEffect(() => {
    void refreshRoster();
    leanClient.toolsets().then((sets) => setToolsetIds(sets.map((s) => s.id))).catch(() => undefined);
  }, [refreshRoster, leanClient]);
  const activeRoom = useMemo(() => rooms.find((room) => room.thread_id === activeThreadId) || null, [rooms, activeThreadId]);
  const roomThreadIds = useMemo(() => new Set(rooms.map((room) => room.thread_id)), [rooms]);
  const roomMembers = useMemo(
    () => (activeRoom ? (activeRoom.agent_ids.map((id) => agents.find((a) => a.id === id)).filter(Boolean) as LeanPersona[]) : []),
    [activeRoom, agents]
  );
  const decideLeanApproval = useCallback(
    async (approvalId: string, decision: "allow" | "deny" | "always") => {
      try {
        await leanClient.decide(approvalId, decision);
      } catch (error) {
        console.warn("Approval decision failed", error);
      }
    },
    [leanClient]
  );
  const activeThreadIdRef = useRef<string>(desktopBootstrap?.active_session_id || "");
  const threadCreationFlightRef = useRef<Promise<string> | null>(null);
  const streamControllersRef = useRef<Map<string, AbortController>>(new Map());
  const activeRequestIdsRef = useRef<Map<string, string>>(new Map());
  const activeExecutionIdsRef = useRef<Map<string, string>>(new Map());
  const historyRequestSeqRef = useRef<Map<string, number>>(new Map());
  const projectionRevisionRef = useRef<Map<string, number>>(new Map());
  const sessionProjectionRef = useRef<Map<string, { messages: Message[]; activities: ActivityItem[] }>>(new Map());
  const [inFlightSessionIds, setInFlightSessionIds] = useState<Set<string>>(() => new Set());

  const setSessionInFlight = useCallback((threadId: string, active: boolean) => {
    setInFlightSessionIds((current) => {
      const next = new Set(current);
      if (active) next.add(threadId);
      else next.delete(threadId);
      return next;
    });
  }, []);

  const cancelSessionTurn = useCallback((
    threadId: string,
    preserveStream = false,
    voiceTranscript?: VoiceTranscript,
  ) => {
    const sessionId = String(threadId || "").trim();
    if (!sessionId) return;
    const requestId = activeRequestIdsRef.current.get(sessionId);
    const executionId = activeExecutionIdsRef.current.get(sessionId) || "";
    // Navigation/supersession detaches local ownership immediately. The user
    // Stop control keeps the exact stream open so the durable cancellation and
    // final "Stopped" state can arrive from the backend.
    if (!preserveStream) {
      streamControllersRef.current.get(sessionId)?.abort();
      streamControllersRef.current.delete(sessionId);
      activeRequestIdsRef.current.delete(sessionId);
      activeExecutionIdsRef.current.delete(sessionId);
      setSessionInFlight(sessionId, false);
    }
    if (requestId) {
      void fetch(`${apiBase}/query/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_id: requestId,
          execution_id: executionId,
          thread_id: sessionId,
          voice_turn_id: voiceTranscript?.voiceTurnId,
          voice_transcript: voiceTranscript?.text,
        }),
        keepalive: true,
      }).catch(() => undefined);
    }
  }, [apiBase, setSessionInFlight]);

  useEffect(() => {
    const cancelAll = () => {
      for (const sessionId of Array.from(activeRequestIdsRef.current.keys())) {
        cancelSessionTurn(sessionId);
      }
    };
    window.addEventListener("beforeunload", cancelAll);
    return () => window.removeEventListener("beforeunload", cancelAll);
  }, [cancelSessionTurn]);

  useEffect(() => {
    const applyBootstrap = (event: Event) => {
      const bootstrap = (event as CustomEvent).detail || window.__ECHOSPEAK_DESKTOP_BOOTSTRAP__;
      if (!bootstrap) return;
      const mapped = (Array.isArray(bootstrap.threads) ? bootstrap.threads : []).map((item: any) => ({
        id: String(item.thread_id || item.id || ""),
        name: String(item.title || item.name || "Session"),
        at: normalizeTimestampMs(item.last_active_at || item.created_at || Date.now()),
        projectId: String(item.project_id || ""),
        messageCount: Number(item.message_count || 0),
      }));
      setProjects(Array.isArray(bootstrap.projects) ? bootstrap.projects : []);
      setThreads(mapped);
      setActiveProjectId(String(bootstrap.active_project_id || ""));
      setThreadState((bootstrap.thread_state || null) as ThreadSessionState | null);
      const sessionId = String(bootstrap.active_session_id || "");
      const mustRebind = Boolean(sessionId && activeThreadIdRef.current === sessionId);
      if (mustRebind) {
        activeThreadIdRef.current = "";
        setActiveThreadId("");
        window.setTimeout(() => {
          activeThreadIdRef.current = sessionId;
          setActiveThreadId(sessionId);
        }, 0);
      } else {
        activeThreadIdRef.current = sessionId;
        setActiveThreadId(sessionId);
      }
      // Keep the bootstrap projection hidden until the authoritative refresh
      // below has completed.  This avoids rendering stale/ephemeral Sessions.
      void refreshThreads().then((ok) => {
        if (ok) setInitialHydrationComplete(true);
      });
    };
    window.addEventListener("echospeak-desktop-bootstrap", applyBootstrap);
    return () => window.removeEventListener("echospeak-desktop-bootstrap", applyBootstrap);
  }, []);

  useEffect(() => {
    activeThreadIdRef.current = activeThreadId;
    setStreaming(Boolean(activeThreadId && streamControllersRef.current.has(activeThreadId)));
  }, [activeThreadId, inFlightSessionIds, setStreaming]);

  useEffect(() => {
    activeProjectIdRef.current = activeProjectId;
  }, [activeProjectId]);

  useEffect(() => {
    if (!initialHydrationComplete || !activeThreadId) return;
    if (activeThreadId) {
      localStorage.setItem("echospeak.active_thread_id", activeThreadId);
      // Keep legacy key updated for compatibility if needed
      localStorage.setItem("echospeak.thread_id", activeThreadId);
      loadHistory(activeThreadId);
      refreshThreadState(activeThreadId);
      refreshPendingApproval(activeThreadId);
    }
  }, [activeThreadId, initialHydrationComplete]);

  /**
   * Reconstruct the completed chat timeline from durable Session → Turn records.
   * Never parse assistant prose for tools/sources; never restart live stream chrome.
   */
  const loadHistory = async (threadId: string) => {
    const requestSeq = (historyRequestSeqRef.current.get(threadId) || 0) + 1;
    historyRequestSeqRef.current.set(threadId, requestSeq);
    const startingRevision = projectionRevisionRef.current.get(threadId) || 0;
    try {
      const rawTid = String(threadId || "").trim();
      const tid = encodeURIComponent(rawTid);
      const resp = await fetchWithTimeout(`${apiBase}/history?thread_id=${tid}`, undefined, 12000);
      // Canonical ToolRun list (Session-scoped) — merge when turns omit runs after restart.
      let sessionToolRuns: any[] = [];
      try {
        const trResp = await fetchWithTimeout(
          `${apiBase}/tool-runs?session_id=${encodeURIComponent(rawTid)}&limit=200`,
          undefined,
          8000
        );
        if (trResp.ok) {
          const trBody = await trResp.json();
          sessionToolRuns = Array.isArray(trBody?.items) ? trBody.items : [];
        }
      } catch {
        sessionToolRuns = [];
      }
      if (!resp.ok) return;
      const data = await resp.json();
      if (!canApplySessionHistory({
        activeSessionId: activeThreadIdRef.current,
        targetSessionId: threadId,
        currentRequestSeq: historyRequestSeqRef.current.get(threadId) || 0,
        requestSeq,
        currentRevision: projectionRevisionRef.current.get(threadId) || 0,
        startingRevision,
        streamInFlight: streamControllersRef.current.has(threadId),
      })) return;

      const turns: any[] = Array.isArray(data?.turns) ? data.turns : [];
      if (turns.length > 0) {
        const { messages: loadedMsgs, activities: loadedActs, research: hydratedResearch } =
          projectSessionHistory(threadId, turns, sessionToolRuns, providerInfo, agents);

        // Replace — never append (idempotent refresh / session switch)
        useAppStore.setState({ messages: loadedMsgs });
        setActivities(loadedActs);
        sessionProjectionRef.current.set(threadId, { messages: loadedMsgs, activities: loadedActs });
        // Historical research remains available to Chat embeds on assistant messages.
        if (hydratedResearch.length) {
          replaceResearchRuns(hydratedResearch);
        } else {
          clearResearchRuns();
        }
        return;
      }

      // Legacy fallback: string-only history
      if (data && data.history && Array.isArray(data.history)) {
        const loadedMsgs = data.history
          .map((h: string, i: number) => {
            const isUser = h.startsWith("Human:");
            const text = h.replace(/^(Human:|Assistant:)\s*/, "").trim();
            return {
              id: `hist-legacy-${threadId}-${i}`,
              role: (isUser ? "user" : "assistant") as Role,
              text,
              at: Date.now() - (data.history.length - i) * 1000,
              skipTypewriter: true,
            };
          })
          .filter((m: Message) => m.text);
        useAppStore.setState({ messages: loadedMsgs });
        setActivities([]);
        sessionProjectionRef.current.set(threadId, { messages: loadedMsgs, activities: [] });
        clearResearchRuns();
      }
    } catch (e) {
      console.error("Failed to load history:", e);
    }
  };

  const refreshThreads = async () => {
    try {
      const resp = await fetchWithTimeout(`${apiBase}/threads?limit=50`, undefined, 6000);
      if (!resp.ok) throw new Error(`Threads failed (${resp.status})`);
      const data = await resp.json();
      const items = Array.isArray(data) ? data : [];
      const selectedId = activeThreadIdRef.current || localStorage.getItem("echospeak.active_thread_id") || "";
      const mapped = items.map((item: any) => ({
        id: String(item.thread_id || item.id || ""),
        name: String(item.title || item.name || "Session"),
        at: normalizeTimestampMs(item.last_active_at || item.created_at || Date.now()),
        projectId: String(item.project_id || ""),
        messageCount: Number(item.message_count || 0),
      })).filter((item: any) => {
        if (!item.id) return false;
        const placeholder = isEmptySessionDraft(item);
        if (!placeholder) return true;
        // A zero-message Session is a draft. Show it only while it is the
        // explicitly selected draft; never resurrect abandoned placeholders
        // after an AI response or Session refresh.
        if (item.id !== selectedId) return false;
        item.name = "New Session";
        return true;
      });
      if (mapped.length) {
        setThreads(mapped);
        const nextId = mapped.some((item: any) => item.id === selectedId) ? selectedId : mapped[0].id;
        activeThreadIdRef.current = nextId;
        setActiveThreadId(nextId);
      } else {
        // Session creation belongs exclusively to explicit New Session/+ UI.
        activeThreadIdRef.current = "";
        setActiveThreadId("");
        setThreads([]);
        useAppStore.setState({ messages: [] });
        setActivities([]);
      }
      return true;
    } catch (e) {
      console.error("Failed to refresh threads:", e);
      return false;
    }
  };

  const refreshThreadState = async (threadId: string = activeThreadId) => {
    if (!threadId) return null;
    try {
      const resp = await fetchWithTimeout(`${apiBase}/threads/${encodeURIComponent(threadId)}/state`, undefined, 5000);
      if (!resp.ok) throw new Error(`Thread state failed (${resp.status})`);
      const data = (await resp.json()) as ThreadSessionState;
      if (activeThreadIdRef.current !== threadId) return data;
      setThreadState(data);
      setActiveProjectId(String(data.active_project_id || ""));
      // The scoped Thread state is the first authoritative model projection
      // available during startup.  Seed the controls from it so the picker
      // does not briefly show a global/default provider before /provider has
      // returned for this Session.
      if (data.runtime_provider || data.selected_model_id) {
        setProviderDraft((draft) => ({
          ...draft,
          provider: String(data.runtime_provider || draft.provider || ""),
          model: String(data.selected_model_id || draft.model || ""),
        }));
      }
      return data;
    } catch (e) {
      console.error("Failed to refresh thread state:", e);
      return null;
    }
  };

  const refreshPendingApproval = async (threadId: string = activeThreadId) => {
    if (!threadId) return null;
    try {
      const resp = await fetchWithTimeout(`${apiBase}/pending-action?thread_id=${encodeURIComponent(threadId)}`, undefined, 5000);
      if (!resp.ok) throw new Error(`Pending action failed (${resp.status})`);
      const data = (await resp.json()) as PendingActionEnvelope;
      if (activeThreadIdRef.current !== threadId) return data;
      setPendingApproval(data);
      return data;
    } catch (e) {
      console.error("Failed to refresh pending approval:", e);
      return null;
    }
  };

  const decideApproval = async (approvalId: string, decision: "confirm" | "cancel") => {
    if (!approvalId || approvalDecisionBusy) return;
    const expectedSessionId = String(activeThreadIdRef.current || "").trim();
    if (!expectedSessionId) return;
    // Disable immediately so duplicate rapid clicks cannot fire a second mutation.
    setApprovalDecisionBusy(true);
    try {
      const resp = await fetchWithTimeout(
        `${apiBase}/approvals/${encodeURIComponent(approvalId)}/${decision}?expected_session_id=${encodeURIComponent(expectedSessionId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        },
        30000,
        // Mutations must never auto-replay on 429.
        { retrySafeGetOn429: false },
      );
      if (!resp.ok) {
        const detail = await resp.text().catch(() => "");
        if (resp.status === 409) {
          throw new Error("That approval is stale or already consumed; nothing was re-executed.");
        }
        if (resp.status === 429) {
          // Do not retry mutation. Surface rate limit honestly.
          throw new Error("Rate limited while confirming. The approval was not re-sent automatically.");
        }
        throw new Error(`Approval failed (${resp.status}): ${detail}`);
      }
      const data = (await resp.json()) as ApprovalDecisionEnvelope;
      if (activeThreadIdRef.current !== expectedSessionId) return;
      if (data.thread_state) {
        setThreadState(data.thread_state);
      }
      // Terminal projection: clear pending only after successful HTTP response.
      setPendingApproval(null);
      if (data.response) {
        addMessage({
          id: crypto.randomUUID(),
          role: "assistant",
          text: data.response,
          at: Date.now(),
          skipTypewriter: true,
          operation: data.thread_state ? {
            state: data.thread_state,
            success: Boolean(data.success),
            executionId: data.execution_id || undefined,
          } : undefined,
        });
      }
    } catch (error) {
      if (activeThreadIdRef.current !== expectedSessionId) return;
      const message = error instanceof Error ? error.message : String(error);
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: message, at: Date.now(), skipTypewriter: true });
    } finally {
      setApprovalDecisionBusy(false);
      if (activeThreadIdRef.current === expectedSessionId) {
        // Hydration failures must not look like mutation failure (safe GET may retry once).
        await refreshThreadState(expectedSessionId);
        await refreshPendingApproval(expectedSessionId);
      }
    }
  };

  const refreshProjects = async () => {
    try {
      const res = await fetch(`${apiBase}/projects`);
      if (!res.ok) throw new Error(`Projects failed (${res.status})`);
      const data = await res.json();
      setProjects(data.items || []);
      return true;
    } catch (e) {
      console.error("Failed to load projects:", e);
      return false;
    } finally {
    }
  };

  useEffect(() => {
    let cancelled = false;
    const hydrate = async () => {
      // Dashboard mounts only after the desktop host reports ready, but a
      // recovering sidecar can still race the first authenticated requests.
      // Retry safe reads; never create a Session as part of hydration.
      let hydrated = false;
      for (let attempt = 0; attempt < 4 && !cancelled; attempt += 1) {
        const [threadsReady, projectsReady] = await Promise.all([refreshThreads(), refreshProjects()]);
        if (threadsReady && projectsReady) {
          hydrated = true;
          break;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 350 * (attempt + 1)));
      }
      if (!cancelled && hydrated) setInitialHydrationComplete(true);
    };
    void hydrate();
    return () => { cancelled = true; };
  }, [apiBase]);

  const createNewThread = async (projectId: string = "", requestedTitle: string = "New Session"): Promise<string> => {
    if (threadCreationFlightRef.current) return threadCreationFlightRef.current;
    const idempotencyKey = crypto.randomUUID();
    const flight = (async (): Promise<string> => {
      try {
        const resp = await fetch(`${apiBase}/threads`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: requestedTitle, source: "web", project_id: projectId, idempotency_key: idempotencyKey }),
        });
        if (!resp.ok) throw new Error(`Create thread failed (${resp.status})`);
        const data = await resp.json();
        const nextThread = { id: String(data.thread_id), name: String(data.title || "New Session"), at: normalizeTimestampMs(data.last_active_at || data.created_at || Date.now()), projectId: String(data.project_id || projectId || "") };
        projectionRevisionRef.current.set(nextThread.id, 0);
        sessionProjectionRef.current.set(nextThread.id, { messages: [], activities: [] });
        activeThreadIdRef.current = nextThread.id;
        setThreads((prev) => [
          nextThread,
          ...prev.filter((item) => item.id !== nextThread.id && !isEmptySessionDraft(item)),
        ]);
        setActiveThreadId(nextThread.id);
        useAppStore.setState({ messages: [] });
        setActivities([]);
        clearResearchRuns();
        setPendingApproval(null);
        setStreaming(false);
        return nextThread.id;
      } catch (e) {
        console.error("Failed to create thread:", e);
        return "";
      }
    })();
    threadCreationFlightRef.current = flight;
    try {
      return await flight;
    } finally {
      if (threadCreationFlightRef.current === flight) threadCreationFlightRef.current = null;
    }
  };

  const switchThread = (id: string) => {
    if (id === activeThreadId) return;
    // A Session owns its request. Navigation changes only the projection;
    // it must not cancel another Session's durable execution.
    const previousId = String(activeThreadIdRef.current || activeThreadId || "");
    if (previousId) {
      sessionProjectionRef.current.set(previousId, {
        messages: useAppStore.getState().messages,
        activities,
      });
    }
    activeThreadIdRef.current = id;
    setThreads((prev) => prev.filter((item) => item.id === id || !isEmptySessionDraft(item)));
    setActiveThreadId(id);
    // The live timeline belongs to the Session that was visible; history reload restores it.
    lean.finish();
    setMention(null);
    setStreaming(streamControllersRef.current.has(id));
    // In a real app, we might fetch history from backend here.
    // For now, we'll clear local state to start fresh in the new context.
    const cachedProjection = sessionProjectionRef.current.get(id);
    useAppStore.setState({ messages: cachedProjection?.messages || [] });
    setActivities(cachedProjection?.activities || []);
    clearResearchRuns();
    setPendingApproval(null);
  };

  const deleteThread = async (id: string) => {
    try {
      const response = await fetch(`${apiBase}/threads/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!response.ok) throw new Error(`Delete Session failed (${response.status})`);
    } catch (e2) {
      console.error("Failed to delete thread:", e2);
    }
    const nextThreads = threads.filter((t) => t.id !== id);
    setThreads(nextThreads);
    if (id === activeThreadId) {
      if (nextThreads[0]) switchThread(nextThreads[0].id);
      else {
        cancelSessionTurn(id);
        activeThreadIdRef.current = "";
        setActiveThreadId("");
        useAppStore.setState({ messages: [] });
        setActivities([]);
        setPendingApproval(null);
        setThreadState(null);
        setActiveProjectId("");
      }
    }
  };

  const attachFolder = async (candidatePath: string = "") => {
    let path = candidatePath.trim();
    if (!path) {
      if (desktopMode) {
        try { path = String(await pickDesktopProjectFolder() || ""); } catch { /* surfaced by the desktop host */ }
      } else {
        try {
          const picker = await fetch(`${apiBase}/projects/pick-folder`, { method: "POST" });
          if (picker.ok) path = String((await picker.json()).path || "");
        } catch { /* native picker may not be available outside the desktop host */ }
      }
    }
    if (!path) return;
    const response = await fetch(`${apiBase}/projects/attach-folder`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, session_id: activeThreadId, trust_state: "trusted" }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "Could not attach folder");
    const project = await response.json();
    await refreshProjects();
    setActiveProjectId(String(project.id || ""));
    setThreads(items => items.map(item => item.id === activeThreadId ? { ...item, projectId: String(project.id || "") } : item));
    await refreshThreadState(activeThreadId);
  };

  const folderPathFromDrop = (event: React.DragEvent): string => {
    const file = event.dataTransfer.files?.[0] as (File & { path?: string }) | undefined;
    if (file?.path) return file.path;
    const uri = event.dataTransfer.getData("text/uri-list").split(/\r?\n/).find(line => line && !line.startsWith("#")) || "";
    const plain = event.dataTransfer.getData("text/plain").trim();
    const value = uri || plain;
    if (/^file:\/\//i.test(value)) {
      try { return decodeURIComponent(new URL(value).pathname).replace(/^\/(?:([A-Za-z]:))/, "$1"); } catch { return ""; }
    }
    return /^[A-Za-z]:[\\/]/.test(value) ? value : "";
  };

  const renameThread = async (id: string, title: string) => {
    const response = await fetch(`${apiBase}/threads/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
    if (!response.ok) return;
    const data = await response.json();
    setThreads(items => items.map(item => item.id === id ? { ...item, name: String(data.title || title) } : item));
  };

  const {
    providerInfo, setProviderModels, providerDraft, setProviderDraft, providerError, switchingProvider, backendOnline, setBackendOnline, lmStudioOnly,
    refreshProviderInfo, showModelPicker, modelPickerOptions, modelPickerValue,
  } = useProviderSettings({ apiBase, activeThreadIdRef, cancelSessionTurn });
  const [thinkingEnabled, setThinkingEnabled] = useState<boolean>(
    () => window.localStorage.getItem("echospeak.chat.thinking_enabled") !== "false",
  );
  const [reasoningEffort, setReasoningEffort] = useState<
    "minimal" | "low" | "medium" | "high" | "extra_high" | "max" | "ultra"
  >(() => {
    const stored = window.localStorage.getItem("echospeak.chat.reasoning_effort");
    return stored === "minimal" || stored === "low" || stored === "medium" || stored === "high" ||
      stored === "extra_high" || stored === "max" || stored === "ultra"
      ? stored
      : "medium";
  });
  useEffect(() => {
    window.localStorage.setItem("echospeak.chat.thinking_enabled", String(thinkingEnabled));
  }, [thinkingEnabled]);
  useEffect(() => {
    window.localStorage.setItem("echospeak.chat.reasoning_effort", reasoningEffort);
  }, [reasoningEffort]);

  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const chatBottomRef = useRef<HTMLDivElement | null>(null);
  /** Follow new messages while the user is at the bottom; leave the view alone once they scroll up (app/chatFollow.ts). */
  const followerRef = useRef(new ChatFollower());
  const sessionScrollRef = useRef<Map<string, { top: number; atBottom: boolean }>>(new Map());
  const pinBottomRafRef = useRef(0);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      const nextHeight = Math.min(el.scrollHeight, 156);
      el.style.height = `${nextHeight}px`;
      el.style.overflowY = el.scrollHeight > 156 ? "auto" : "hidden";
    }
  }, [input]);


  const timeline = useMemo<TimelineItem[]>(() => {
    // Chat is a conversation projection. Durable operational evidence stays in
    // Studio/Viewer. While streaming, thinking steps are ephemeral live chrome.
    // After the turn, only durable actionable errors remain with message bubbles.
    const merged: TimelineItem[] = [
      ...messages.map(
        (m): TimelineItem => ({
          kind: "message",
          id: m.id,
          at: m.at,
          msg: m,
        })
      ),
      ...activities
        .filter((item) => shouldIncludeChatActivity(String(item.kind || ""), streaming))
        .map(
          (a): TimelineItem => ({
            kind: "activity",
            id: a.id,
            at: a.at,
            item: a,
          })
        ),
    ];
    // Chronological user/assistant history with live activity + persistent errors.
    const kindRank = (k: TimelineItem["kind"]) =>
      k === "message" ? 0 : k === "activity" ? 1 : 2;
    merged.sort((a, b) => {
      const dt = a.at - b.at;
      if (dt !== 0) return dt;
      return kindRank(a.kind) - kindRank(b.kind);
    });
    return merged;
  }, [messages, activities, streaming]);

  const lastMsgLen = messages.length ? (messages[messages.length - 1]?.text || "").length : 0;
  const activityLen = activities.length;

  /**
   * Pin chat fully to the latest content. Instant scroll only — smooth scrolling
   * gets interrupted mid-animation when content keeps growing and leaves the view at ~90–98%.
   */
  const scrollChatToBottom = useCallback((force: boolean = false) => {
    if (!force && !followerRef.current.shouldPin()) return;
    const el = chatScrollRef.current;
    if (!el) return;

    const pin = () => {
      // The user may have started scrolling up since this was scheduled.
      if (!force && !followerRef.current.shouldPin()) return;
      // Direct assignment is more reliable than scrollTo for max bottom.
      el.scrollTop = el.scrollHeight;
      // Bottom sentinel (if mounted) — catches residual subpixel / padding cases.
      try {
        chatBottomRef.current?.scrollIntoView({ block: "end", behavior: "auto" });
      } catch {
        // ignore
      }
      el.scrollTop = el.scrollHeight;
    };

    if (pinBottomRafRef.current) cancelAnimationFrame(pinBottomRafRef.current);
    // Two frames: after React paint, then after layout (markdown / framer-motion / embeds).
    pinBottomRafRef.current = requestAnimationFrame(() => {
      pin();
      pinBottomRafRef.current = requestAnimationFrame(pin);
    });
  }, []);

  const onChatScroll = () => {
    const el = chatScrollRef.current;
    if (!el) return;
    followerRef.current.onScroll(el);
    const sessionId = String(activeThreadIdRef.current || "");
    if (sessionId) sessionScrollRef.current.set(sessionId, { top: el.scrollTop, atBottom: followerRef.current.following });
  };
  const onChatWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (chatScrollRef.current) followerRef.current.onWheel(event.deltaY, chatScrollRef.current);
  };
  const onChatKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (chatScrollRef.current) followerRef.current.onKey(event.key, chatScrollRef.current);
  };
  const onChatTouchStart = () => followerRef.current.onTouchStart();
  const onChatTouchEnd = () => {
    if (chatScrollRef.current) followerRef.current.onTouchEnd(chatScrollRef.current);
  };

  useLayoutEffect(() => {
    const el = chatScrollRef.current;
    if (!el || !activeThreadId) return;
    const saved = sessionScrollRef.current.get(activeThreadId);
    requestAnimationFrame(() => {
      if (saved?.atBottom || !saved) el.scrollTop = el.scrollHeight;
      else el.scrollTop = Math.min(saved.top, Math.max(0, el.scrollHeight - el.clientHeight));
      followerRef.current.reset(saved?.atBottom ?? true, el);
    });
  }, [activeThreadId]);

  useEffect(() => {
    // initial mount / tab switch — always jump to latest
    if (leftTab === "chat") {
      followerRef.current.reset(true);
      scrollChatToBottom(true);
    }
  }, [leftTab, scrollChatToBottom]);

  // Re-pin whenever timeline / draft / tools grow, unless user scrolled up.
  useLayoutEffect(() => {
    if (leftTab !== "chat") return;
    scrollChatToBottom(false);
  }, [
    leftTab,
    timeline.length,
    lastMsgLen,
    activityLen,
    streaming,
    speaking,
    pendingApproval?.has_pending,
    scrollChatToBottom,
  ]);

  // While streaming/speaking, content height keeps changing after effects run — keep pinned.
  useEffect(() => {
    if (leftTab !== "chat") return;
    if (!streaming && !speaking) return;
    const id = window.setInterval(() => {
      if (followerRef.current.shouldPin()) scrollChatToBottom(false);
    }, 80);
    return () => window.clearInterval(id);
  }, [leftTab, streaming, speaking, scrollChatToBottom]);

  // When message/tool nodes resize (markdown, embeds, ops card), stay at true bottom.
  useEffect(() => {
    const el = chatScrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;

    const pinIfStuck = () => {
      if (followerRef.current.shouldPin()) scrollChatToBottom(false);
    };

    const ro = new ResizeObserver(() => pinIfStuck());
    const observeChildren = () => {
      ro.disconnect();
      for (const child of Array.from(el.children)) ro.observe(child);
    };
    observeChildren();

    const mo = new MutationObserver(() => {
      observeChildren();
      pinIfStuck();
    });
    mo.observe(el, { childList: true, subtree: true, characterData: true });

    return () => {
      ro.disconnect();
      mo.disconnect();
    };
  }, [scrollChatToBottom, leftTab]);


  const sendText = async (overrideText?: string, voiceTranscript?: VoiceTranscript) => {
    const raw = overrideText ?? input;
    if (!raw.trim()) return;
    // Session creation has one explicit owner: the + controls in the sidebar.
    // Composer submission, navigation, hydration, and assistant replies never
    // invent a Session.
    const streamThreadId = String(activeThreadIdRef.current || activeThreadId || "").trim();
    if (!streamThreadId) return;
    const runRequestId = crypto.randomUUID();
    const streamProjectId = String(activeProjectIdRef.current || activeProjectId || "").trim();
    cancelSessionTurn(streamThreadId);
    const streamController = new AbortController();
    streamControllersRef.current.set(streamThreadId, streamController);
    activeRequestIdsRef.current.set(streamThreadId, runRequestId);
    setSessionInFlight(streamThreadId, true);

    followerRef.current.reset(true); // sending a message always follows its reply
    if (!overrideText) setInput("");

    const clampContext = (t: string, n: number) => {
      const s = (t || "").replace(/\s+/g, " ").trim();
      if (s.length <= n) return s;
      return s.slice(0, n).trimEnd() + "…";
    };

    const shouldAttachMonitor = (q: string) => {
      const low = (q || "").toLowerCase();
      if (!monitoring) return false;
      if (!monitorText || !monitorText.trim()) return false;
      if (low.includes("on my screen") || low.includes("on my desktop") || low.includes("what am i looking") || low.includes("what do you see")) return true;
      if (low.includes("watching") || low.includes("seeing") || low.includes("look at") || low.includes("this") || low.includes("that") || low.includes("here")) return true;
      return false;
    };

    const desktopContext = !voiceTranscript && shouldAttachMonitor(raw) ? clampContext(monitorText, 1200) : "";
    const requestText = desktopContext ? `${raw}\n\nLive desktop context:\n${desktopContext}` : raw;

    const ctxWindow = Number(providerInfo?.context_window || 0) || 32768;
    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      text: raw,
      at: Date.now(),
      usage: buildMessageUsage(raw, messages, ctxWindow, {
        provider: providerInfo?.provider,
        model: providerInfo?.model,
      }),
    };
    projectionRevisionRef.current.set(
      streamThreadId,
      (projectionRevisionRef.current.get(streamThreadId) || 0) + 1,
    );
    addMessage(userMsg);
    setInput("");
    setStreaming(true);
    lean.start(runRequestId);
    setMention(null);
    /** Backend Execution id once the run starts. */
    let durableTurnId = "";
    let finalHandled = false;
    let streamWasHidden = false;
    // Close any prior-Turn running chrome so this turn never inherits it.
    setActivities((prev) => {
      const closed = prev.map((a) => {
        if (a.kind === "tool" && a.status === "running") {
          return {
            ...a,
            status: "error" as const,
            output: a.output || "Superseded by a new Turn",
          };
        }
        if (a.kind === "thinking" && a.steps?.some((s) => s.status === "running")) {
          return {
            ...a,
            steps: a.steps.map((s) =>
              s.status === "running" ? { ...s, status: "done" as const } : s
            ),
          };
        }
        return a;
      });
      // Drop idle "thinking…" shells from prior turns (keep completed tool rows).
      const pruned = closed.filter(
        (a) =>
          !(
            a.kind === "thinking" &&
            (!a.steps?.length || a.steps.every((s) => s.content === "thinking…" || s.status === "done"))
          )
      );
      return pruned;
    });
    try {
      const resp = await fetch(`${apiBase}/query/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: streamController.signal,
        body: JSON.stringify({
          message: requestText,
          include_memory: true,
          thread_id: streamThreadId,
          client_request_id: runRequestId,
          thinking_enabled: thinkingEnabled,
          reasoning_effort: reasoningEffort,
          transport: voiceTranscript ? "voice" : "chat",
          voice_turn_id: voiceTranscript?.voiceTurnId,
        }),
      });
      if (!resp.ok) {
        const errText = await resp.text();
        throw new Error(errText || `HTTP ${resp.status}`);
      }

      if (!resp.body) {
        throw new Error("No response body");
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      // Monotonic stream seq (backend) — ignore reordered/stale reconnect frames.
      let maxStreamSeq = 0;

      while (true) {
        if (streamController.signal.aborted) break;
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx = buffer.indexOf("\n");
        while (idx !== -1) {
          const line = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 1);
          idx = buffer.indexOf("\n");
          if (!line) continue;

          let evt: AgentStreamEvent & { seq?: number };
          try {
            evt = JSON.parse(line) as AgentStreamEvent & { seq?: number };
          } catch (e) {
            continue;
          }
          if (streamController.signal.aborted) continue;
          if (!isStreamThreadCurrent(streamThreadId, activeThreadIdRef.current)) {
            streamWasHidden = true;
            if (evt.type === "final") finalHandled = true;
            continue;
          }
          const evtSeq = Number((evt as { seq?: number }).seq || 0);
          if (evtSeq > 0) {
            if (evtSeq <= maxStreamSeq) {
              // Stale or duplicated frame after reconnect — do not apply.
              continue;
            }
            maxStreamSeq = evtSeq;
          }
          // Lean runtime events render through the agent timeline, not the legacy cards.
          if (isLeanEvent(evt as unknown as LeanEvent)) {
            const leanEvt = evt as unknown as LeanEvent;
            if (leanEvt.type === "run_start") {
              const execId = String(leanEvt.execution_id || "");
              if (execId) {
                durableTurnId = execId;
                activeExecutionIdsRef.current.set(streamThreadId, execId);
              }
              // The legacy bootstrap "thinking…" card is not part of a lean turn.
              setActivities((prev) => prev.filter((a) => !(a.kind === "thinking" && a.request_id === runRequestId)));
            }
            if (leanEvt.type === "memory_saved" && typeof leanEvt.memory_count === "number") {
              continue;
            }
            if (leanEvt.type !== "final") {
              lean.push(leanEvt);
              continue;
            }
            // Final: commit exactly what streamed, one message per agent.
            if (finalHandled) continue;
            finalHandled = true;
            const done = lean.finish();
            const finalExecId = String(leanEvt.execution_id || durableTurnId || "");
            const committed = done ? done.order.map((id) => done.messages[id]).filter(Boolean) : [];
            const ctxWindowLean = Number(providerInfo?.context_window || 0) || 32768;
            if (!canApplyFinalToChat({
              activeThreadId: String(activeThreadIdRef.current || ""),
              activeProjectId: String(activeProjectIdRef.current || ""),
              ownedThreadId: streamThreadId,
              ownedProjectId: streamProjectId,
              streamOpen: streamControllersRef.current.get(streamThreadId) === streamController,
            })) {
              setStreaming(false);
              continue;
            }
            for (const item of committed) {
              const text = item.text || item.segments.filter((s) => s.kind === "text").map((s) => (s as { text: string }).text).join("\n\n").trim();
              addMessage({
                id: item.messageId,
                role: "assistant",
                text,
                at: item.startedAt,
                skipTypewriter: true,
                lean: { ...item, status: item.status === "streaming" ? "done" : item.status, text },
                executionId: finalExecId || undefined,
                clientRequestId: runRequestId,
                usage: buildMessageUsage(text, useAppStore.getState().messages, ctxWindowLean, {
                  provider: providerInfo?.provider,
                  model: providerInfo?.model,
                }),
              });
            }
            if (!committed.length && String(leanEvt.response || "").trim()) {
              addMessage({ id: crypto.randomUUID(), role: "assistant", text: String(leanEvt.response), at: Date.now(), skipTypewriter: true });
            }
            if (leanEvt.thread_state) {
              setThreadState(leanEvt.thread_state);
              setActiveProjectId(String(leanEvt.thread_state.active_project_id || ""));
            }
            setStreaming(false);
            const spokenLean = String(committed[committed.length - 1]?.text || leanEvt.response || "").trim();
            if (spokenLean && (voiceReadAloud || voiceConversationMode)) {
              void speakLocalText(spokenLean, {
                clientTurnId: voiceTranscript?.clientTurnId || runRequestId,
                requestId: runRequestId,
                executionId: finalExecId,
              }).then((played) => {
                if (played && voiceConversationMode && activeThreadIdRef.current === streamThreadId && !streamControllersRef.current.has(streamThreadId)) {
                  void start();
                }
              });
            }
            void refreshThreads();
            void refreshRoster();
            continue;
          }
          if (evt.type === "error") {
            setStreaming(false);
            setActivities((prev) => [
              ...prev,
              { kind: "error", id: crypto.randomUUID(), message: evt.message, at: Date.now() },
            ]);
          }
        }
      }
    } catch (err) {
      if (streamController.signal.aborted) {
        // Explicit same-Session supersession/delete: never paint a cancellation error.
        return;
      }
      if (!isStreamThreadCurrent(streamThreadId, activeThreadIdRef.current)) return;
      const msg = String(err);
      const pretty = msg.includes("Failed to fetch") ? `Backend offline (${apiBase})` : msg;
      setBackendOnline(false);
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${pretty}`, at: Date.now() });
      setActivities((prev) => [
        ...prev,
        { kind: "error", id: crypto.randomUUID(), message: pretty, at: Date.now() },
      ]);
    } finally {
      const owned = ownsStreamCleanup(streamControllersRef.current.get(streamThreadId), streamController);
      const sameThread = isStreamThreadCurrent(streamThreadId, activeThreadIdRef.current);
      const aborted = streamController.signal.aborted;

      if (owned) {
        streamControllersRef.current.delete(streamThreadId);
        if (activeRequestIdsRef.current.get(streamThreadId) === runRequestId) {
          activeRequestIdsRef.current.delete(streamThreadId);
          activeExecutionIdsRef.current.delete(streamThreadId);
        }
        setSessionInFlight(streamThreadId, false);
      }

      // A superseded controller owns no visible or durable projection cleanup.
      if (!owned) return;

      // Only the visible Session owns the current projection's phase machine.
      if (sameThread && aborted) {
      } else if (sameThread) {
      }

      // Do not mutate chat of a different Session (switch already cleared UI).
      if (!sameThread) {
        return;
      }

      if (streamWasHidden) {
        // Frames skipped while this Session was hidden are reconstructed from
        // canonical Turns/ToolRuns, preventing duplicate or partially measured rows.
        await loadHistory(streamThreadId);
      }

      setStreaming(false);
      // A lean turn interrupted before its final event keeps what already streamed.
      if (lean.stateRef.current?.requestId === runRequestId) {
        const leftover = lean.finish();
        if (leftover && !finalHandled) {
          for (const id of leftover.order) {
            const item = leftover.messages[id];
            if (!item || !item.segments.length) continue;
            const text = item.segments.filter((s) => s.kind === "text").map((s) => (s as { text: string }).text).join("\n\n").trim();
            addMessage({
              id: item.messageId,
              role: "assistant",
              text: text || (aborted ? "Stopped." : "Interrupted."),
              at: item.startedAt,
              skipTypewriter: true,
              lean: {
                ...item,
                status: "failed",
                text,
                segments: item.segments.map((s) =>
                  s.kind === "tool" && s.status === "running"
                    ? { ...s, status: "failed" as const }
                    : s.kind === "thinking" && !s.endedAt
                    ? { ...s, endedAt: Date.now() }
                    : s
                ),
              },
            });
          }
        }
      }
      if (!finalHandled && streamThreadId && !aborted) {
        void refreshThreadState(streamThreadId);
        void refreshPendingApproval(streamThreadId);
      }
      if (finalHandled && !aborted && sameThread) {
        window.setTimeout(async () => {
          try {
            const response = await fetch(
              `${apiBase}/query/queue/claim?thread_id=${encodeURIComponent(streamThreadId)}`,
              { method: "POST" },
            );
            if (!response.ok) return;
            const payload = await response.json();
            const message = String(payload?.item?.message || "").trim();
            if (payload?.claimed && message && activeThreadIdRef.current === streamThreadId) {
              void sendText(message);
            }
          } catch {
            // The durable queue remains available for the next idle reconnect.
          }
        }, 200);
      }
    }
  };

  // ── Composer toolbar: always one row; collapses by its own width ──
  const [toolbarSize, setToolbarSize] = useState<"full" | "icons" | "compact" | "mini">("full");
  const [toolbarMenuOpen, setToolbarMenuOpen] = useState(false);
  const toolbarObserverRef = useRef<ResizeObserver | null>(null);
  const toolbarRef = useCallback((el: HTMLDivElement | null) => {
    toolbarObserverRef.current?.disconnect();
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width;
      setToolbarSize(width >= 960 ? "full" : width >= 760 ? "icons" : width >= 600 ? "compact" : "mini");
    });
    observer.observe(el);
    toolbarObserverRef.current = observer;
  }, []);
  useEffect(() => {
    if (toolbarSize !== "mini") setToolbarMenuOpen(false);
  }, [toolbarSize]);
  useEffect(() => {
    if (!toolbarMenuOpen) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      const outside = event instanceof KeyboardEvent
        ? event.key === "Escape"
        : !(event.target as HTMLElement | null)?.closest?.(".toolbar-overflow");
      if (outside) setToolbarMenuOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [toolbarMenuOpen]);
  const toggleMonitor = () =>
    setMonitoring((v) => {
      const next = !v;
      if (next) refreshMonitor();
      return next;
    });

  const stopActiveTurn = () => {
    stopTts();
    setVoicePhase("idle");
    cancelSessionTurn(activeThreadIdRef.current || activeThreadId, true);
  };
  // Esc stops the running turn unless a menu or picker is using it.
  useEffect(() => {
    if (!streaming) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (mention || toolbarMenuOpen || document.querySelector(".es-modal-scrim, .st-root")) return;
      stopActiveTurn();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    const sessionId = String(activeThreadId || "").trim();
    if (!sessionId || streaming || streamControllersRef.current.has(sessionId)) return;
    let cancelled = false;
    const resumeQueued = async () => {
      try {
        const response = await fetch(
          `${apiBase}/query/queue/claim?thread_id=${encodeURIComponent(sessionId)}`,
          { method: "POST" },
        );
        if (!response.ok || cancelled) return;
        const payload = await response.json();
        const message = String(payload?.item?.message || "").trim();
        if (payload?.claimed && message && activeThreadIdRef.current === sessionId) {
          void sendText(message);
        }
      } catch {
        // Persisted input remains queued until the Session is idle again.
      }
    };
    void resumeQueued();
    return () => { cancelled = true; };
    // sendText intentionally follows the current render's exact Session state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeThreadId, apiBase, streaming]);

  const submitVoiceTranscript = async (transcript: VoiceTranscript) => {
    if (
      String(activeThreadIdRef.current || "") !== transcript.sessionId ||
      String(activeProjectIdRef.current || "") !== transcript.projectId
    ) {
      setVoicePhase("error");
      setVoiceNotice("Voice capture ended after the active Session changed, so it was not submitted.");
      return;
    }
    setInput(transcript.text);
    if (transcript.controlHint === "cancel_active") {
      stopTts();
      if (activeRequestIdsRef.current.get(transcript.sessionId)) {
        cancelSessionTurn(transcript.sessionId, true, transcript);
      } else {
        void fetch(
          `${apiBase}/media-runtime/voice/turns/${encodeURIComponent(transcript.voiceTurnId)}/cancel`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: transcript.sessionId }),
          },
        ).catch(() => undefined);
      }
      setVoicePhase("idle");
      setVoiceNotice("Stopped.");
      setInput("");
      return;
    }
    await sendText(transcript.text, transcript);
  };

  const {
    voicePhase, setVoicePhase, voiceNotice, setVoiceNotice, voiceInputLevel,
    voiceReadAloud, voiceConversationMode, wakeWordEnabled,
    toggleReadAloud, toggleVoiceMode, toggleWakeWord, start, stop, speakLocalText,
  } = useVoice({
    apiBase, activeThreadId, activeProjectId, activeThreadIdRef, activeProjectIdRef,
    streaming, listening, setListening, speechEnabled, onTranscript: submitVoiceTranscript,
  });



  const refreshMonitor = async () => {
    try {
      const resp = await fetchWithTimeout(`${apiBase}/vision/analyze`, { method: "POST" }, 6000);
      if (!resp.ok) {
        const t = await resp.text();
        throw new Error(t || `${resp.status} ${resp.statusText}`);
      }
      const data = (await resp.json()) as VisionAnalyzeResponse;
      setMonitorText(String(data?.text || ""));
    } catch (e) {
    }
  };

  useEffect(() => {
    // Also with no chat open yet (fresh install): show the real default, not the first list entry.
    if (!initialHydrationComplete) return;
    refreshProviderInfo({ allowRetry: true });
  }, [apiBase, activeThreadId, initialHydrationComplete]);



  useEffect(() => {
    if (!activeGroup) {
      setActiveGroupPos(null);
      return;
    }

    const computePos = () => {
      const btn = activeGroupButtonRef.current;
      if (!btn) return;
      const r = btn.getBoundingClientRect();
      setActiveGroupPos({
        top: Math.round(r.bottom + 8),
        left: Math.round(r.left),
      });
    };

    computePos();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActiveGroup(null);
    };

    const onPointerDown = (e: MouseEvent | PointerEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      const menu = activeGroupMenuRef.current;
      const btn = activeGroupButtonRef.current;
      if (menu && menu.contains(t)) return;
      if (btn && btn.contains(t)) return;
      setActiveGroup(null);
    };

    const onWindowChange = () => {
      // Reposition on scroll/resize so the menu doesn't look "stuck".
      computePos();
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("resize", onWindowChange);
    window.addEventListener("scroll", onWindowChange, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("resize", onWindowChange);
      window.removeEventListener("scroll", onWindowChange, true);
    };
  }, [activeGroup]);

  useEffect(() => {
    if (!monitoring) return;
    let cancelled = false;
    let inFlight = false;

    const tick = async () => {
      if (cancelled) return;
      if (inFlight) {
        window.setTimeout(tick, 1200);
        return;
      }
      inFlight = true;
      try {
        await refreshMonitor();
      } finally {
        inFlight = false;
      }
      window.setTimeout(tick, 2200);
    };

    tick();
    return () => {
      cancelled = true;
    };
  }, [monitoring, apiBase]);

  useEffect(() => {
    saveRuntimeLayout(typeof window !== "undefined" ? window.localStorage : null, {
      sidebarVisible: showSidebar,
      sidebarCollapsed,
      visualizerVisible: false,
      visualizerDensity: "normal",
    });
  }, [showSidebar, sidebarCollapsed]);

  useEffect(() => {
    const onResize = () => setNarrowLayout(window.innerWidth < 900);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const studioOpen = desktopMode
    ? desktopSettingsOpen
    : leftTab !== "chat" && leftTab !== "research";
  const mediaWorkspaceOpen = !desktopMode && mediaRouteActive;
  const desktopContextualWorkspace = false;
  const activeWorkspaceLabel = desktopMode ? "Conversation" : "EchoSpeak";
  const closeStudio = () => {
    if (desktopSettingsWindow) {
      void controlDesktopWindow("close");
      return;
    }
    setLeftTab("chat");
    if (desktopMode) setDesktopSettingsOpen(false);
    if (mediaRouteActive) navigate("/app");
  };
  useEffect(() => {
    if (!desktopMode || !studioOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeStudio();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [desktopMode, studioOpen]);

  const rightOpen = mainPage === "chat" && Boolean(openArtifact || activityOpen);
  const rightDocked = rightOpen && !narrowLayout;
  const shellColumns = [
    desktopMode
      ? [showSidebar ? (sidebarCollapsed || narrowLayout ? "56px" : "288px") : null, "minmax(0, 1fr)"].filter(Boolean).join(" ")
      : runtimeGridColumns({
        sidebarVisible: showSidebar,
        sidebarCollapsed: sidebarCollapsed || narrowLayout,
        visualizerVisible: false,
        visualizerDensity: "normal",
      }),
    rightDocked ? `${panelWidth}px` : "",
  ].filter(Boolean).join(" ");

  const closePage = useCallback(() => setMainPage("chat"), []);
  const sidebarWidthPx = showSidebar ? (sidebarCollapsed || narrowLayout ? 56 : desktopMode ? 288 : 252) : 0;
  /** Room chat + side panel share (the shell minus the left sidebar). */
  const measurePanelRoom = useCallback(() => {
    const el = shellRef.current;
    const layoutWidth = el?.clientWidth || window.innerWidth;
    const scale = el ? el.getBoundingClientRect().width / (layoutWidth || 1) || 1 : 1;
    return { room: layoutWidth - sidebarWidthPx, scale };
  }, [sidebarWidthPx]);
  useEffect(() => {
    savePanelWidth(panelWidth);
  }, [panelWidth]);
  useEffect(() => {
    // Keep the chat usable when the window shrinks.
    const fit = () => setPanelWidth((w) => clampPanelWidth(w, measurePanelRoom().room));
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [measurePanelRoom, rightOpen]);
  useEffect(() => {
    if (openArtifact) setRightTab("artifact");
  }, [openArtifact]);
  const activityItems = useMemo(() => {
    const live = lean.live ? lean.live.order.map((id) => lean.live!.messages[id]) : [];
    return collectActivity([...messages.map((m) => m.lean), ...live]);
  }, [messages, lean.live]);
  const seenTerminalRef = useRef<Set<string>>(new Set());
  const seenArtifactsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const live = lean.live;
    if (!live) return;
    for (const id of live.order) {
      for (const seg of live.messages[id]?.segments || []) {
        if (seg.kind === "tool" && TERMINAL_TOOLS.has(seg.name) && seg.status === "running" && !seenTerminalRef.current.has(seg.id)) {
          seenTerminalRef.current.add(seg.id);
          setActivityOpen(true);
          if (!openArtifact) setRightTab("activity");
        }
        if (seg.kind !== "tool" || !seg.widgets) continue;
        for (const widget of seg.widgets as { type?: string; data?: { id?: string; version?: number } }[]) {
          if (widget?.type !== "artifact" || !widget.data?.id) continue;
          const key = `${widget.data.id}@${widget.data.version}`;
          if (seenArtifactsRef.current.has(key)) continue;
          seenArtifactsRef.current.add(key);
          setOpenArtifact({ id: widget.data.id });
        }
      }
    }
  }, [lean.live]); // eslint-disable-line react-hooks/exhaustive-deps
  const widgetEnv = useMemo<WidgetEnv>(
    () => ({ apiBase, openArtifact: (id, version) => setOpenArtifact({ id, version }) }),
    [apiBase],
  );
  /** Open (or create) the one-to-one chat with an agent. Echo's chat is the most recent plain chat. */
  const openAgentChat = async (agent: LeanPersona) => {
    setMainPage("chat");
    if (agent.id === "echo") {
      const recent = threads.find((thread) => !roomThreadIds.has(thread.id));
      if (recent) switchThread(recent.id);
      else void createNewThread();
      return;
    }
    let room = rooms.find((r) => r.kind === "direct" && r.agent_ids.length === 1 && r.agent_ids[0] === agent.id);
    if (!room) {
      try {
        room = await leanClient.createRoom({ name: agent.name, agent_ids: [agent.id], kind: "direct" });
        await refreshRoster();
        await refreshThreads();
      } catch (error) {
        console.warn("Could not open a chat with", agent.name, error);
        return;
      }
    }
    const opened = room;
    setThreads((prev) => (prev.some((t) => t.id === opened.thread_id) ? prev : [{ id: opened.thread_id, name: opened.name, at: Date.now() }, ...prev]));
    switchThread(opened.thread_id);
  };
  const openRoom = (room: LeanRoom) => {
    setMainPage("chat");
    setThreads((prev) => (prev.some((t) => t.id === room.thread_id) ? prev : [{ id: room.thread_id, name: room.name, at: Date.now() }, ...prev]));
    switchThread(room.thread_id);
  };
  const openArtifactFromPage = (item: ArtifactSummary) => {
    setMainPage("chat");
    if (item.session_id && item.session_id !== activeThreadId) switchThread(item.session_id);
    setOpenArtifact({ id: item.id, version: item.version });
  };

  return (
    <div
      className="echo-root"
      data-execution-profile={desktopMode ? desktopExecutionProfile(desktopSurface) : undefined}
      style={{
        width: "100%",
        height: "100%",
        maxHeight: "100dvh",
        background: colors.bg,
        color: colors.text,
        overflow: "hidden",
        position: "relative",
      }}
    >
      <style>{globalCss}</style>
      <style>{leanCss}</style>
      <style>{settingsCss}</style>
      {agentEditor.open ? (
        <AgentEditor
          agent={agentEditor.agent}
          toolsets={toolsetIds.length ? toolsetIds : ["core", "research", "terminal", "vision", "memory", "skills", "desktop", "comms"]}
          onClose={() => setAgentEditor({ open: false, agent: null })}
          onSave={async (payload) => {
            if (agentEditor.agent) await leanClient.updateAgent(agentEditor.agent.id, payload);
            else await leanClient.createAgent(payload);
            await refreshRoster();
          }}
          onDelete={
            agentEditor.agent && !agentEditor.agent.builtin
              ? async () => {
                  await leanClient.deleteAgent(agentEditor.agent!.id);
                  await refreshRoster();
                }
              : undefined
          }
        />
      ) : null}
      {roomDialog.open ? (
        <RoomDialog
          room={roomDialog.room}
          agents={agents}
          onClose={() => setRoomDialog({ open: false, room: null })}
          onSave={async (payload) => {
            if (roomDialog.room) {
              await leanClient.updateRoom(roomDialog.room.id, payload);
              await refreshRoster();
              return;
            }
            const room = await leanClient.createRoom({ ...payload, kind: "group" });
            await refreshRoster();
            setThreads((prev) => [{ id: room.thread_id, name: room.name, at: Date.now() }, ...prev.filter((t) => t.id !== room.thread_id)]);
            switchThread(room.thread_id);
          }}
        />
      ) : null}
      {!showSidebar && !studioOpen ? (
        <button
          className="icon-button"
          onClick={() => setShowSidebar(true)}
          title="Show sidebar"
          style={{ position: "fixed", left: 10, top: 10, zIndex: 100 }}
        >
          ☰
        </button>
      ) : null}
      <div
        ref={shellRef}
        className={
          "app-shell" +
          (studioOpen && !desktopMode ? " is-studio-covered" : "") +
          (desktopMode ? " desktop-single-workspace" : "")
        }
        style={{
          gridTemplateColumns: shellColumns,
        }}
        aria-hidden={!desktopMode && studioOpen || undefined}
      >
        {showSidebar ? <ProjectSidebar
          desktop={desktopMode}
          hydrating={!initialHydrationComplete}
          collapsed={sidebarCollapsed || narrowLayout}
          projects={projects}
          sessions={threads.filter((thread) => !roomThreadIds.has(thread.id))}
          agents={agents.length ? {
            count: agents.length,
            onNew: () => setAgentEditor({ open: true, agent: null }),
            list: (
              <AgentRows
                agents={agents}
                rooms={rooms}
                activeThreadId={activeThreadId}
                onOpenAgent={(agent) => void openAgentChat(agent)}
                onEditAgent={(agent) => setAgentEditor({ open: true, agent })}
              />
            ),
          } : undefined}
          collapsedRoster={agents.length ? <CollapsedRoster agents={agents} onOpenAgent={(agent) => void openAgentChat(agent)} /> : undefined}
          page={mainPage}
          onPage={setMainPage}
          pageCounts={{ groups: rooms.filter((r) => r.kind === "group").length, projects: projects.filter((p) => !p.archived).length }}
          activeProjectId={activeProjectId}
          activeSessionId={activeThreadId}
          activeView="chat"
          onToggleCollapsed={() => setSidebarCollapsed(v => !v)}
          onNewSession={(projectId) => {
            setMainPage("chat");
            void createNewThread(projectId);
          }}
          onAddFolder={() => void attachFolder()}
          onSelectSession={(id) => {
            setMainPage("chat");
            switchThread(id);
          }}
          onSearchChats={searchChats}
          onRenameSession={(id, title) => void renameThread(id, title)}
          onDeleteSession={(id) => void deleteThread(id)}
          onDeleteProject={async (id) => {
            const response = await fetch(`${apiBase}/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
            if (!response.ok) return;
            setThreads(items => items.map(item => item.projectId === id ? { ...item, projectId: "" } : item));
            if (activeProjectId === id) { setActiveProjectId(""); await refreshThreadState(activeThreadId); }
            await refreshProjects();
          }}
          onSettings={() => {
            setLeftTab("settings");
            if (desktopMode && !desktopSettingsWindow) {
              void openDesktopSettingsWindow().catch(() => setDesktopSettingsOpen(true));
            } else {
              setDesktopSettingsOpen(true);
            }
          }}
          settingsOpen={studioOpen}
          onView={() => {
            setLeftTab("chat");
            if (mediaRouteActive) navigate("/app");
          }}
        /> : null}
        {mediaWorkspaceOpen ? (
          <div className="visualizer-pane media-workspace-pane" data-testid="media-workspace-pane">
            <MediaLibraryView
              apiBase={apiBase}
              sessionId={activeThreadId}
              projectId={activeProjectId}
            />
          </div>
        ) : null}
        {desktopMode && studioOpen ? (
          <div
            className="desktop-studio-host"
            ref={setDesktopStudioHost}
            data-testid="desktop-studio-host"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) closeStudio();
            }}
          />
        ) : null}
        {rightOpen ? (
          <RightPanel
            apiBase={apiBase}
            tab={rightTab}
            onTab={setRightTab}
            artifact={openArtifact}
            activity={activityItems}
            width={panelWidth}
            onWidth={setPanelWidth}
            measureRoom={measurePanelRoom}
            overlay={narrowLayout}
            onClose={() => {
              setOpenArtifact(null);
              setActivityOpen(false);
            }}
          />
        ) : null}
        {mainPage !== "chat" ? (
          <PageCloseContext.Provider value={closePage}>
          <div className="glow-panel es-page-host" data-testid="sidebar-page">
            {mainPage === "groups" ? (
              <GroupChatsPage
                agents={agents}
                rooms={rooms}
                activeThreadId={activeThreadId}
                onOpen={openRoom}
                onNew={() => setRoomDialog({ open: true, room: null })}
                onDelete={async (room) => {
                  try {
                    await leanClient.deleteRoom(room.id);
                  } catch (error) {
                    console.warn("Delete room failed", error);
                  }
                  await refreshRoster();
                  await refreshThreads();
                }}
              />
            ) : mainPage === "projects" ? (
              <ProjectsPage
                projects={projects}
                activeProjectId={activeProjectId}
                chatCounts={threads.reduce<Record<string, number>>((acc, t) => {
                  if (t.projectId) acc[t.projectId] = (acc[t.projectId] || 0) + 1;
                  return acc;
                }, {})}
                onAdd={() => void attachFolder()}
                onOpen={(project) => {
                  setMainPage("chat");
                  const recent = threads.find((t) => t.projectId === project.id);
                  if (recent) switchThread(recent.id);
                  else void createNewThread(project.id);
                }}
              />
            ) : mainPage === "artifacts" ? (
              <ArtifactsPage apiBase={apiBase} onOpen={openArtifactFromPage} />
            ) : (
              <RoutinesPage apiBase={apiBase} agents={agents} />
            )}
          </div>
          </PageCloseContext.Provider>
        ) : null}
        <div
          className={`glow-panel${desktopContextualWorkspace ? " desktop-contextual-workspace" : desktopMode ? " desktop-chat-workspace" : ""}`}
          data-page-hidden={mainPage !== "chat" ? "true" : undefined}
        >
          <div className="panel-header">
            <div className="title">
              <img src="/logo.png" alt="" style={{ width: 15, height: 15 }} />
              <span>{activeWorkspaceLabel}</span>
              {activeProjectId && leftTab === "chat" && threadState?.mode === "coding" && (
                <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 6, background: "linear-gradient(135deg, rgba(34,197,94,0.15), rgba(34,197,94,0.05))", border: "1px solid rgba(34,197,94,0.25)", color: "#22c55e", fontWeight: 600, marginLeft: 8 }}>
                  📁 {projects.find(p => p.id === activeProjectId)?.name || "Project Active"}
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <button
                type="button"
                className="icon-button"
                onClick={() => {
                  if (studioOpen) {
                    closeStudio();
                    return;
                  }
                  setLeftTab("overview");
                  if (desktopMode && !desktopSettingsWindow) {
                    void openDesktopSettingsWindow().catch(() => setDesktopSettingsOpen(true));
                  } else if (desktopMode) {
                    setDesktopSettingsOpen(true);
                  }
                }}
                title={studioOpen ? "Close Settings" : "Open Settings"}
                style={{
                  display: "none",
                  height: 32,
                  padding: "0 12px",
                  fontSize: 12,
                  fontWeight: 700,
                  color: "#fff",
                  background: studioOpen ? "rgba(140,180,255,0.16)" : "transparent",
                  border: `1px solid ${studioOpen ? "rgba(140,180,255,0.38)" : colors.line}`,
                }}
              >
                Settings
              </button>
              <div
                className={`switcher-dot ${backendOnline ? "online" : "offline"}`}
                title={backendOnline ? "Connected" : "Disconnected"}
              />
              <button
                type="button"
                className="icon-button"
                onClick={() => setSpeechEnabled(!speechEnabled)}
                title={speechEnabled ? "Mute Speech" : "Unmute Speech"}
                style={{
                  display: "none",
                  color: "#fff",
                  background: speechEnabled ? "#222" : "transparent",
                  border: `1px solid ${colors.line}`,
                }}
              >
                {speechEnabled ? (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><line x1="23" y1="9" x2="17" y2="15"></line><line x1="17" y1="9" x2="23" y2="15"></line></svg>
                )}
              </button>

            </div>
          </div>
          <div className="panel-body">
            <div className="research-panel">
              <div className="tab-bar" style={{
                display: "none",
                position: "relative",
                overflow: "visible",
                marginBottom: "16px",
              }}>
                <div className="top-tab-groups" style={{
                  alignItems: "center",
                  padding: 0,
                  background: "transparent",
                  borderRadius: 0,
                  border: "none",
                  boxShadow: "none",
                  backdropFilter: "none",
                  WebkitBackdropFilter: "none",
                  overflowY: "hidden",
                  scrollbarWidth: "none",
                }}>
                  {[
                    { id: 'core', label: 'Core', icon: '⚡', tabs: [{ id: 'chat', label: 'Chat' }, { id: 'research', label: 'Research' }] },
                    { id: 'knowledge', label: 'Knowledge', icon: '📚', tabs: [{ id: 'memory', label: 'Memory' }, { id: 'docs', label: 'Docs' }] },
                    { id: 'config', label: 'Config', icon: '⚙️', tabs: [{ id: 'settings', label: 'Settings' }, { id: 'capabilities', label: 'Tools' }, { id: 'soul', label: 'Soul' }, { id: 'avatar_editor', label: 'Avatar' }] },
                    { id: 'operations', label: 'Operations', icon: '🤖', tabs: [{ id: 'overview', label: 'Overview' }, { id: 'skills', label: 'Skills' }, { id: 'executions', label: 'Viewer' }, { id: 'approvals', label: 'Approvals' }, { id: 'projects', label: 'Projects' }, { id: 'automations', label: 'Automations' }, { id: 'connections', label: 'Connections' }, { id: 'services', label: 'Services' }] },
                  ].map((group) => {
                    const isGroupActive = group.tabs.some(t => t.id === leftTab);
                    return (
                      <div key={group.id} className="top-tab-group">
                        <button
                          type="button"
                          className={`tab-button ${isGroupActive ? "active" : ""}`}
                          ref={(el) => {
                            if (activeGroup === group.id) activeGroupButtonRef.current = el;
                          }}
                          onClick={(e) => {
                            if (group.tabs.length === 1) {
                              setLeftTab(group.tabs[0].id as any);
                              setActiveGroup(null);
                            } else {
                              activeGroupButtonRef.current = e.currentTarget as HTMLButtonElement;
                              setActiveGroup(activeGroup === group.id ? null : group.id);
                            }
                          }}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                            padding: "8px 16px",
                            borderRadius: "12px",
                            fontSize: "13px",
                            fontWeight: 600,
                            background: isGroupActive ? "linear-gradient(135deg, rgba(255,255,255,0.15) 0%, rgba(255,255,255,0.05) 100%)" : "transparent",
                            border: isGroupActive ? "1px solid rgba(255,255,255,0.2)" : "1px solid transparent",
                            boxShadow: isGroupActive ? "inset 0 1px 1px rgba(255,255,255,0.3), 0 2px 8px rgba(0,0,0,0.2)" : "none",
                            color: isGroupActive ? "#ffffff" : "rgba(255,255,255,0.6)",
                            transition: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
                            cursor: "pointer",
                            whiteSpace: "nowrap"
                          }}
                        >
                          <span style={{ fontSize: "16px", filter: "brightness(0) invert(1)", opacity: isGroupActive ? 1 : 0.7 }}>{group.icon}</span>
                          <span style={{ textShadow: isGroupActive ? "0 0 8px rgba(255,255,255,0.4)" : "none" }}>{group.label}</span>
                          {group.tabs.length > 1 && (
                            <span style={{ fontSize: "10px", opacity: 0.5, marginLeft: 4 }}>{activeGroup === group.id ? '▲' : '▼'}</span>
                          )}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>

              {activeGroup && activeGroupPos
                ? createPortal(
                  <AnimatePresence>
                    <motion.div
                      ref={(el) => {
                        activeGroupMenuRef.current = el;
                      }}
                      initial={{ opacity: 0, y: 8, scale: 0.95 }}
                      animate={{ opacity: 1, y: 0, scale: 1 }}
                      exit={{ opacity: 0, y: 4, scale: 0.95 }}
                      transition={{ duration: 0.15 }}
                      style={{
                        position: "fixed",
                        top: activeGroupPos.top,
                        left: activeGroupPos.left,
                        zIndex: 2147483647,
                        display: "flex",
                        flexDirection: "column",
                        gap: 2,
                        padding: "6px",
                        background: "rgba(20, 20, 20, 0.95)",
                        backdropFilter: "blur(16px)",
                        WebkitBackdropFilter: "blur(16px)",
                        borderRadius: "12px",
                        border: `1px solid ${colors.line}`,
                        boxShadow:
                          "0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5)",
                        minWidth: "140px",
                      }}
                    >
                      {(
                        [
                          { id: 'core', label: 'Core', icon: '⚡', tabs: [{ id: 'chat', label: 'Chat' }, { id: 'research', label: 'Research' }] },
                          { id: 'knowledge', label: 'Knowledge', icon: '📚', tabs: [{ id: 'memory', label: 'Memory' }, { id: 'docs', label: 'Docs' }] },
                          { id: 'config', label: 'Config', icon: '⚙️', tabs: [{ id: 'settings', label: 'Settings' }, { id: 'capabilities', label: 'Tools' }, { id: 'soul', label: 'Soul' }, { id: 'avatar_editor', label: 'Avatar' }] },
                          { id: 'operations', label: 'Operations', icon: '🤖', tabs: [{ id: 'overview', label: 'Overview' }, { id: 'skills', label: 'Skills' }, { id: 'executions', label: 'Viewer' }, { id: 'approvals', label: 'Approvals' }, { id: 'projects', label: 'Projects' }, { id: 'automations', label: 'Automations' }, { id: 'connections', label: 'Connections' }, { id: 'services', label: 'Services' }] },
                        ].find((g) => g.id === activeGroup)?.tabs || []
                      ).map((tab) => (
                        <button
                          key={tab.id}
                          type="button"
                          className={`tab-button ${leftTab === tab.id ? "active" : ""}`}
                          onClick={() => {
                            setLeftTab(tab.id as any);
                            setActiveGroup(null);
                          }}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            padding: "8px 12px",
                            borderRadius: "8px",
                            fontSize: "12px",
                            fontWeight: 500,
                            textAlign: "left",
                            background: leftTab === tab.id ? "rgba(255,255,255,0.1)" : "transparent",
                            color: leftTab === tab.id ? colors.text : colors.textDim,
                            border: "none",
                            cursor: "pointer",
                            transition: "all 0.15s ease",
                            width: "100%",
                          }}
                        >
                          {tab.label}
                        </button>
                      ))}
                    </motion.div>
                  </AnimatePresence>,
                  document.body
                )
                : null}

              {/* Chat Tab */}
              {true && (
                <>
                  <WidgetEnvProvider value={widgetEnv}>
                  {!rightOpen && activityItems.length ? (
                    <button
                      type="button"
                      className="rp-toggle"
                      onClick={() => {
                        setActivityOpen(true);
                        setRightTab("activity");
                      }}
                      title="Show what the agents did: commands, files, searches"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M4 12h3l2-6 4 12 2-6h5" /></svg>
                      Activity
                      <small>{activityItems.length}</small>
                    </button>
                  ) : null}
                  <div key={activeThreadId || "quick-chat"} className="chat-scroll" data-live={streaming ? "true" : undefined} style={{ flex: 1 }} ref={chatScrollRef} onScroll={onChatScroll} onWheel={onChatWheel} onKeyDown={onChatKeyDown} onTouchStart={onChatTouchStart} onTouchEnd={onChatTouchEnd} onTouchCancel={onChatTouchEnd}>
                    {activeRoom ? (
                      <RoomHeader room={activeRoom} agents={agents} onEdit={() => setRoomDialog({ open: true, room: activeRoom })} />
                    ) : null}
                    {!timeline.length && !streaming && !lean.live ? (
                      <div className="es-chat-empty">
                        <strong>{activeRoom ? activeRoom.name : "What can I help with?"}</strong>
                        <span>
                          {activeRoom?.kind === "group"
                            ? "Write to the whole group, or @mention an agent to pick who answers."
                            : "Ask anything, or drop a folder on the composer to work inside a project."}
                        </span>
                      </div>
                    ) : null}
                    <AnimatePresence initial={false}>
                      {timeline.map((t) =>
                        t.kind === "message" ? (
                          <ChatBubble
                            key={`msg-${t.id}`}
                            msg={t.msg}
                            streaming={streaming}
                            typewriter={t.msg.role === "assistant" && !t.msg.skipTypewriter}
                            contextWindow={Number(providerInfo?.context_window || 0) || 32768}
                            providerLabel={providerInfo?.provider}
                            modelLabel={providerInfo?.model}
                            onQuickReply={(text) => {
                              try {
                                stopTts();
                              } catch {
                                // ignore
                              }
                              sendText(text);
                            }}
                          />
                        ) : (
                          <ActivityCard
                            key={`act-${t.id}`}
                            item={t.item}
                            // Step list uses static marks when the hero strip owns the Echo spinner.
                            primarySpinner={!streaming}
                          />
                        )
                      )}
                    </AnimatePresence>
                    {pendingApproval?.has_pending && pendingApproval.action ? (
                      <div
                        style={{ width: "100%", padding: "2px 4px 4px", position: "relative", zIndex: 20 }}
                        data-testid="chat-pending-approval"
                        data-approval-id={String(pendingApproval.approval_id || pendingApproval.action.id || "")}
                      >
                        <OperationalStateCard
                          state={threadState}
                          approval={{
                            ...pendingApproval.action,
                            id: String(pendingApproval.approval_id || pendingApproval.action.id || ""),
                            status: String(pendingApproval.action.status || "pending"),
                            policy_flags: pendingApproval.policy_flags || pendingApproval.action.policy_flags,
                            session_permissions: pendingApproval.session_permissions || pendingApproval.action.session_permissions,
                          }}
                          busy={approvalDecisionBusy}
                          onDecision={decideApproval}
                          compact
                        />
                      </div>
                    ) : null}
                    {lean.live ? (
                      <div data-testid="lean-live-turn">
                        {lean.live.routing && !lean.live.order.length ? (
                          <div className="lm-routing" aria-hidden>
                            <span className="lm-dots"><i /><i /><i /></span>
                          </div>
                        ) : null}
                        {lean.live.order.map((id) => {
                          const item = lean.live!.messages[id];
                          return item ? <LeanMessage key={id} data={item} live onDecide={decideLeanApproval} at={item.startedAt} /> : null;
                        })}
                        {!lean.live.order.length && !lean.live.routing ? (
                          <div className="lm-routing" aria-hidden>
                            <span className="lm-dots"><i /><i /><i /></span>
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                  </WidgetEnvProvider>
                  <div className="input-bar">
                    <LiveStatusPill live={streaming ? lean.live : null} onStop={stopActiveTurn} />
                    {/* Row 1: session strip stacked on input (same column width) + context + send */}
                    <div className="input-row">
                      <div className="composer-input-stack">
                        <div
                          className={"session-folder-strip" + (folderDropActive ? " is-drop-active" : "")}
                          aria-label="Session and Project folder attachment. Drop a local folder here to create or select its Project."
                          onDragEnter={(event) => { event.preventDefault(); setFolderDropActive(true); }}
                          onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "link"; setFolderDropActive(true); }}
                          onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setFolderDropActive(false); }}
                          onDrop={(event) => { event.preventDefault(); setFolderDropActive(false); const path = folderPathFromDrop(event); if (path) void attachFolder(path); else void attachFolder(); }}
                        >
                          <span style={{ whiteSpace: "nowrap" }}>
                            Session: <b style={{ color: "rgba(255,255,255,.8)" }}>{threads.find(t => t.id === activeThreadId)?.name || activeThreadId}</b>
                          </span>
                          {(() => {
                            const folderFull =
                              String(threadState?.workspace_root || threadState?.project_path || "").trim();
                            const folderName = folderFull
                              ? folderFull.replace(/[\\/]+$/, "").split(/[/\\]/).filter(Boolean).pop() || folderFull
                              : "";
                            const gitBranch = projects.find(project => project.id === activeProjectId)?.git_metadata?.is_repository
                              ? String(projects.find(project => project.id === activeProjectId)?.git_metadata?.branch || "repository")
                              : "";
                            return (
                              <button
                                type="button"
                                onClick={() => void attachFolder()}
                                title={
                                  folderFull
                                    ? folderFull
                                    : "Choose or drop a local folder; folders become Projects automatically"
                                }
                                style={{
                                  border: 0,
                                  background: "transparent",
                                  color: "inherit",
                                  padding: 0,
                                  font: "inherit",
                                  cursor: "pointer",
                                  textAlign: "left",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                Folder:{" "}
                                <b style={{ color: "rgba(255,255,255,.8)" }}>
                                  {folderName || "drop folder to start Project"}
                                  {gitBranch ? ` · git:${gitBranch}` : ""}
                                </b>
                              </button>
                            );
                          })()}
                          {(threadState?.workspace_root || threadState?.project_path) && (
                            <button
                              type="button"
                              aria-label="Remove folder from this Session"
                              title="Remove folder from this Session"
                              onClick={async () => {
                                const response = await fetch(`${apiBase}/projects/deactivate?thread_id=${encodeURIComponent(activeThreadId)}`, { method: "POST" });
                                if (!response.ok) return;
                                const data = await response.json();
                                setActiveProjectId(""); setThreadState(data.thread_state || null);
                                setThreads(items => items.map(item => item.id === activeThreadId ? { ...item, projectId: "" } : item));
                              }}
                              style={{ width: 18, height: 18, border: 0, background: "transparent", color: "rgba(255,255,255,.65)", borderRadius: 2, cursor: "pointer", lineHeight: 1, flexShrink: 0 }}
                            >
                              ×
                            </button>
                          )}
                          {providerError ? (
                            <span role="status" title={providerError} style={{ marginLeft: 8, color: "#e8b86a", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>
                              {providerError}
                            </span>
                          ) : null}
                        </div>
                        <textarea
                          ref={textareaRef}
                          className="input-field"
                          value={input}
                          rows={1}
                          disabled={!activeThreadId}
                          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => {
                            updateComposerInput(e.target.value);
                            if (activeRoom?.kind === "group") {
                              const found = activeMention(e.target.value, e.target.selectionStart ?? e.target.value.length);
                              setMention(found && mentionMatches(found.query, roomMembers).length ? { ...found, index: 0 } : null);
                            }
                          }}
                          onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
                            if (mention) {
                              const matches = mentionMatches(mention.query, roomMembers);
                              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                                e.preventDefault();
                                const step = e.key === "ArrowDown" ? 1 : -1;
                                setMention({ ...mention, index: (mention.index + step + matches.length) % Math.max(1, matches.length) });
                                return;
                              }
                              if ((e.key === "Enter" || e.key === "Tab") && matches[mention.index]) {
                                e.preventDefault();
                                const pick = matches[mention.index];
                                const caret = e.currentTarget.selectionStart ?? input.length;
                                const next = `${input.slice(0, mention.start)}@${pick.name} ${input.slice(caret)}`;
                                updateComposerInput(next);
                                setMention(null);
                                return;
                              }
                              if (e.key === "Escape") {
                                setMention(null);
                                return;
                              }
                            }
                            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                              e.preventDefault();
                              void sendText();
                            }
                          }}
                          onBlur={() => window.setTimeout(() => setMention(null), 120)}
                          placeholder={
                            !activeThreadId
                              ? "Create a Session with + to chat"
                              : activeRoom?.kind === "group"
                              ? `Message ${activeRoom.name}  ·  @ to pick who answers`
                              : activeRoom
                              ? `Message ${roomMembers[0]?.name || activeRoom.name}`
                              : "Ask Echo anything..."
                          }
                          aria-label="Message"
                        />
                        {mention && activeRoom?.kind === "group" ? (
                          <MentionMenu
                            anchor={textareaRef.current}
                            query={mention.query}
                            agents={roomMembers}
                            activeIndex={mention.index}
                            onPick={(pick) => {
                              const caret = textareaRef.current?.selectionStart ?? input.length;
                              updateComposerInput(`${input.slice(0, mention.start)}@${pick.name} ${input.slice(caret)}`);
                              setMention(null);
                              textareaRef.current?.focus();
                            }}
                          />
                        ) : null}
                      </div>
                      <div className="composer-trailing">
                        <ContextMeter messages={messages} contextWindow={providerInfo?.context_window || 0} />
                        <button
                          className="send-button"
                          onClick={() => void sendText()}
                          type="button"
                          disabled={!activeThreadId || !input.trim()}
                          title="Send"
                          aria-label="Send message"
                        >
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                            <path d="M5 12L19 12M19 12L13 6M19 12L13 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </button>
                      </div>
                    </div>
                    {/* Row 2: mic mon viz | Provider | Model */}
                    <div className="controls-row" ref={toolbarRef} data-size={toolbarSize}>
                      <div className="composer-primary-controls">
                        <div className="composer-tools-slot" role="group" aria-label="Input tools">
                        <button
                          className={`mic-button ${listening ? "active" : ""}`}
                          type="button"
                          title={listening ? "Stop microphone" : "Start microphone"}
                          aria-label={listening ? "Stop microphone" : "Start microphone"}
                          disabled={voicePhase === "transcribing" || voicePhase === "requesting_permission"}
                          onClick={() => listening ? void stop() : void start()}
                        >
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" fill="currentColor" />
                            <path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                        </button>
                        {(voicePhase !== "idle" || voiceNotice) ? (
                          <span
                            className="voice-transport-status"
                            data-state={voicePhase}
                            title={voiceNotice || voicePhase}
                            aria-live="polite"
                          >
                            <i style={{ transform: `scale(${1 + voiceInputLevel * 0.55})` }} />
                            {voicePhase === "requesting_permission"
                              ? "Mic access"
                              : voicePhase === "listening"
                              ? "Listening"
                              : voicePhase === "transcribing"
                              ? "Local transcript"
                              : voicePhase === "speaking"
                              ? "Speaking"
                              : voicePhase === "error"
                              ? "Voice setup"
                              : voiceNotice || "Voice ready"}
                          </span>
                        ) : null}
                        <button
                          className={`composer-square is-overflowable ${monitoring ? "active" : ""}`}
                          type="button"
                          title={monitoring ? "Stop screen monitor" : "Screen monitor"}
                          aria-label={monitoring ? "Stop screen monitor" : "Screen monitor"}
                          onClick={toggleMonitor}
                        >
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                            <rect x="2" y="4" width="20" height="12" rx="2" stroke="currentColor" strokeWidth="2" />
                            <path d="M12 16v4M8 20h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                        </button>
                        <button
                          className={`composer-square is-overflowable ${!speechEnabled ? "active" : ""}`}
                          type="button"
                          title={speechEnabled ? "Sound on · click to mute" : "Sound off · click to unmute"}
                          aria-label={speechEnabled ? "Mute sound" : "Unmute sound"}
                          onClick={() => setSpeechEnabled(!speechEnabled)}
                        >
                          {speechEnabled ? (
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                              <path d="M4.5 10.5h3l4-3.5v10l-4-3.5h-3z" />
                              <path d="M15.5 9.5a4 4 0 0 1 0 5" />
                              <path d="M17.5 7.5a7 7 0 0 1 0 9" />
                            </svg>
                          ) : (
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                              <path d="M4.5 10.5h3l4-3.5v10l-4-3.5h-3z" />
                              <path d="M16 9.5 20 14.5M20 9.5 16 14.5" />
                            </svg>
                          )}
                        </button>
                        </div>
                        <div className="control-slot provider-slot" data-label="Provider">
                        <div className="inline-switcher">
                          <select
                            className="provider-picker"
                            value={providerDraft.provider}
                            onChange={(e) => {
                              const p = e.target.value;
                              setProviderModels([]);
                              setProviderDraft((d) => ({
                                ...d,
                                provider: p,
                                base_url: "",
                                model: p === "openai" ? openaiModelOptions[0] : p === "gemini" ? geminiModelOptions[0] : "",
                              }));
                            }}
                            disabled={switchingProvider || lmStudioOnly}
                            title="Model provider"
                            aria-label="Model provider"
                          >
                            {(providerInfo?.available_providers || fallbackProviders)
                              .filter((p) => !lmStudioOnly || p.id === "lmstudio")
                              .map((p) => (
                                <option key={p.id} value={p.id}>
                                  {p.name}
                                </option>
                              ))}
                          </select>
                        </div>
                        </div>
                        <div className="control-slot model-slot" data-label="Model">
                        <select
                          className="model-picker"
                          value={modelPickerValue}
                          onChange={(e) => {
                            if (!showModelPicker) return;
                            setProviderDraft((d) => ({ ...d, model: e.target.value }));
                          }}
                          disabled={switchingProvider || !showModelPicker}
                          title="Model"
                          aria-label="Model"
                        >
                          {modelPickerOptions.map((m) => (
                            <option key={m} value={m}>
                              {m}
                            </option>
                          ))}
                        </select>
                        </div>
                        <div className="control-slot effort-slot is-overflowable" data-label="Effort">
                        <select
                          className="model-picker"
                          value={reasoningEffort}
                          onChange={(e: any) => setReasoningEffort(e.target.value)}
                          title="Reasoning effort"
                          aria-label="Reasoning effort"
                        >
                          <option value="minimal">Minimal</option>
                          <option value="low">Low</option>
                          <option value="medium">Medium</option>
                          <option value="high">High</option>
                          <option value="extra_high">Extra High</option>
                          <option value="max">Max</option>
                          <option value="ultra">Ultra</option>
                        </select>
                      </div>
                      </div>
                      <div className="composer-mode-controls" role="group" aria-label="Thinking and voice controls">
                      <button
                        className={`composer-mode-button ${thinkingEnabled ? "active" : ""}`}
                        type="button"
                        title={thinkingEnabled ? "Thinking: on" : "Thinking: off"}
                        aria-label="Thinking"
                        aria-pressed={thinkingEnabled}
                        onClick={() => setThinkingEnabled(!thinkingEnabled)}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="3"/></svg>
                        <span className="composer-mode-label">Think</span>
                      </button>
                      <button
                        className={`composer-mode-button is-overflowable ${voiceReadAloud ? "active" : ""}`}
                        type="button"
                        title={voiceReadAloud ? "Read replies aloud: on" : "Read replies aloud: off"}
                        aria-label="Read replies aloud"
                        aria-pressed={voiceReadAloud}
                        onClick={toggleReadAloud}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M4 10h3l4-3v10l-4-3H4zM15 9a4 4 0 0 1 0 6M18 6a8 8 0 0 1 0 12"/></svg>
                        <span className="composer-mode-label">Read</span>
                      </button>
                      <button
                        className={`composer-mode-button is-overflowable ${voiceConversationMode ? "active" : ""}`}
                        type="button"
                        title={voiceConversationMode ? "Voice conversation: on" : "Voice conversation: off"}
                        aria-label="Voice conversation mode"
                        aria-pressed={voiceConversationMode}
                        onClick={toggleVoiceMode}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M5 10v4M9 7v10M13 4v16M17 7v10M21 10v4"/></svg>
                        <span className="composer-mode-label">Voice</span>
                      </button>
                      <button
                        className={`composer-mode-button is-overflowable ${wakeWordEnabled ? "active" : ""}`}
                        type="button"
                        title={wakeWordEnabled ? "Wake word: on (say “Hey Echo”)" : "Wake word: off"}
                        aria-label="Wake word"
                        aria-pressed={wakeWordEnabled}
                        onClick={toggleWakeWord}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><circle cx="12" cy="12" r="3"/><path d="M12 2a10 10 0 0 1 10 10M12 22A10 10 0 0 1 2 12M5 5a10 10 0 0 1 14 14"/></svg>
                        <span className="composer-mode-label">Wake</span>
                      </button>
                      {toolbarSize === "mini" ? (
                        <div className="toolbar-overflow">
                          <button
                            type="button"
                            className={`composer-mode-button${toolbarMenuOpen ? " active" : ""}`}
                            title="More controls"
                            aria-label="More controls"
                            aria-haspopup="menu"
                            aria-expanded={toolbarMenuOpen}
                            onClick={() => setToolbarMenuOpen((v) => !v)}
                          >
                            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden><circle cx="5" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="19" cy="12" r="1.7" /></svg>
                          </button>
                          {toolbarMenuOpen ? (
                            <div className="toolbar-menu" role="menu" aria-label="More controls">
                              <button type="button" role="menuitemcheckbox" aria-checked={voiceReadAloud} onClick={toggleReadAloud}>
                                <span>Read replies aloud</span><i data-on={voiceReadAloud ? "true" : "false"} />
                              </button>
                              <button type="button" role="menuitemcheckbox" aria-checked={voiceConversationMode} onClick={toggleVoiceMode}>
                                <span>Voice conversation</span><i data-on={voiceConversationMode ? "true" : "false"} />
                              </button>
                              <button type="button" role="menuitemcheckbox" aria-checked={monitoring} onClick={toggleMonitor}>
                                <span>Screen monitor</span><i data-on={monitoring ? "true" : "false"} />
                              </button>
                              <button type="button" role="menuitemcheckbox" aria-checked={speechEnabled} onClick={() => setSpeechEnabled(!speechEnabled)}>
                                <span>Sound</span><i data-on={speechEnabled ? "true" : "false"} />
                              </button>
                              <button type="button" role="menuitemcheckbox" aria-checked={wakeWordEnabled} onClick={toggleWakeWord}>
                                <span>Wake word</span><i data-on={wakeWordEnabled ? "true" : "false"} />
                              </button>
                              <label className="toolbar-menu-select">
                                <span>Effort</span>
                                <select value={reasoningEffort} onChange={(e: any) => setReasoningEffort(e.target.value)} aria-label="Reasoning effort">
                                  <option value="minimal">Minimal</option>
                                  <option value="low">Low</option>
                                  <option value="medium">Medium</option>
                                  <option value="high">High</option>
                                  <option value="extra_high">Extra High</option>
                                  <option value="max">Max</option>
                                  <option value="ultra">Ultra</option>
                                </select>
                              </label>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                      </div>
                    </div>
                  </div>
                </>
              )}

              {studioOpen && (!desktopMode || desktopStudioHost) && createPortal(
                <SettingsPanel
                  apiBase={apiBase}
                  fullscreen={Boolean(desktopMode && desktopSettingsWindow)}
                  agents={agents}
                  onClose={closeStudio}
                  onEditAgent={(agent) => setAgentEditor({ open: true, agent })}
                  sessionId={activeThreadId}
                  projectId={activeProjectId}
                />,
                desktopMode ? desktopStudioHost! : document.body
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
