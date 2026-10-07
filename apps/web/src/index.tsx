import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import { ProjectSidebar, type SidebarPage } from "./components/ProjectSidebar";
import { MediaLibraryView } from "./features/media/MediaLibraryView.tsx";
import { loadRuntimeLayout, runtimeGridColumns, saveRuntimeLayout, SIDEBAR_WIDTH } from "./runtimeLayout";
import {
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
import learningCss from "./lean/learning.css?inline";
import { LearningPage } from "./lean/LearningPage";
import settingsCss from "./settings/settings.css?inline";
import chatPolishCss from "./dashboard/chatPolish.css?inline";
import { CreationsPage } from "./creations/CreationsPage";
import { FirstRunSetup } from "./setup/FirstRunSetup";
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
import { liveAudioPlayback, type LiveAudioPacket } from "./liveVoiceTransport";
import { ArtifactsPage, GroupChatsPage, PageCloseContext, ProjectsPage, RoutinesPage, type ArtifactSummary } from "./lean/Pages";
import { AgentEditor, RoomDialog } from "./lean/Dialogs";
import type { LeanEvent, LeanPersona, LeanRoom } from "./lean/types";
import {
  desktopExecutionProfile,
  type DesktopWorkspaceSurface,
} from "./desktop/workspaceState";
import { isStreamThreadCurrent } from "./agentActivity";
import { type ActivityItem, type AgentStreamEvent, type ApprovalDecisionEnvelope, type Message, type PendingActionEnvelope, type Role, type ThreadSessionState, type TimelineItem, type VisionAnalyzeResponse } from "./app/types";
import { buildMessageUsage } from "./app/toolDisplay";
import { colors, fetchWithTimeout, isEmptySessionDraft, normalizeTimestampMs, stopTts, useAppStore } from "./app/runtime";
import { ComposerToolbar, type ReasoningEffort } from "./dashboard/ComposerToolbar";
import { globalCss } from "./app/globalCss";
import { useProviderSettings } from "./dashboard/useProviderSettings";
import { useVoice } from "./dashboard/useVoice";
import { projectSessionHistory } from "./dashboard/historyProjection";
import { ComposerInput } from "./dashboard/ComposerInput";
import { ChatThread } from "./dashboard/ChatThread";
import { recoverableQuery } from "./dashboard/recoverableStream";
import { VoiceStage } from "./dashboard/VoiceStage";

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
  const [messageActionBusy, setMessageActionBusy] = useState(false);
  const [readingId, setReadingId] = useState("");
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const updateComposerInput = useCallback((value: string) => {
    setInput(value);
  }, []);
  const replaceResearchRuns = useResearchStore((state) => state.replaceRuns);
  const clearResearchRuns = useResearchStore((state) => state.clearRuns);
  const [leftTab, setLeftTab] = useState<DashboardTab>(
    desktopSettingsWindow ? "settings" : initialView
  );

  const [showSidebar, setShowSidebar] = useState<boolean>(() => loadRuntimeLayout(typeof window !== "undefined" ? window.localStorage : null).sidebarVisible);
  /** Which page the main area shows: the chat, or one of the sidebar nav pages. */
  const [mainPage, setMainPage] = useState<SidebarPage>("chat");
  /** The artifact shown in the side panel. */
  const [openArtifact, setOpenArtifact] = useState<{ id: string; version?: number } | null>(null);
  /** Shared right side panel: selected tab, open state and remembered width. */
  const [rightTab, setRightTab] = useState<RightTab>("artifact");
  const [activityOpen, setActivityOpen] = useState(false);
  // The research & activity button stays out of the way until the pointer nears the chat's top-right corner.
  const [rpPeek, setRpPeek] = useState(false);
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
  const lean = useLeanLive(activeThreadId);
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
    // Explicit supersession/deletion detaches local ownership. The user
    // Stop control keeps the exact stream open so the durable cancellation and
    // final "Stopped" state can arrive from the backend.
    if (!preserveStream) {
      streamControllersRef.current.get(sessionId)?.abort();
      streamControllersRef.current.delete(sessionId);
      lean.finish(sessionId);
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
  }, [apiBase, setSessionInFlight, lean.finish]);

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
    // Select this Session’s live turn without stopping or clearing other turns.
    lean.select(id);
    setMention(null);
    setStreaming(streamControllersRef.current.has(id));
    // Restore the cached projection immediately; idle Sessions also refresh history.
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
    refreshProviderInfo, showModelPicker, modelPickerOptions, modelPickerValue, modelsLoading,
  } = useProviderSettings({ apiBase, activeThreadIdRef, cancelSessionTurn });
  const [thinkingEnabled, setThinkingEnabled] = useState<boolean>(
    () => window.localStorage.getItem("echospeak.chat.thinking_enabled") !== "false",
  );
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>(() => {
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

    pin();
    if (pinBottomRafRef.current) cancelAnimationFrame(pinBottomRafRef.current);
    // Two more frames: after React paint, then after layout (markdown / framer-motion / embeds).
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
    const restore = () => {
      if (saved?.atBottom || !saved) el.scrollTop = el.scrollHeight;
      else el.scrollTop = Math.min(saved.top, Math.max(0, el.scrollHeight - el.clientHeight));
      followerRef.current.reset(saved?.atBottom ?? true, el);
    };
    restore();
    requestAnimationFrame(restore);
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


  const sendText = async (overrideText?: string, voiceTranscript?: VoiceTranscript, recovery?: { id: string; session: string }) => {
    const raw = overrideText ?? input;
    if (!raw.trim() && !recovery) return;
    // Session creation has one explicit owner: the + controls in the sidebar.
    // Composer submission, navigation, hydration, and assistant replies never
    // invent a Session.
    const streamThreadId = String(recovery?.session || activeThreadIdRef.current || activeThreadId || "").trim();
    if (!streamThreadId) return;
    const appendTurnMessage = (message: Message) => {
      const visible = activeThreadIdRef.current === streamThreadId;
      const cached = sessionProjectionRef.current.get(streamThreadId);
      const current = visible ? useAppStore.getState().messages : cached?.messages || [];
      const next = current.some(item => item.id === message.id)
        ? current.map(item => item.id === message.id ? message : item) : [...current, message];
      sessionProjectionRef.current.set(streamThreadId, { messages: next, activities: cached?.activities || [] });
      if (visible) useAppStore.setState({ messages: next });
    };
    const runRequestId = recovery?.id || crypto.randomUUID();
    const streamProjectId = String(activeProjectIdRef.current || activeProjectId || "");
    if (!recovery) cancelSessionTurn(streamThreadId);
    const streamController = new AbortController();
    streamControllersRef.current.set(streamThreadId, streamController);
    activeRequestIdsRef.current.set(streamThreadId, runRequestId);
    setSessionInFlight(streamThreadId, true);

    if (!recovery) {
      // Sending a message (or resending after a failure) jumps to the newest message and follows the reply.
      followerRef.current.reset(true, chatScrollRef.current || undefined);
      scrollChatToBottom(true);
    }
    if (!recovery && !overrideText) setInput("");

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
      clientRequestId: runRequestId,
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
    if (!recovery) { appendTurnMessage(userMsg); setInput(""); }
    setStreaming(true);
    lean.start(runRequestId, streamThreadId);
    setMention(null);
    /** Backend Execution id once the run starts. */
    let durableTurnId = "";
    let finalHandled = false;
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
      const resp = await recoverableQuery(apiBase, {
          message: requestText,
          include_memory: true,
          thread_id: streamThreadId,
          client_request_id: runRequestId,
          thinking_enabled: providerInfo?.model_profile?.thinking_controls?.supported
            ? (providerInfo.model_profile.thinking_controls.toggle ? thinkingEnabled : true) : false,
          reasoning_effort: reasoningEffort,
          transport: voiceTranscript ? "voice" : "chat",
          voice_turn_id: voiceTranscript?.voiceTurnId,
        }, streamController.signal, Boolean(recovery));
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
      let nativeAudioPlayed = false;

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
          if (!ownsStreamCleanup(streamControllersRef.current.get(streamThreadId), streamController)) continue;
          const visible = isStreamThreadCurrent(streamThreadId, activeThreadIdRef.current);
          const evtSeq = Number((evt as { seq?: number }).seq || 0);
          if (evtSeq > 0) {
            if (evtSeq <= maxStreamSeq) {
              // Stale or duplicated frame after reconnect — do not apply.
              continue;
            }
            maxStreamSeq = evtSeq;
          }
          // Lean runtime events render through the agent timeline, not the legacy cards.
          if ((evt as any).type === "voice_audio") {
            if (visible && useAppStore.getState().speechEnabled && (voiceReadAloud || voiceConversationMode || voiceTranscript?.providerId === "gemini-live")) {
              try {
                const played = await liveAudioPlayback.enqueue(evt as unknown as LiveAudioPacket, speaking => {
                  useAppStore.getState().setSpeaking(speaking);
                  setVoicePhase(speaking ? "speaking" : "idle");
                });
                nativeAudioPlayed = nativeAudioPlayed || played;
              } catch (error) { setVoiceNotice(error instanceof Error ? error.message : "Live audio playback failed."); }
            }
            continue;
          }
          if (isLeanEvent(evt as unknown as LeanEvent)) {
            const leanEvt = evt as unknown as LeanEvent;
            if (leanEvt.type === "run_start") {
              const execId = String(leanEvt.execution_id || "");
              if (execId) {
                durableTurnId = execId;
                activeExecutionIdsRef.current.set(streamThreadId, execId);
                if (!recovery) appendTurnMessage({ ...userMsg, executionId: execId });
              }
              // The legacy bootstrap "thinking…" card is not part of a lean turn.
              if (visible) setActivities((prev) => prev.filter((a) => !(a.kind === "thinking" && a.request_id === runRequestId)));
            }
            if (leanEvt.type === "memory_saved" && typeof leanEvt.memory_count === "number") {
              continue;
            }
            if (leanEvt.type !== "final") {
              lean.push(leanEvt, streamThreadId);
              continue;
            }
            // Final: commit exactly what streamed, one message per agent.
            if (finalHandled) continue;
            finalHandled = true;
            const done = lean.finish(streamThreadId);
            const finalExecId = String(leanEvt.execution_id || durableTurnId || "");
            const committed = done ? done.order.map((id) => done.messages[id]).filter(Boolean) : [];
            const ctxWindowLean = Number(providerInfo?.context_window || 0) || 32768;
            for (const item of committed) {
              const text = item.text || item.segments.filter((s) => s.kind === "text").map((s) => (s as { text: string }).text).join("\n\n").trim();
              appendTurnMessage({
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
              appendTurnMessage({ id: crypto.randomUUID(), role: "assistant", text: String(leanEvt.response), at: Date.now(), skipTypewriter: true });
            }
            if (visible && leanEvt.thread_state && String(activeProjectIdRef.current || "") === streamProjectId) {
              setThreadState(leanEvt.thread_state);
              setActiveProjectId(String(leanEvt.thread_state.active_project_id || ""));
            }
            if (visible) setStreaming(false);
            const spokenLean = String(committed[committed.length - 1]?.text || leanEvt.response || "").trim();
            if (visible && nativeAudioPlayed) {
              if (nativeLiveVoice && voiceConversationMode && activeThreadIdRef.current === streamThreadId) {
                // Listen for the next utterance while queued speech plays; actual speech stops playback.
                void start(true);
              } else {
                void liveAudioPlayback.finished().then(played => {
                  if (played && voiceConversationMode && activeThreadIdRef.current === streamThreadId && !streamControllersRef.current.has(streamThreadId)) void start();
                });
              }
            } else if (visible && !(evt as any)._recovered && spokenLean && (voiceReadAloud || voiceConversationMode)) {
              void speakLocalText(spokenLean, {
                clientTurnId: voiceTranscript?.clientTurnId || runRequestId,
                requestId: runRequestId,
                executionId: finalExecId,
              }).then((played) => {
                if (played && voiceConversationMode && activeThreadIdRef.current === streamThreadId && !streamControllersRef.current.has(streamThreadId)) {
                  resumeAfterReply();
                }
              });
            }
            void refreshThreads();
            // A topic title is generated independently of the reply; refresh without delaying it.
            if (!messages.some(message => message.role === "user")) [2500, 7000, 15000].forEach(delay => window.setTimeout(() => void refreshThreads(), delay));
            void refreshRoster();
            continue;
          }
          if (evt.type === "error" && visible) {
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
      if (!ownsStreamCleanup(streamControllersRef.current.get(streamThreadId), streamController)) return;
      const msg = String(err);
      const pretty = msg.includes("Failed to fetch") ? `Backend offline (${apiBase})` : msg;
      if (activeThreadIdRef.current === streamThreadId) setBackendOnline(false);
      appendTurnMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${pretty}`, at: Date.now() });
      if (activeThreadIdRef.current === streamThreadId) setActivities((prev) => [
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

      if (sameThread) setStreaming(false);
      // A lean turn interrupted before its final event keeps what already streamed.
      if (lean.get(streamThreadId)?.requestId === runRequestId) {
        const leftover = lean.finish(streamThreadId);
        if (leftover && !finalHandled) {
          for (const id of leftover.order) {
            const item = leftover.messages[id];
            if (!item || !item.segments.length) continue;
            const text = item.segments.filter((s) => s.kind === "text").map((s) => (s as { text: string }).text).join("\n\n").trim();
            appendTurnMessage({
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
      if (!finalHandled && streamThreadId && !aborted && sameThread) {
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

  const recoveredRunsRef = useRef(new Set<string>());
  useEffect(() => {
    const session = String(activeThreadId || "");
    if (!initialHydrationComplete || !session || streamControllersRef.current.has(session)) return;
    let disposed = false;
    void fetch(`${apiBase}/query/runs?thread_id=${encodeURIComponent(session)}`).then(async response => {
      if (!response.ok) return;
      const data = await response.json();
      const run = (data.items || []).find((item: any) => item.status === "running") || (data.items?.[0]?.status === "interrupted" ? data.items[0] : undefined);
      if (!disposed && run && !recoveredRunsRef.current.has(run.id) && !streamControllersRef.current.has(session)) {
        await loadHistory(session);
        if (disposed || activeThreadIdRef.current !== session || streamControllersRef.current.has(session)) return;
        recoveredRunsRef.current.add(run.id);
        void sendText("", undefined, { id: run.id, session });
      }
    }).catch(() => {});
    return () => { disposed = true; };
  }, [apiBase, activeThreadId, initialHydrationComplete]);

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
      if (mention || document.querySelector(".toolbar-menu, .es-modal-scrim, .st-root")) return;
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
    nativeLiveVoice, pauseVoice, resumeAfterReply,
    toggleReadAloud, toggleVoiceMode, toggleWakeWord, start, stop, speakLocalText,
  } = useVoice({
    apiBase, activeThreadId, activeProjectId, activeThreadIdRef, activeProjectIdRef,
    streaming, listening, setListening, speechEnabled, onTranscript: submitVoiceTranscript,
    nativeLiveAvailable: providerInfo?.provider === "gemini" && /live|native-audio/i.test(providerInfo?.model || ""),
    onInterrupt: () => { if (streamControllersRef.current.has(activeThreadIdRef.current)) cancelSessionTurn(activeThreadIdRef.current, true); },
  });

  useLayoutEffect(() => {
    if (voiceConversationMode) scrollChatToBottom();
  }, [voiceConversationMode, scrollChatToBottom]);

  const reviseMessage = async (message: Message, text: string) => {
    const source = activeThreadIdRef.current;
    if (messageActionBusy || streamControllersRef.current.has(source)) throw new Error("Stop this chat or wait for its reply before retrying.");
    if (!message.executionId && !message.clientRequestId) throw new Error("Reload this chat to retrieve the prompt's saved identity.");
    setMessageActionBusy(true);
    let continuationId = "";
    try {
      const response = await fetch(`${apiBase}/threads/${encodeURIComponent(source)}/branch`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ execution_id: message.executionId || "", client_request_id: message.clientRequestId || "" }),
      });
      const branch = await response.json();
      if (!response.ok) throw new Error(typeof branch.detail === "string" ? branch.detail : "Couldn't continue from this prompt.");
      await refreshThreads(); await refreshRoster();
      if (activeThreadIdRef.current !== source) throw new Error("The branch is saved in your chats. Open it to continue; your current chat was left in place.");
      stopTts();
      switchThread(branch.thread_id);
      activeProjectIdRef.current = String(branch.project_id || "");
      setActiveProjectId(activeProjectIdRef.current);
      await loadHistory(branch.thread_id);
      await refreshProviderInfo({ allowRetry: true });
      continuationId = branch.thread_id;
    } finally { setMessageActionBusy(false); }
    if (activeThreadIdRef.current !== continuationId) throw new Error("Your branch is saved. Open it to send the revised prompt.");
    followerRef.current.reset(true, chatScrollRef.current || undefined);
    scrollChatToBottom(true);
    await sendText(text);
  };

  const readMessage = async (message: Message) => {
    if (readingId === message.id) { stopTts(); setReadingId(""); return; }
    if (voiceConversationMode) pauseVoice();
    stopTts(); setReadingId(message.id); setSpeechEnabled(true);
    await speakLocalText(message.text, { clientTurnId: `read-${crypto.randomUUID()}`, executionId: message.executionId, completeTurn: true, force: true });
    setReadingId(current => current === message.id ? "" : current);
  };

  const openVoiceSettings = () => {
    localStorage.setItem("echospeak.settings.section", "voice");
    window.dispatchEvent(new CustomEvent("echospeak.settings.navigate", { detail: "voice" }));
    if (desktopMode && !desktopSettingsWindow) void openDesktopSettingsWindow().catch(() => setDesktopSettingsOpen(true));
    else setDesktopSettingsOpen(true);
  };


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

  const rightOpen = mainPage === "chat" && !voiceConversationMode && Boolean(openArtifact || activityOpen);
  const rightDocked = rightOpen && !narrowLayout;
  const shellColumns = [
    desktopMode
      ? [showSidebar ? (sidebarCollapsed || narrowLayout ? "56px" : `${SIDEBAR_WIDTH}px`) : null, "minmax(0, 1fr)"].filter(Boolean).join(" ")
      : runtimeGridColumns({
        sidebarVisible: showSidebar,
        sidebarCollapsed: sidebarCollapsed || narrowLayout,
        visualizerVisible: false,
        visualizerDensity: "normal",
      }),
    rightDocked ? `${panelWidth}px` : "",
  ].filter(Boolean).join(" ");

  const closePage = useCallback(() => setMainPage("chat"), []);
  const sidebarWidthPx = showSidebar ? (sidebarCollapsed || narrowLayout ? 56 : SIDEBAR_WIDTH) : 0;
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
          if (!openArtifact && !activityOpen) setRightTab("activity");
        }
        if (seg.kind !== "tool" || !seg.widgets) continue;
        for (const widget of seg.widgets as { type?: string; data?: { id?: string; version?: number } }[]) {
          if (widget?.type !== "artifact" || !widget.data?.id) continue;
          const key = `${widget.data.id}@${widget.data.version}`;
          if (seenArtifactsRef.current.has(key)) continue;
          seenArtifactsRef.current.add(key);
          setOpenArtifact({ id: widget.data.id });
          if (!openArtifact && !activityOpen) setRightTab("artifact");
        }
      }
    }
  }, [lean.live]); // eslint-disable-line react-hooks/exhaustive-deps
  const widgetEnv = useMemo<WidgetEnv>(
    () => ({ apiBase, openArtifact: (id, version) => { setOpenArtifact({ id, version }); setRightTab("artifact"); }, openResearch: async url => {
      const session = activeThreadIdRef.current;
      if (!session) return false;
      const response = await fetch(`${apiBase}/sessions/${encodeURIComponent(session)}/research`);
      if (!response.ok) return false;
      const book = await response.json();
      const normalize = (value: string) => { const u = new URL(value); u.hash = ""; return u.href.replace(/\/$/, ""); };
      const source = book.sources?.find((item: any) => normalize(item.url) === normalize(url));
      if (!source || activeThreadIdRef.current !== session) return false;
      sessionStorage.setItem(`echospeak:research-source:${session}`, source.id);
      setActivityOpen(true); setRightTab("research");
      window.dispatchEvent(new CustomEvent("echospeak:research-source", { detail: { session, sourceId: source.id } }));
      return true;
    } }),
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
  /** Removes a project; its chats stay and become ordinary chats. */
  const deleteProject = async (id: string) => {
    const response = await fetch(`${apiBase}/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!response.ok) return;
    setThreads(items => items.map(item => item.projectId === id ? { ...item, projectId: "" } : item));
    if (activeProjectId === id) { setActiveProjectId(""); await refreshThreadState(activeThreadId); }
    await refreshProjects();
  };

  const openArtifactFromPage = (item: ArtifactSummary) => {
    setMainPage("chat");
    if (item.session_id && item.session_id !== activeThreadId) switchThread(item.session_id);
    setOpenArtifact({ id: item.id, version: item.version });
    setRightTab("artifact");
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
      <FirstRunSetup apiBase={apiBase} autoShow={!desktopSettingsWindow} onReady={async () => {
        setMainPage("chat");
        setLeftTab("chat");
        if (!activeThreadIdRef.current && !await createNewThread("", "First chat")) throw new Error("Could not open your first chat. Try again.");
      }} />
      <style>{globalCss}</style>
      <style>{leanCss}</style>
      <style>{learningCss}</style>
      <style>{settingsCss}</style>
      <style>{chatPolishCss}</style>
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
          (desktopMode ? " desktop-single-workspace" : "") +
          (voiceConversationMode && mainPage === "chat" ? " is-voice-mode" : "")
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
          onDeleteProject={(id) => void deleteProject(id)}
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
            sessionId={activeThreadId}
            tab={rightTab}
            onTab={setRightTab}
            artifact={openArtifact}
            activity={activityItems}
            onEditArtifact={(id, version, passage) => {
              setMainPage("chat");
              setInput(`Read artifact ${id} at version ${version}, then revise it and save a new version. Preserve the original version.${passage ? `\nSelected passage:\n${passage}\n` : "\n"}Changes I'd like: `);
              window.setTimeout(() => textareaRef.current?.focus(), 0);
            }}
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
                onNewChat={(project) => {
                  setMainPage("chat");
                  void createNewThread(project.id);
                }}
                onDelete={(project) => void deleteProject(project.id)}
                onOpen={(project) => {
                  setMainPage("chat");
                  const recent = threads.find((t) => t.projectId === project.id);
                  if (recent) switchThread(recent.id);
                  else void createNewThread(project.id);
                }}
              />
            ) : mainPage === "creations" ? (
              <CreationsPage apiBase={apiBase} sessionId={activeThreadId} onChat={id => { setMainPage("chat"); switchThread(id); }} onEdit={asset => { setMainPage("chat"); switchThread(asset.session_id); setInput(`Edit this image using input_asset_ids ["${asset.id}"]. Keep the composition and change the lighting to soft morning light.`); }} />
            ) : mainPage === "learning" ? (
              <LearningPage apiBase={apiBase} />
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
                <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 6, background: "linear-gradient(135deg, rgba(34,197,94,0.15), rgba(34,197,94,0.05))", border: "1px solid rgba(34,197,94,0.25)", color: "var(--es-ok)", fontWeight: 600, marginLeft: 8 }}>
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
                  color: "var(--es-text-strong)",
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
                  color: "var(--es-text-strong)",
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
            <div
              className="research-panel"
              onMouseMove={(event) => {
                const box = event.currentTarget.getBoundingClientRect();
                const near = event.clientX > box.right - 260 && event.clientY < box.top + 80;
                if (near !== rpPeek) setRpPeek(near);
              }}
              onMouseLeave={() => setRpPeek(false)}
            >
              {/* Chat Tab */}
                <>
                  <WidgetEnvProvider value={widgetEnv}>
                  {!rightOpen && !voiceConversationMode && activeThreadId ? (
                    <button
                      type="button"
                      className={`rp-toggle${rpPeek ? " is-peek" : ""}`}
                      aria-label="Research & activity"
                      onClick={() => {
                        setActivityOpen(true);
                        setRightTab("research");
                      }}
                      title="Open this chat’s research notebook and activity"
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M4 12h3l2-6 4 12 2-6h5" /></svg>
                      Research &amp; activity
                      <small>{activityItems.length}</small>
                    </button>
                  ) : null}
                  <ChatThread
                    activeThreadId={activeThreadId} streaming={streaming} scrollRef={chatScrollRef}
                    onScroll={onChatScroll} onWheel={onChatWheel} onKeyDown={onChatKeyDown}
                    onTouchStart={onChatTouchStart} onTouchEnd={onChatTouchEnd}
                    activeRoom={activeRoom} agents={agents}
                    onEditRoom={(room) => setRoomDialog({ open: true, room })}
                    timeline={timeline} live={lean.live} providerInfo={providerInfo}
                    onQuickReply={(text) => void sendText(text)}
                    pendingApproval={pendingApproval} threadState={threadState}
                    approvalDecisionBusy={approvalDecisionBusy} onApprovalDecision={decideApproval}
                    onLeanApproval={decideLeanApproval}
                    actions={{ onRevise: reviseMessage, onRead: readMessage, onFeedback: (message, value, note) => leanClient.feedback(message.executionId || "", value, note || "", message.lean?.agent?.id || ""), readingId: speaking ? readingId : "", actionBusy: messageActionBusy }}
                    voiceStage={voiceConversationMode ? <VoiceStage apiBase={apiBase} phase={voicePhase} notice={voiceNotice} listening={listening} speaking={speaking} streaming={streaming} native={nativeLiveVoice} level={voiceInputLevel} tool={activityItems.find(item => item.status === "running")?.label || ""} online={Boolean(backendOnline)} onListen={() => { if (streaming) stopActiveTurn(); void start(); }} onPause={pauseVoice} onEnd={toggleVoiceMode} onSettings={openVoiceSettings} /> : undefined}
                  />
                  </WidgetEnvProvider>
                  {!voiceConversationMode || mainPage !== "chat" ? <div className="input-bar">
                    <LiveStatusPill live={streaming ? lean.live : null} onStop={stopActiveTurn} />
                    {/* One card: session strip, the text box, then tools · context · send */}
                    <ComposerInput
                      threads={threads} projects={projects} activeThreadId={activeThreadId}
                      activeProjectId={activeProjectId} threadState={threadState} providerError={providerError}
                      folderPathFromDrop={folderPathFromDrop} attachFolder={attachFolder} onRemoveFolder={async () => {
                        const response = await fetch(`${apiBase}/projects/deactivate?thread_id=${encodeURIComponent(activeThreadId)}`, { method: "POST" });
                        if (!response.ok) return;
                        const data = await response.json();
                        setActiveProjectId(""); setThreadState(data.thread_state || null);
                        setThreads(items => items.map(item => item.id === activeThreadId ? { ...item, projectId: "" } : item));
                      }}
                      textareaRef={textareaRef} input={input} onInput={updateComposerInput} onSend={() => void sendText()}
                      activeRoom={activeRoom} roomMembers={roomMembers} mention={mention} setMention={setMention}
                      messages={messages} providerInfo={providerInfo}
                      toolbar={
                        <ComposerToolbar
                          listening={listening} voicePhase={voicePhase} voiceNotice={voiceNotice}
                          voiceInputLevel={voiceInputLevel} startMic={() => void start()} stopMic={() => void stop()}
                          monitoring={monitoring} toggleMonitor={toggleMonitor}
                          speechEnabled={speechEnabled} setSpeechEnabled={setSpeechEnabled}
                          providerDraft={providerDraft} setProviderDraft={setProviderDraft}
                          setProviderModels={setProviderModels} switchingProvider={switchingProvider}
                          lmStudioOnly={lmStudioOnly} providerInfo={providerInfo}
                          modelPickerValue={modelPickerValue} modelPickerOptions={modelPickerOptions}
                          showModelPicker={showModelPicker} modelsLoading={modelsLoading} reasoningEffort={reasoningEffort}
                          setReasoningEffort={setReasoningEffort} thinkingEnabled={thinkingEnabled}
                          setThinkingEnabled={setThinkingEnabled} voiceReadAloud={voiceReadAloud}
                          toggleReadAloud={toggleReadAloud} voiceConversationMode={voiceConversationMode}
                          toggleVoiceMode={toggleVoiceMode} wakeWordEnabled={wakeWordEnabled} toggleWakeWord={toggleWakeWord}
                        />
                      }
                    />
                  </div> : null}
                </>

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
