import React, { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useLocation, useNavigate } from "react-router-dom";
import { createEmptyTaskPlan, taskPlanReducer } from "./taskPlanProjection";
import type { EchoReaction } from "./components/echoAnimationUtils";
import { ProjectSidebar, type SidebarPage } from "./components/ProjectSidebar";
import { MediaLibraryView } from "./features/media/MediaLibraryView.tsx";
import { useWorkStore } from "./features/work/store";
import { loadRuntimeLayout, runtimeGridColumns, saveRuntimeLayout } from "./runtimeLayout";
import {
  buildLiveOperationalStatus,
  canApplyFinalToChat,
  mergeFinalReply,
  shouldIncludeChatActivity,
} from "./chatPresentation";
import { buildResearchRunFromToolEvent, normalizeResearchRun } from "./features/research/buildResearchRun";
import { useResearchStore } from "./features/research/store";
import type { ResearchRun } from "./features/research/types";
import { buildResponseRenderPlan } from "./features/responseRenderer/buildResponseRenderPlan";
import { buildChatEmbeds } from "./features/embeds/buildChatEmbeds";
import { OperationalStateCard } from "./features/operations/OperationalStateCard";
import type { OperationalThreadState } from "./features/operations/OperationalStateCard";
import { createEchoSpeakWebSocket, controlDesktopWindow, getEchoSpeakApiBase, isDesktopRuntime, openDesktopSettingsWindow, pickDesktopProjectFolder } from "./desktop/bridge";
import {
  LocalVoiceInput,
  WakeListener,
  localVoicePlayback,
} from "./voiceTransport";
import type {
  SpeechScope,
  VoiceTranscript,
  VoiceTransportPhase,
} from "./voiceTransport";
import { canApplySessionHistory, ownsStreamCleanup } from "./desktop/sessionProjection";
import leanCss from "./lean/lean.css?inline";
import settingsCss from "./settings/settings.css?inline";
import { SettingsPanel } from "./settings/SettingsPanel";
import { LeanMessage } from "./lean/LeanMessage";
import { isLeanEvent, messageFromTimeline } from "./lean/liveReducer";
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
import { activityActionsFromStreamEvent, agentActivityReducer, initialAgentActivity, isStreamThreadCurrent } from "./agentActivity";
import { type ActivityItem, type AgentStreamEvent, type ApprovalDecisionEnvelope, type ApprovalRecord, type AvatarConfig, type DiscordLiveEvent, type DocSource, type ExecutionListResponse, type ExecutionRecord, type GatewayEvent, type Message, type PendingActionEnvelope, type ProviderInfo, type Role, type TaskPlanEntry, type ThinkingStep, type ThreadSessionState, type TimelineItem, type VisionAnalyzeResponse, type ProviderModelsResponse } from "./app/types";
import { SILENT_CHAT_TOOLS, buildMessageUsage, formatToolActivity, previewToolInput, toolActivityStepType } from "./app/toolDisplay";
import { colors, defaultAvatarConfig, fallbackProviders, fetchWithTimeout, geminiModelOptions, isEmptySessionDraft, isLmStudioOnlyLocked, listableProviders, normalizeTimestampMs, openaiModelOptions, sanitizeForTTS, stopTts, useAppStore } from "./app/runtime";
import { globalCss } from "./app/globalCss";
import { ActivityCard, ChatBubble, ContextMeter, LiveChatActivityBar } from "./app/chatComponents";
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
    speechBeat,
    speechEnabled,
    setSpeechEnabled,
  } = useAppStore();

  const [input, setInput] = useState("");
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [taskPlans, setTaskPlans] = useState<TaskPlanEntry[]>([]);
  const [echoReaction, setEchoReaction] = useState<EchoReaction | null>(null);
  const [userIsTyping, setUserIsTyping] = useState(false);
  const userTypingTimerRef = useRef<number>(0);
  const updateComposerInput = useCallback((value: string) => {
    setInput(value);
    setUserIsTyping(true);
    if (userTypingTimerRef.current) clearTimeout(userTypingTimerRef.current);
    userTypingTimerRef.current = window.setTimeout(() => setUserIsTyping(false), 1500);
  }, []);
  const prependResearchRun = useResearchStore((state) => state.prependRun);
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
  const [agentMode, setAgentMode] = useState<"idle" | "research" | "coding" | "working" | "thinking">("idle");
  const [agentActivity, dispatchActivity] = useReducer(agentActivityReducer, undefined, initialAgentActivity);
  useEffect(() => {
    if (mediaRouteActive) {
      setLeftTab("chat");
      return;
    }
  }, [desktopMode, mediaRouteActive]);
  const [liveReplyDraft, setLiveReplyDraft] = useState("");
  const liveReplyDraftRef = useRef("");
  const [avatarConfig, setAvatarConfig] = useState<AvatarConfig>(defaultAvatarConfig);
  const [memoryCount, setMemoryCount] = useState<number>(0);
  const [docSources, setDocSources] = useState<DocSource[]>([]);
  const [docFile, setDocFile] = useState<File | null>(null);
  const [monitoring, setMonitoring] = useState<boolean>(false);
  const [monitorText, setMonitorText] = useState<string>("");
  const [monitorAt, setMonitorAt] = useState<number>(0);
  const [monitorError, setMonitorError] = useState<string | null>(null);
  const toolInfoRef = useRef<Record<string, { name: string; input: string; requestId?: string }>>({});
  const desktopBootstrap = typeof window !== "undefined" ? window.__ECHOSPEAK_DESKTOP_BOOTSTRAP__ : undefined;
  const [projects, setProjects] = useState<{
    id: string; name: string; description?: string; context_prompt?: string; tags?: string[];
    workspace_root?: string; archived?: boolean; git_metadata?: Record<string, any>;
  }[]>(() => (desktopBootstrap?.projects || []) as any[]);
  const [activeProjectId, setActiveProjectId] = useState<string>(() => desktopBootstrap?.active_project_id || "");
  const activeProjectIdRef = useRef<string>(desktopBootstrap?.active_project_id || "");
  const [folderDropActive, setFolderDropActive] = useState(false);
  const [projectsLoading, setProjectsLoading] = useState<boolean>(false);
  // Bootstrap data is only a startup hint.  Do not paint it as authoritative
  // chat history: the first scoped /threads read reconciles the durable list.
  // Keeping this false until that read completes prevents transient sessions,
  // welcome messages, and activity rows from flashing and then disappearing.
  const [initialHydrationComplete, setInitialHydrationComplete] = useState(false);
  const [threadState, setThreadState] = useState<ThreadSessionState | null>(() => (desktopBootstrap?.thread_state || null) as ThreadSessionState | null);
  const [pendingApproval, setPendingApproval] = useState<PendingActionEnvelope | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [approvalDecisionBusy, setApprovalDecisionBusy] = useState<boolean>(false);
  const [executions, setExecutions] = useState<ExecutionRecord[]>([]);
  const [executionsLoading, setExecutionsLoading] = useState<boolean>(false);
  const [selectedTrace, setSelectedTrace] = useState<Record<string, any> | null>(null);
  const [selectedTraceId, setSelectedTraceId] = useState<string>("");
  const [latestExecutionId, setLatestExecutionId] = useState<string>("");
  const [latestTraceId, setLatestTraceId] = useState<string>("");

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
  const currentWorkRuns = useWorkStore((state) => state.runs);
  const loadCurrentWorkRuns = useWorkStore((state) => state.loadRuns);
  const workProjectionRevisionRef = useRef<string>("");
  const activeThreadIdRef = useRef<string>(desktopBootstrap?.active_session_id || "");
  const threadCreationFlightRef = useRef<Promise<string> | null>(null);
  const streamControllersRef = useRef<Map<string, AbortController>>(new Map());
  const activeRequestIdsRef = useRef<Map<string, string>>(new Map());
  const activeExecutionIdsRef = useRef<Map<string, string>>(new Map());
  const activeTaskRunIdsRef = useRef<Map<string, string>>(new Map());
  const historyRequestSeqRef = useRef<Map<string, number>>(new Map());
  const projectionRevisionRef = useRef<Map<string, number>>(new Map());
  const sessionProjectionRef = useRef<Map<string, { messages: Message[]; activities: ActivityItem[] }>>(new Map());
  const [inFlightSessionIds, setInFlightSessionIds] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    if (!initialHydrationComplete || !activeThreadId) return;
    void loadCurrentWorkRuns(apiBase, activeThreadId, activeProjectId || "");
  }, [activeProjectId, activeThreadId, apiBase, initialHydrationComplete, loadCurrentWorkRuns]);

  useEffect(() => {
    if (!initialHydrationComplete || !activeThreadId) return;
    const activeStatuses = new Set([
      "running",
      "suspended_waiting_for_user",
      "suspended_waiting_for_approval",
    ]);
    if (!currentWorkRuns.some((run) => activeStatuses.has(String(run.status || "").toLowerCase()))) {
      workProjectionRevisionRef.current = currentWorkRuns
        .map((run) => `${run.id}:${run.revision}:${run.status}`)
        .join("|");
      return;
    }
    let cancelled = false;
    const poll = async () => {
      const before = useWorkStore.getState().runs
        .map((run) => `${run.id}:${run.revision}:${run.status}`)
        .join("|");
      await loadCurrentWorkRuns(apiBase, activeThreadId, activeProjectId || "");
      if (cancelled || activeThreadIdRef.current !== activeThreadId) return;
      const after = useWorkStore.getState().runs
        .map((run) => `${run.id}:${run.revision}:${run.status}`)
        .join("|");
      if (after && after !== before && after !== workProjectionRevisionRef.current) {
        workProjectionRevisionRef.current = after;
        await loadHistory(activeThreadId);
      }
    };
    workProjectionRevisionRef.current = currentWorkRuns
      .map((run) => `${run.id}:${run.revision}:${run.status}`)
      .join("|");
    const interval = window.setInterval(() => void poll(), 2500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [
    activeProjectId,
    activeThreadId,
    apiBase,
    currentWorkRuns,
    initialHydrationComplete,
    loadCurrentWorkRuns,
  ]);

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
    // final "Stopped by Ty." state can arrive from the backend.
    if (!preserveStream) {
      streamControllersRef.current.get(sessionId)?.abort();
      streamControllersRef.current.delete(sessionId);
      activeRequestIdsRef.current.delete(sessionId);
      activeExecutionIdsRef.current.delete(sessionId);
      activeTaskRunIdsRef.current.delete(sessionId);
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
        const loadedMsgs: Message[] = [];
        const loadedActs: ActivityItem[] = [];
        const hydratedResearch: ResearchRun[] = [];
        const ctxWindow = Number(providerInfo?.context_window || 0) || 32768;
        const runsByTurn = new Map<string, any[]>();
        for (const run of sessionToolRuns) {
          const turnKey = String(run.turn_id || "").trim();
          if (!turnKey) continue;
          const bucket = runsByTurn.get(turnKey) || [];
          bucket.push(run);
          runsByTurn.set(turnKey, bucket);
        }

        for (const turn of turns) {
          const executionId = String(turn.execution_id || turn.execution?.id || "").trim();
          const turnStatus = String(turn.progress_status || turn.terminal_status || turn.status || "complete");
          const baseAt = Number(turn.created_at || 0) * 1000 || Date.now();
          const doneAt = Number(turn.completed_at || turn.created_at || 0) * 1000 || baseAt + 1;
          // Prefer turn.tool_runs; fall back to canonical /tool-runs by execution id.
          if ((!Array.isArray(turn.tool_runs) || turn.tool_runs.length === 0) && executionId) {
            const fromApi = runsByTurn.get(executionId) || [];
            if (fromApi.length) turn.tool_runs = fromApi;
          }

          // User + assistant messages (durable items / execution fallback)
          for (const msg of Array.isArray(turn.messages) ? turn.messages : []) {
            const role = String(msg.role || "").toLowerCase() === "user" ? "user" : "assistant";
            const text = String(msg.text || "").trim();
            // Lean messages closed at a handoff may hold only tool cards.
            const leanPart = role === "assistant" && Boolean(msg.agent_id && msg.message_id) && Array.isArray(msg.timeline) && msg.timeline.length > 0;
            if (!text && !leanPart) continue;
            const atMs = Number(msg.at || 0) * 1000 || (role === "user" ? baseAt : doneAt);
            const msgId = `hist-${executionId || "x"}-${role}-${msg.item_id || loadedMsgs.length}`;
            if (loadedMsgs.some((m) => m.id === msgId || (msg.message_id && m.id === String(msg.message_id)) || (!leanPart && m.executionId === executionId && m.role === role && m.text === text))) {
              continue;
            }
            const researchRuns: ResearchRun[] = [];
            if (role === "assistant" && Array.isArray(turn.research_runs)) {
              for (const raw of turn.research_runs) {
                const normalized = normalizeResearchRun(raw);
                if (normalized) researchRuns.push(normalized);
              }
            }
            const embeds =
              role === "assistant" && researchRuns.length
                ? buildChatEmbeds({
                    answerText: text,
                    researchRuns,
                    searchQueries: researchRuns.map((r) => r.query).filter(Boolean),
                  })
                : undefined;
            const renderPlan =
              role === "assistant"
                ? buildResponseRenderPlan({
                    answerText: text,
                    researchRuns,
                    searchQueries: researchRuns.map((r) => r.query).filter(Boolean),
                  })
                : undefined;
            for (const r of researchRuns) {
              if (!hydratedResearch.some((h) => h.id === r.id)) hydratedResearch.push(r);
            }
            // Turn-scoped progress only — never attach full Session action lists
            // (that painted Pokémon research under a prior "whats up" chat Turn).
            const executionProjection =
              turn.execution_projection && typeof turn.execution_projection === "object"
                ? turn.execution_projection
                : {};
            const projectedRuns = Array.isArray(turn.tool_runs) ? turn.tool_runs : [];
            const runById = new Map(projectedRuns.map((run: any) => [String(run.id || ""), run]));
            const projectedAction = (runId: unknown, success: boolean) => {
              const run: any = runById.get(String(runId || ""));
              if (!run) return null;
              const outcome = run.outcome || {};
              return {
                execution_id: executionId,
                tool_run_id: String(run.id || ""),
                tool: String(run.tool_name || "tool"),
                summary: String(outcome.output || outcome.error_message || run.status || "").slice(0, 240),
                status: String(run.status || (success ? "complete" : "failed")),
                success,
                execution_status: String(outcome.execution_status || ""),
                result_state: String(outcome.result_state || ""),
                provider: String(outcome.provider || ""),
                observed_at: Number(outcome.observed_at || 0),
                confidence: outcome.confidence == null ? null : Number(outcome.confidence),
              };
            };
            const completedActions = (Array.isArray(executionProjection.successful_mutations)
              ? executionProjection.successful_mutations
              : [])
              .map((id: unknown) => projectedAction(id, true))
              .filter(Boolean) as Record<string, any>[];
            const failedActions = (Array.isArray(executionProjection.blocked_mutations)
              ? executionProjection.blocked_mutations
              : [])
              .map((id: unknown) => projectedAction(id, false))
              .filter(Boolean) as Record<string, any>[];
            const pendingActions = (Array.isArray(turn.approvals) ? turn.approvals : [])
              .filter((approval: any) => String(approval.status || "") === "pending")
              .map((approval: any) => ({
                execution_id: executionId,
                approval_id: String(approval.id || ""),
                tool: String(approval.tool || "action"),
                summary: String(approval.summary || approval.preview || "Awaiting approval"),
                status: "needs_permission",
                success: false,
              }));
            const changedFiles = (Array.isArray(executionProjection.files_actually_changed)
              ? executionProjection.files_actually_changed
              : [])
              .flatMap((item: any) => [String(item?.path || "").trim(), String(item?.destination || "").trim()])
              .filter(Boolean);
            const turnScopedState: OperationalThreadState = {
              thread_id: threadId,
              mode: String(turn.execution?.mode || "chat"),
              phase: String(turn.execution?.phase || ""),
              execution_status:
                turnStatus === "interrupted"
                  ? "in_progress"
                  : String(executionProjection.status || turnStatus || "complete"),
              current_execution_id: executionId,
              last_execution_id: executionId,
              terminal_status: String(turn.terminal_status || turnStatus),
              safest_next_action:
                turnStatus && !["complete", "completed", "ready", ""].includes(String(turnStatus))
                  ? String(executionProjection.next_action || turn.verification?.next_action || turn.progress?.status || "")
                  : "",
              completed_actions: completedActions,
              failed_actions: failedActions,
              pending_actions: pendingActions,
              plan_steps: [],
              retry_target:
                executionProjection.retry_target && typeof executionProjection.retry_target === "object"
                  ? executionProjection.retry_target
                  : {},
              operation_details: {
                tools_used: projectedRuns
                  .filter((r: any) => {
                    const st = String(r.status || "").toLowerCase();
                    return !["cancelled", "canceled", "interrupted"].includes(st);
                  })
                  .map((r: any) => String(r.tool_name || ""))
                  .filter(Boolean),
                files_changed: changedFiles,
                memory_records: Array.isArray(executionProjection.memory_records)
                  ? executionProjection.memory_records.map((item: any) => String(item?.memory_id || item?.item_id || "")).filter(Boolean)
                  : [],
              },
            } as OperationalThreadState;
            const leanAgentId = role === "assistant" ? String(msg.agent_id || "") : "";
            const leanPersona = leanAgentId ? agents.find((a) => a.id === leanAgentId) : undefined;
            loadedMsgs.push({
              id: leanAgentId && msg.message_id ? String(msg.message_id) : msgId,
              lean: leanAgentId
                ? messageFromTimeline({
                    messageId: String(msg.message_id || msgId),
                    agentId: leanAgentId,
                    agentName: String(leanPersona?.name || msg.agent_name || "Echo"),
                    initials: leanPersona?.initials,
                    title: leanPersona?.title,
                    text,
                    timeline: Array.isArray(msg.timeline) ? msg.timeline : [],
                    at: atMs,
                    success: msg.backend_success !== false,
                    delegatedBy: msg.delegated_by?.name ? String(msg.delegated_by.name) : undefined,
                    role: msg.agent_role ? String(msg.agent_role) : undefined,
                    stopReason: msg.stop_reason ? String(msg.stop_reason) : undefined,
                  })
                : undefined,
              role,
              text,
              at: atMs,
              skipTypewriter: true,
              streamBeat: role === "assistant" ? "final" : undefined,
              executionId: executionId || undefined,
              clientRequestId: String(turn.request_id || turn.execution?.request_id || "") || undefined,
              embeds: embeds?.length ? embeds : undefined,
              renderPlan,
              operation:
                role === "assistant"
                  ? {
                      state: turnScopedState,
                      success:
                        turn.success !== false &&
                        !["failed", "blocked", "cancelled"].includes(String(turnStatus)),
                      executionId: executionId || undefined,
                    }
                  : undefined,
              usage: buildMessageUsage(text, loadedMsgs, ctxWindow, {
                provider: providerInfo?.provider,
                model: providerInfo?.model,
              }),
            });
          }

          // A group chat or handed-off job shows how it ended under its last message.
          const turnOutcome = turn.execution?.metadata?.outcome;
          if (turnOutcome && typeof turnOutcome === "object") {
            for (let i = loadedMsgs.length - 1; i >= 0; i -= 1) {
              const m = loadedMsgs[i];
              if (m.lean && m.executionId === (executionId || undefined)) {
                m.lean = {
                  ...m.lean,
                  outcome: {
                    status: turnOutcome.status === "stopped" ? "stopped" : "done",
                    summary: turnOutcome.summary ? String(turnOutcome.summary) : undefined,
                    reason: turnOutcome.reason ? String(turnOutcome.reason) : undefined,
                  },
                };
                break;
              }
            }
          }

          // ToolRuns — exact IDs, completed/failed only (never live spinners after refresh).
          // Lean turns carry their tools inside each agent's timeline instead.
          const leanTurn = (Array.isArray(turn.messages) ? turn.messages : []).some((m: any) => m?.agent_id);
          const runs = leanTurn ? [] : Array.isArray(turn.tool_runs) ? turn.tool_runs : [];
          for (const run of runs) {
            const runId = String(run.id || "").trim();
            const toolName = String(run.tool_name || "tool").trim();
            if (!runId || SILENT_CHAT_TOOLS.has(toolName)) continue;
            if (loadedActs.some((a) => a.kind === "tool" && a.id === runId)) continue;
            const args = run.canonical_arguments || {};
            const inputPreview = previewToolInput(
              toolName,
              typeof args === "object" ? JSON.stringify(args) : String(args || "")
            );
            const st = String(run.status || "").toLowerCase();
            const outcome = run.outcome || {};
            const outcomeOk = outcome.success === true || st === "complete" || st === "success";
            const outcomeFail =
              outcome.success === false ||
              st === "failed" ||
              st === "error" ||
              Boolean(outcome.error_message) ||
              Boolean(outcome.policy_block);
            let uiStatus: "running" | "done" | "error" = "done";
            if (outcomeFail) uiStatus = "error";
            else if (outcomeOk) uiStatus = "done";
            else if (st === "started" || st === "pending" || st === "running") {
              // A persisted nonterminal ToolRun has no success evidence. Never
              // turn it into "done" merely because its Turn was superseded or
              // finalized; hydrate it as interrupted without a live spinner.
              uiStatus = "error";
            }
            const outText =
              String(outcome.output || outcome.error_message || outcome.error_code || "").trim() ||
              (uiStatus === "error" && ["started", "pending", "running"].includes(st)
                ? "Interrupted before a terminal tool outcome was recorded"
                : "");
            // Place tools strictly inside this Turn's time window so they never
            // sort under a previous casual-chat assistant message.
            const atMs = Math.min(
              Math.max(Number(run.created_at || 0) * 1000 || baseAt + 10, baseAt + 1),
              Math.max(doneAt - 1, baseAt + 2)
            );
            // Skip pure wrapper fan-out shells — children are the canonical rows.
            if (
              toolName === "web_search" &&
              (String(outText || "").startsWith("(expanded to") ||
                String(outText || "").startsWith("(superseded by canonical"))
            ) {
              continue;
            }
            // Hydration: one user-facing web_search row per ToolRun id (already unique).
            // Skip cancelled/wrapper statuses that slipped past earlier filters.
            if (toolName === "web_search" && ["cancelled", "canceled"].includes(st)) {
              continue;
            }
            if (
              toolName === "file_list" && st === "interrupted" &&
              runs.some((other: any) => other.id !== run.id && other.tool_name === "file_list" && ["complete", "success"].includes(String(other.status || "").toLowerCase()))
            ) {
              continue;
            }
            loadedActs.push({
              kind: "tool",
              id: runId,
              name: toolName,
              input: inputPreview,
              status: uiStatus,
              output: outText ? outText.slice(0, 1500) : undefined,
              at: atMs,
            });
          }

          // Durable verification / denial errors as activity rows under the Turn timestamps
          if (turn.error && String(turn.error).trim()) {
            const errId = `hist-err-${executionId}`;
            if (!loadedActs.some((a) => a.id === errId)) {
              loadedActs.push({
                kind: "error",
                id: errId,
                message: String(turn.error).trim().slice(0, 800),
                at: doneAt,
              });
            }
          }
        }

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
        // Never resume live stream chrome from history.
        setAgentMode("idle");
        setEchoReaction(null);
        dispatchActivity({ type: "reset" });
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
        setAgentMode("idle");
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
      setLatestExecutionId(String(data.last_execution_id || ""));
      setLatestTraceId(String(data.last_trace_id || ""));
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
        setLatestExecutionId(String(data.execution_id || data.thread_state.last_execution_id || ""));
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
      setEchoReaction(data.success ? "success" : "error");
    } catch (error) {
      if (activeThreadIdRef.current !== expectedSessionId) return;
      const message = error instanceof Error ? error.message : String(error);
      setEchoReaction("error");
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

  const refreshExecutions = async (threadId: string = activeThreadId) => {
    if (!threadId) return;
    setExecutionsLoading(true);
    try {
      const resp = await fetchWithTimeout(`${apiBase}/executions?thread_id=${encodeURIComponent(threadId)}&limit=25`, undefined, 6000);
      if (!resp.ok) throw new Error(`Executions failed (${resp.status})`);
      const data = (await resp.json()) as ExecutionListResponse;
      if (activeThreadIdRef.current !== threadId) return;
      setExecutions(Array.isArray(data.items) ? data.items : []);
    } catch (e) {
      console.error("Failed to refresh executions:", e);
    } finally {
      setExecutionsLoading(false);
    }
  };

  const refreshProjects = async () => {
    setProjectsLoading(true);
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
      setProjectsLoading(false);
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
        setTaskPlans([]);
        clearResearchRuns();
        setPendingApproval(null);
        setApprovals([]);
        setExecutions([]);
        setSelectedTrace(null);
        dispatchActivity({ type: "reset" });
        setStreaming(false);
        liveReplyDraftRef.current = "";
        setLiveReplyDraft("");
        setDocSources([]);
        toolInfoRef.current = {};
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
    dispatchActivity({ type: "reset" });
    // The live timeline belongs to the Session that was visible; history reload restores it.
    lean.finish();
    setMention(null);
    setStreaming(streamControllersRef.current.has(id));
    liveReplyDraftRef.current = "";
    setLiveReplyDraft("");
    setDocSources([]);
    toolInfoRef.current = {};
    // In a real app, we might fetch history from backend here.
    // For now, we'll clear local state to start fresh in the new context.
    const cachedProjection = sessionProjectionRef.current.get(id);
    useAppStore.setState({ messages: cachedProjection?.messages || [] });
    setActivities(cachedProjection?.activities || []);
    setTaskPlans([]);
    clearResearchRuns();
    setPendingApproval(null);
    setApprovals([]);
    setExecutions([]);
    setSelectedTrace(null);
    setSelectedTraceId("");
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

  const docInputRef = useRef<HTMLInputElement | null>(null);
  const backendRetryRef = useRef<{ attempt: number; timer: number | null }>({ attempt: 0, timer: null });
  const refreshAvatarConfig = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/avatar/config`);
      if (!res.ok) return;
      const data = await res.json();
      setAvatarConfig({ ...defaultAvatarConfig, ...data });
    } catch {
      // ignore bootstrap avatar failures
    }
  }, [apiBase]);

  useEffect(() => {
    refreshAvatarConfig();
  }, [refreshAvatarConfig]);

  const [providerInfo, setProviderInfo] = useState<ProviderInfo | null>(null);
  const [providerModels, setProviderModels] = useState<string[]>([]);
  const [providerDraft, setProviderDraft] = useState<{ provider: string; model: string; base_url: string }>({
    provider: "",
    model: "",
    base_url: "",
  });
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
  const [voiceReadAloud, setVoiceReadAloud] = useState<boolean>(
    () => window.localStorage.getItem("echospeak.voice.read_aloud") === "true",
  );
  const [voiceConversationMode, setVoiceConversationMode] = useState<boolean>(false);
  const [wakeWordEnabled, setWakeWordEnabled] = useState<boolean>(
    () => window.localStorage.getItem("echospeak.voice.wake") === "true",
  );
  const wakeListenerRef = useRef<WakeListener | null>(null);
  const [voicePhase, setVoicePhase] = useState<VoiceTransportPhase>("idle");
  const [voiceNotice, setVoiceNotice] = useState("");
  const [voiceInputLevel, setVoiceInputLevel] = useState(0);
  const voiceInputRef = useRef<LocalVoiceInput | null>(null);
  if (voiceInputRef.current == null) voiceInputRef.current = new LocalVoiceInput();
  const [showSteerModal, setShowSteerModal] = useState<boolean>(false);
  const [steerInput, setSteerInput] = useState<string>("");
  const [steerSubmitting, setSteerSubmitting] = useState<boolean>(false);
  useEffect(() => {
    window.localStorage.setItem("echospeak.voice.read_aloud", String(voiceReadAloud));
  }, [voiceReadAloud]);
  useEffect(() => {
    window.localStorage.setItem("echospeak.chat.thinking_enabled", String(thinkingEnabled));
  }, [thinkingEnabled]);
  useEffect(() => {
    window.localStorage.setItem("echospeak.chat.reasoning_effort", reasoningEffort);
  }, [reasoningEffort]);
  const lmStudioOnly = useMemo(() => isLmStudioOnlyLocked(providerInfo), [providerInfo]);
  const [providerError, setProviderError] = useState<string | null>(null);
  const [switchingProvider, setSwitchingProvider] = useState(false);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const gatewaySocketRef = useRef<WebSocket | null>(null);
  const gatewayRetryTimerRef = useRef<number | null>(null);
  const gatewayRetryAttemptRef = useRef<number>(0);
  const [discordGatewayConnected, setDiscordGatewayConnected] = useState<boolean>(false);
  const [discordGatewaySessionId, setDiscordGatewaySessionId] = useState<string>("");
  const [discordLiveEvents, setDiscordLiveEvents] = useState<DiscordLiveEvent[]>([]);
  const [spotifyPlaying, setSpotifyPlaying] = useState<{ is_playing: boolean; track_id: string; track_name: string; track_artist: string } | null>(null);

  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const chatBottomRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const sessionScrollRef = useRef<Map<string, { top: number; atBottom: boolean }>>(new Map());
  /** Ignore scroll events caused by our own pin-to-bottom so we never unstick mid-update. */
  const programmaticScrollRef = useRef(false);
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

  const lastAppliedProviderRef = useRef<{ provider: string; model: string } | null>(null);
  const suppressAutoApplyRef = useRef(true);

  const scheduleBackendRetry = () => {
    if (backendRetryRef.current.timer != null) return;
    const attempt = backendRetryRef.current.attempt;
    // Backoff is deliberately slow after the initial recovery window. The old
    // six-second ceiling could hammer a failing provider endpoint indefinitely
    // and turn one backend exception into a noisy desktop-wide failure loop.
    const delay = Math.min(30000, Math.round(900 * Math.pow(1.8, attempt)));
    backendRetryRef.current.attempt = Math.min(attempt + 1, 8);
    backendRetryRef.current.timer = window.setTimeout(() => {
      backendRetryRef.current.timer = null;
      refreshProviderInfo({ allowRetry: true });
    }, delay);
  };

  const refreshProviderInfo = async (opts: { allowRetry?: boolean } = {}) => {
    try {
      setProviderError(null);
      const scope = new URLSearchParams({ session_id: String(activeThreadIdRef.current || "default") });
      const resp = await fetchWithTimeout(`${apiBase}/provider?${scope.toString()}`, undefined, 10000);
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      const info = (await resp.json()) as ProviderInfo;
      setProviderInfo(info);
      setBackendOnline(true);
      backendRetryRef.current.attempt = 0;
      if (backendRetryRef.current.timer != null) {
        window.clearTimeout(backendRetryRef.current.timer);
        backendRetryRef.current.timer = null;
      }
      lastAppliedProviderRef.current = { provider: info.provider, model: info.model };
      suppressAutoApplyRef.current = false;
      setProviderDraft((d) => ({
        ...d,
        provider: info.provider,
        model: info.model,
        base_url: info.base_url ? String(info.base_url) : d.base_url,
      }));
    } catch (e) {
      setBackendOnline(false);
      const err = e instanceof Error ? e : new Error(String(e));
      const msg = err.message || String(e);
      const aborted = err.name === "AbortError" || msg.toLowerCase().includes("aborted");
      const offline = aborted || msg.includes("Failed to fetch");
      const serverFailure = /\b5\d\d\b/.test(msg);
      const pretty = offline ? "Backend offline" : msg;
      const shouldRetry = Boolean(opts.allowRetry && (offline || serverFailure));
      setProviderError(offline && shouldRetry ? "Backend offline — retrying" : pretty);
      if (shouldRetry) scheduleBackendRetry();
    }
  };

  const refreshProviderModels = async (provider: string) => {
    try {
      setModelsLoading(true);
      const resp = await fetchWithTimeout(`${apiBase}/provider/models?provider=${encodeURIComponent(provider)}`);
      if (!resp.ok) return;
      const data = (await resp.json()) as ProviderModelsResponse;
      setProviderModels(Array.isArray(data.models) ? data.models : []);
    } catch {
      setProviderModels([]);
    } finally {
      setModelsLoading(false);
    }
  };

  const applyProviderSwitch = async (draft?: { provider: string; model: string; base_url: string }) => {
    if (lmStudioOnly) return;
    const next = draft || providerDraft;
    if (!next.provider) return;
    setSwitchingProvider(true);
    setProviderError(null);
    cancelSessionTurn(String(activeThreadIdRef.current || ""));
    try {
      const body: any = {
        provider: next.provider,
        session_id: String(activeThreadIdRef.current || "default"),
        expected_revision: Number(providerInfo?.binding_revision || 1),
      };
      if (next.provider === "openai") body.openai_model = next.model || undefined;
      else if (next.provider === "gemini") body.gemini_model = next.model || undefined;
      else body.model = next.model || undefined;
      if (next.base_url) body.base_url = next.base_url;

      const resp = await fetchWithTimeout(`${apiBase}/provider/switch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const t = await resp.text();
        throw new Error(t || `${resp.status} ${resp.statusText}`);
      }
      lastAppliedProviderRef.current = { provider: next.provider, model: next.model || "" };
      await refreshProviderInfo();
      if (listableProviders.includes(next.provider)) {
        await refreshProviderModels(next.provider);
      }
    } catch (e) {
      setProviderError(e instanceof Error ? e.message : String(e));
    } finally {
      setSwitchingProvider(false);
    }
  };

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
  const taskPlanLen = taskPlans.reduce((sum, entry) => sum + entry.plan.tasks.length, 0);

  /**
   * Pin chat fully to the latest content. Instant scroll only — smooth scrolling
   * gets interrupted mid-animation when content keeps growing and leaves the view at ~90–98%.
   */
  const scrollChatToBottom = useCallback((force: boolean = false) => {
    if (!force && !stickToBottomRef.current) return;
    const el = chatScrollRef.current;
    if (!el) return;

    const pin = () => {
      programmaticScrollRef.current = true;
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
      pinBottomRafRef.current = requestAnimationFrame(() => {
        pin();
        // Clear flag after the browser has emitted the scroll event for our pin.
        requestAnimationFrame(() => {
          programmaticScrollRef.current = false;
        });
      });
    });
  }, []);

  const onChatScroll = () => {
    if (programmaticScrollRef.current) return;
    const el = chatScrollRef.current;
    if (!el) return;
    // User is still "at bottom" if within a small slack of the true end.
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distFromBottom <= 48;
    const sessionId = String(activeThreadIdRef.current || "");
    if (sessionId) sessionScrollRef.current.set(sessionId, { top: el.scrollTop, atBottom: stickToBottomRef.current });
  };

  useLayoutEffect(() => {
    const el = chatScrollRef.current;
    if (!el || !activeThreadId) return;
    const saved = sessionScrollRef.current.get(activeThreadId);
    programmaticScrollRef.current = true;
    requestAnimationFrame(() => {
      if (saved?.atBottom || !saved) el.scrollTop = el.scrollHeight;
      else el.scrollTop = Math.min(saved.top, Math.max(0, el.scrollHeight - el.clientHeight));
      stickToBottomRef.current = saved?.atBottom ?? true;
      requestAnimationFrame(() => { programmaticScrollRef.current = false; });
    });
  }, [activeThreadId]);

  useEffect(() => {
    // initial mount / tab switch — always jump to latest
    if (leftTab === "chat") {
      stickToBottomRef.current = true;
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
    taskPlanLen,
    streaming,
    speaking,
    liveReplyDraft,
    pendingApproval?.has_pending,
    scrollChatToBottom,
  ]);

  // While streaming/speaking, content height keeps changing after effects run — keep pinned.
  useEffect(() => {
    if (leftTab !== "chat") return;
    if (!streaming && !speaking) return;
    const id = window.setInterval(() => {
      if (stickToBottomRef.current) scrollChatToBottom(false);
    }, 80);
    return () => window.clearInterval(id);
  }, [leftTab, streaming, speaking, scrollChatToBottom]);

  // When message/tool nodes resize (markdown, embeds, ops card), stay at true bottom.
  useEffect(() => {
    const el = chatScrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;

    const pinIfStuck = () => {
      if (stickToBottomRef.current) scrollChatToBottom(false);
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

  const speakLocalText = async (
    text: string,
    metadata: Pick<SpeechScope, "clientTurnId" | "requestId" | "executionId" | "taskRunId" | "completeTurn">,
  ) => {
    const sessionId = String(activeThreadIdRef.current || "").trim();
    const cleaned = sanitizeForTTS(text);
    if (!speechEnabled || !sessionId || !cleaned) return false;
    setVoiceNotice("");
    try {
      await localVoicePlayback.speak(
        cleaned,
        {
          apiBase,
          sessionId,
          projectId: String(activeProjectIdRef.current || ""),
          ...metadata,
        },
        {
          onPhase: (phase, detail) => {
            setVoicePhase(phase);
            setVoiceNotice(detail || "");
            useAppStore.getState().setSpeaking(phase === "speaking");
          },
          onLevel: (level) => {
            if (level > 0) useAppStore.getState().bumpSpeechBeat();
          },
        },
      );
      return true;
    } catch (error) {
      if ((error as any)?.name === "AbortError") return false;
      setVoicePhase("error");
      setVoiceNotice(error instanceof Error ? error.message : "Local speech playback is unavailable.");
      useAppStore.getState().setSpeaking(false);
      return false;
    }
  };

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

    stickToBottomRef.current = true; // force sticky to bottom when sending a message
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
    setUserIsTyping(false);
    if (userTypingTimerRef.current) clearTimeout(userTypingTimerRef.current);
    setDocSources([]);
    // Turn-local research only — do not carry prior Session source cards into this answer.
    // Research history remains available through Chat embeds; keep each turn isolated.
    // Fresh turn = fresh checklist only (no stacked plans from prior messages)
    setTaskPlans([]);
    liveReplyDraftRef.current = "";
    setLiveReplyDraft("");
    dispatchActivity({ type: "stream_start" });
    setStreaming(true);
    lean.start(runRequestId);
    setMention(null);
    // Drop prior-turn tool metadata so done-labels never inherit stale queries
    // (e.g. Python search label leaking into a later GTA+FIFA turn).
    toolInfoRef.current = {};
    const bootstrapStepId = `${runRequestId}:working`;
    /** Backend Turn id once create_execution emits turn_bound / final. */
    let durableTurnId = "";
      let finalHandled = false;
      let streamWasHidden = false;
    /** Mid-turn spoken beats already committed (so final doesn't re-add them). */
    const partialReplies: string[] = [];
    /** True once the first spoken mid-turn beat is sealed — tools must sort after it. */
    let sawPartialBeat = false;
    /** Floor timestamp for tool/search activity after the first partial. */
    let toolsAfterPartialAt = 0;
    /** Research runs + queries this turn — feed chat embeds under the final bubble. */
    const turnResearchRuns: ResearchRun[] = [];
    const turnSearchQueries: string[] = [];
    // Close any prior-Turn running chrome so B's tools never paint as A's open work.
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
      // Client turn key groups one thinking card. Backend request_id on events is preserved
      // for durable ToolRun correlation in stream payloads but must not spawn extra cards.
      const eventRequestId = (_evt?: { request_id?: string }) => runRequestId;
      const markThinkingStep = (
        evt: { request_id?: string },
        stepId: string,
        patch: Partial<ThinkingStep>,
        opts?: { toolName?: string; stepType?: ThinkingStep["type"] },
      ) => {
        const reqId = eventRequestId(evt);
        setActivities((prev) =>
          prev.map((p) => {
            if (p.kind !== "thinking" || p.request_id !== reqId || !p.steps?.length) return p;
            let matched = false;
            const steps = p.steps.map((s) => {
              if (s.id === stepId) {
                matched = true;
                return { ...s, ...patch };
              }
              return s;
            });
            // No name/type FIFO fallback — only exact ToolRun id may complete a row.
            if (!matched) return p;
            // Keep card under first partial beat if any.
            const nextAt = sawPartialBeat ? Math.max(p.at, toolsAfterPartialAt) : p.at;
            return { ...p, at: nextAt, steps };
          })
        );
      };

      const appendThinkingStep = (evt: { request_id?: string }, step: ThinkingStep) => {
        const reqId = eventRequestId(evt);
        // Ignore raw thought dumps — they duplicate the bootstrap spinner and clutter chat.
        if (step.type === "thought") {
          const short = "Working";
          setActivities((prev) =>
            prev.map((p) => {
              if (p.kind !== "thinking" || p.request_id !== reqId) return p;
              const steps = (p.steps || []).map((s) =>
                s.id === bootstrapStepId && s.status === "running"
                  ? { ...s, content: short.endsWith("…") ? short : `${short}…` }
                  : s
              );
              return { ...p, content: short, steps };
            })
          );
          return;
        }
        setActivities((prev) => {
          const existingIdx = prev.findIndex((p) => p.kind === "thinking" && p.request_id === reqId);
          if (existingIdx !== -1) {
            const updated = [...prev];
            const existing = updated[existingIdx] as Extract<ActivityItem, { kind: "thinking" }>;
            // Real work arrives → drop bootstrap so we don't stack "thinking…" + tool rows.
            let prevSteps = (existing.steps || []).filter((s) =>
              step.id === bootstrapStepId ? true : s.id !== bootstrapStepId
            );
            // Also drop post-partial placeholder once real tool/search steps land.
            prevSteps = prevSteps.filter((s) => s.id !== `${reqId}:post-partial-working`);
            // Upsert by exact ToolRun id only — never complete a different row by tool name/type.
            const byId = prevSteps.findIndex((s) => s.id === step.id);
            let nextSteps: ThinkingStep[];
            if (byId >= 0) {
              const existing = prevSteps[byId];
              // Terminal steps ignore trailing events (idempotent).
              if (
                (existing.status === "done" || existing.status === "failed") &&
                (step.status === "done" || step.status === "failed" || step.status === "running")
              ) {
                nextSteps = prevSteps;
              } else {
                nextSteps = prevSteps.map((s, i) => (i === byId ? { ...s, ...step } : s));
              }
            } else if (step.status === "done" || step.status === "failed") {
              // No open row with this id — append terminal (do not steal another running row).
              nextSteps = [...prevSteps, step];
            } else {
              // tool_start: drop only provisional request-scoped placeholders of same type
              // (ids like `${reqId}:search:...`), never another real ToolRun UUID.
              prevSteps = prevSteps.filter(
                (s) =>
                  !(
                    s.status === "running" &&
                    s.type === step.type &&
                    s.id !== step.id &&
                    String(s.id).startsWith(`${reqId}:`)
                  )
              );
              nextSteps = [...prevSteps, step];
            }
            // NEVER pull the card earlier (Math.min was pinning Search done above the first beat).
            // After a partial beat, stay strictly after that spoken message.
            const stepAt = step.at || Date.now();
            const floor = sawPartialBeat ? Math.max(toolsAfterPartialAt, existing.at) : existing.at;
            const nextAt = Math.max(floor, stepAt, sawPartialBeat ? toolsAfterPartialAt : 0);
            updated[existingIdx] = {
              ...existing,
              at: nextAt || stepAt,
              steps: nextSteps,
            };
            return updated;
          }
          return [
            ...prev,
            {
              kind: "thinking",
              id: crypto.randomUUID(),
              content: "",
              at: step.at || Date.now(),
              steps: [step],
              request_id: reqId,
            },
          ];
        });
      };

      const completeAllRunningSteps = (status: "done" | "failed" = "done") => {
        // Only this Turn's thinking card — never force-complete prior turns.
        // Provisional chrome ids (`${requestId}:…`) that never got a real tool_end must
        // be dropped, not force-completed as a second "Calculate done" / "Search done".
        setActivities((prev) =>
          prev.map((p) => {
            if (p.kind !== "thinking" || p.request_id !== runRequestId || !p.steps?.length) return p;
            const nextSteps = p.steps
              .map((s) => {
                if (s.status !== "running") return s;
                const id = String(s.id || "");
                const provisional = id.startsWith(`${runRequestId}:`);
                if (provisional && s.type !== "thought") {
                  // Drop orphan tool/search/read chrome — ToolRun UUID rows own those.
                  return null;
                }
                return {
                  ...s,
                  status,
                  content:
                    status === "failed" && !/fail/i.test(s.content)
                      ? `${s.content} — failed`
                      : s.content,
                };
              })
              .filter((s): s is NonNullable<typeof s> => s != null && Boolean(String(s.content || "").trim() || s.type === "thought"));
            return { ...p, steps: nextSteps };
          })
        );
      };
      const upsertTaskPlan = (evt: AgentStreamEvent) => {
        const reqId = eventRequestId(evt);
        // Always prefer "now" for plan placement so the checklist tracks the current turn
        // (backend `at` can lag or collide with older messages and pin the plan high up).
        const eventAt = Date.now();
        setTaskPlans((prev) => {
          if (evt.type !== "task_plan") return prev;
          const id = crypto.randomUUID();
          return [
            ...prev,
            {
              id,
              at: eventAt,
              request_id: reqId || runRequestId,
              plan: taskPlanReducer(createEmptyTaskPlan(), evt),
            },
          ];
        });
      };
      const upsertTool = (evt: AgentStreamEvent) => {
        if (evt.type === "tool_start") {
          // Scope tool metadata to this stream turn (avoid stale labels from prior turns)
          toolInfoRef.current[evt.id] = { name: evt.name, input: evt.input, requestId: runRequestId };
          const toolNameStart = String(evt.name || "").toLowerCase();
          if (toolNameStart === "terminal_run") {
            setAgentMode("coding");
          }
          // Only hide pure injects that fire almost every turn
          if (!SILENT_CHAT_TOOLS.has(toolNameStart)) {
            const stepAt = sawPartialBeat
              ? Math.max(Date.now(), toolsAfterPartialAt)
              : normalizeTimestampMs(evt.at || Date.now());
            appendThinkingStep(evt, {
              id: evt.id,
              type: toolActivityStepType(evt.name || ""),
              content: formatToolActivity(evt.name || "tool", "start", { input: evt.input }),
              status: "running",
              at: stepAt,
            });
          }
          return;
        }

        if (evt.type === "tool_end") {
          const info = toolInfoRef.current[evt.id];
          const toolFailed = evt.outcome?.success === false;
          // Ignore tool_end that belongs to another turn's metadata
          if (info && (info as { requestId?: string }).requestId && (info as { requestId?: string }).requestId !== runRequestId) {
            return;
          }
          const toolName = info?.name || evt.name || "tool";
          const toolNameLow = String(toolName || "").toLowerCase();
          if (SILENT_CHAT_TOOLS.has(toolNameLow)) {
            return;
          }
          // Outer fan-out / superseded shells — no second "Search done" timeline line.
          const outRaw = String(evt.output || "");
          if (
            toolNameLow === "web_search" &&
            (/\(expanded to /i.test(outRaw) || /\(superseded by canonical/i.test(outRaw))
          ) {
            markThinkingStep(
              evt,
              evt.id,
              { status: "done", content: "" },
              { toolName, stepType: toolActivityStepType(toolName) },
            );
            // Drop empty wrapper steps from the thinking card
            setActivities((prev) =>
              prev.map((p) => {
                if (p.kind !== "thinking" || p.request_id !== runRequestId || !p.steps) return p;
                return {
                  ...p,
                  steps: p.steps.filter((s) => s.id !== evt.id || Boolean(String(s.content || "").trim())),
                };
              })
            );
            return;
          }
          // Research panel still fed by web_search
          if (!toolFailed && toolNameLow === "web_search") {
            const normalized =
              normalizeResearchRun(evt.research) ||
              buildResearchRunFromToolEvent(
                evt.id,
                toolName,
                info?.input || "",
                evt.output || "",
                evt.at || Date.now()
              );
            if (normalized) {
              prependResearchRun(normalized);
              turnResearchRuns.push(normalized);
              if (normalized.query) turnSearchQueries.push(normalized.query);
            } else if (info?.input) {
              turnSearchQueries.push(String(info.input).replace(/\s+/g, " ").trim().slice(0, 120));
            }
          }
          // The durable ToolRun and specialist projections own code activity.
          // The stream only updates compact Chat status for coding activity.
          const codingTools = new Set([
            "file_write",
            "file_read",
            "file_delete",
            "file_move",
            "file_copy",
            "file_mkdir",
            "artifact_write",
            "terminal_run",
            "notepad_write",
            "checkpoint_undo",
          ]);
          if (codingTools.has(toolName)) {
            setAgentMode("coding");
          }
          // Unified done label: built-in, sports, MCP (mcp__server__tool), skills, …
          markThinkingStep(
            evt,
            evt.id,
            {
              status: toolFailed ? "failed" : "done",
              content: formatToolActivity(toolName, toolFailed ? "failed" : "done", {
                input: info?.input || "",
                output: evt.output || "",
                error: evt.outcome?.error_message || "",
              }),
            },
            { toolName, stepType: toolActivityStepType(toolName) },
          );
          return;
        }

        if (evt.type === "tool_error") {
          const info = toolInfoRef.current[evt.id];
          const toolName = info?.name || "tool";
          if (!SILENT_CHAT_TOOLS.has(String(toolName || "").toLowerCase())) {
            markThinkingStep(
              evt,
              evt.id,
              {
                status: "failed",
                content: formatToolActivity(toolName, "failed", {
                  input: info?.input || "",
                  error: evt.error,
                }),
              },
              { toolName, stepType: toolActivityStepType(toolName) },
            );
          }
          setEchoReaction("error");
        }
      };

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
                setLatestExecutionId(execId);
              }
              // The legacy bootstrap "thinking…" card is not part of a lean turn.
              setActivities((prev) => prev.filter((a) => !(a.kind === "thinking" && a.request_id === runRequestId)));
            }
            if (leanEvt.type === "memory_saved" && typeof leanEvt.memory_count === "number") {
              setMemoryCount(leanEvt.memory_count);
              continue;
            }
            if (leanEvt.type === "tool_start") setAgentMode(String(leanEvt.name || "").includes("search") ? "research" : "working");
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
            if (typeof leanEvt.memory_count === "number") setMemoryCount(leanEvt.memory_count);
            setStreaming(false);
            setAgentMode("idle");
            setEchoReaction(leanEvt.success ? "success" : "error");
            const spokenLean = String(committed[committed.length - 1]?.text || leanEvt.response || "").trim();
            if (spokenLean && (voiceReadAloud || voiceConversationMode)) {
              void speakLocalText(spokenLean, {
                clientTurnId: voiceTranscript?.clientTurnId || runRequestId,
                requestId: runRequestId,
                executionId: finalExecId,
                taskRunId: "",
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
          for (const action of activityActionsFromStreamEvent(evt as unknown as Record<string, unknown>)) {
            dispatchActivity(action);
          }

          if (evt.type === "turn_bound") {
            const execId = String(evt.execution_id || evt.turn_id || "").trim();
            if (execId) {
              durableTurnId = execId;
              activeExecutionIdsRef.current.set(streamThreadId, execId);
              setLatestExecutionId(execId);
            }
            dispatchActivity({ type: "turn_bound", objective: raw });
            const reasoningControl = evt.reasoning_control || {};
            if (
              thinkingEnabled &&
              reasoningControl.native_support === false &&
              reasoningEffort !== "medium"
            ) {
              dispatchActivity({
                type: "step_update",
                nextAction: reasoningControl.applied
                  ? "Using the selected effort as a bounded generation budget on this provider."
                  : "This provider does not expose native effort control on the active endpoint.",
              });
            }
          } else if (evt.type === "task_bound") {
            activeTaskRunIdsRef.current.set(streamThreadId, String(evt.task_run_id || ""));
          } else if (evt.type === "reasoning_summary") {
            const summary = String(evt.content || "").trim();
            if (thinkingEnabled && summary) {
              appendThinkingStep(evt, {
                id: `${runRequestId}:reasoning-summary:${evt.iteration || 0}`,
                type: "thought",
                content: summary,
                status: "done",
                at: normalizeTimestampMs(evt.at || Date.now()),
              });
            }
          } else if (evt.type === "provider_retry") {
            // A failed provider attempt is not a second assistant message.
            // Clear only the transient generation draft so the bounded retry
            // remains one continuous visible Echo run.
            if (evt.retrying) {
              liveReplyDraftRef.current = "";
              setLiveReplyDraft("");
            }
          } else if (evt.type === "recovery" || evt.type === "lifecycle" || evt.type === "iteration_boundary" || evt.type === "token_usage") {
            // The shared activity decoder above owns these semantic projections.
          } else if (evt.type === "task_plan") {
            upsertTaskPlan(evt);
          } else if (evt.type === "tool_start" || evt.type === "tool_end" || evt.type === "tool_error") {
            upsertTool(evt);
          } else if (evt.type === "thinking_step") {
            const stepType = (evt.step_type || "tool") as ThinkingStep["type"];
            const st = String(evt.status || "running").toLowerCase();
            const status: ThinkingStep["status"] =
              st === "failed" || st === "error" ? "failed" : st === "done" || st === "complete" ? "done" : "running";
            const content = String(evt.content || "").trim();
            // thinking_step is provisional chrome only. Never complete real ToolRun UUIDs
            // by type/name FIFO — tool_start/tool_end own ToolRun identity.
            if (stepType === "thought") {
              appendThinkingStep(evt, {
                id: `${eventRequestId(evt)}:thought`,
                type: "thought",
                content,
                status: "running",
                at: normalizeTimestampMs(evt.at || Date.now()),
              });
            } else {
              setActivities((prev) => {
                const idx = prev.findIndex((p) => p.kind === "thinking" && p.request_id === runRequestId);
                if (idx >= 0) {
                  const card = prev[idx] as Extract<ActivityItem, { kind: "thinking" }>;
                  const steps = [...(card.steps || [])];
                  // Only update provisional request-scoped placeholders (never ToolRun UUIDs).
                  const provisionalIdx = steps.findIndex(
                    (s) =>
                      s.status === "running" &&
                      s.type === stepType &&
                      String(s.id).startsWith(`${runRequestId}:`)
                  );
                  if (provisionalIdx >= 0 && status === "running") {
                    steps[provisionalIdx] = {
                      ...steps[provisionalIdx],
                      content: content || steps[provisionalIdx].content,
                      status: "running",
                    };
                    const next = [...prev];
                    next[idx] = { ...card, steps };
                    return next;
                  }
                  // Terminal thinking_step without ToolRun id: ignore (wait for tool_end).
                  if (status === "done" || status === "failed") {
                    return prev;
                  }
                  // No open tool row yet — provisional running row only
                  if (status === "running") {
                    const stableId = `${runRequestId}:${stepType}:${content.slice(0, 48)}`;
                    if (!steps.some((s) => s.id === stableId)) {
                      const floor = sawPartialBeat ? Math.max(toolsAfterPartialAt, card.at) : card.at;
                      const next = [...prev];
                      next[idx] = {
                        ...card,
                        at: Math.max(floor, normalizeTimestampMs(evt.at || Date.now())),
                        steps: [
                          ...steps.filter((s) => s.id !== bootstrapStepId && s.id !== `${runRequestId}:post-partial-working`),
                          {
                            id: stableId,
                            type: stepType,
                            content,
                            status: "running",
                            at: normalizeTimestampMs(evt.at || Date.now()),
                          },
                        ],
                      };
                      return next;
                    }
                  }
                } else if (status === "running") {
                  return [
                    ...prev,
                    {
                      kind: "thinking" as const,
                      id: crypto.randomUUID(),
                      content: "",
                      at: normalizeTimestampMs(evt.at || Date.now()),
                      request_id: runRequestId,
                      steps: [
                        {
                          id: `${runRequestId}:${stepType}:${content.slice(0, 48)}`,
                          type: stepType,
                          content,
                          status: "running" as const,
                          at: normalizeTimestampMs(evt.at || Date.now()),
                        },
                      ],
                    },
                  ];
                }
                return prev;
              });
            }
          } else if (evt.type === "agent_token") {
            const tok = String(evt.data || "");
            if (tok) {
              const prev = liveReplyDraftRef.current;
              const next = prev + tok;
              liveReplyDraftRef.current = next;
              // First token — remove bootstrap spinner (reply is the progress now).
              if (!prev) {
                setActivities((acts) =>
                  acts.map((p) => {
                    if (p.kind !== "thinking" || p.request_id !== eventRequestId(evt)) return p;
                    return {
                      ...p,
                      steps: (p.steps || []).filter((s) => s.id !== bootstrapStepId),
                    };
                  })
                );
              }
              setLiveReplyDraft(next);
            }
          } else if (evt.type === "partial_reply") {
            // Keep partial prose transient. The completed Turn is committed as
            // one assistant message when the final event arrives.
            const text = String(evt.response || liveReplyDraftRef.current || "").trim();
            if (!text) continue;
            if (partialReplies.some((p) => p.trim() === text)) continue;
            partialReplies.push(text);
            const beatAt = Date.now();
            sawPartialBeat = true;
            toolsAfterPartialAt = beatAt + 10;
            liveReplyDraftRef.current = text;
            setLiveReplyDraft(text);
            // Speak this beat now — tools may follow, then a second reply.
            if (evt.speak !== false && (voiceReadAloud || voiceConversationMode)) {
              void speakLocalText(text, {
                clientTurnId: voiceTranscript?.clientTurnId || runRequestId,
                requestId: runRequestId,
                executionId: durableTurnId,
                taskRunId: activeTaskRunIdsRef.current.get(streamThreadId) || "",
                completeTurn: false,
              });
            }
            const reqId = eventRequestId(evt);
            setActivities((prev) =>
              prev.map((p) => {
                if (p.kind !== "thinking" || p.request_id !== reqId) return p;
                const kept = (p.steps || []).filter(
                  (s) =>
                    s.id !== bootstrapStepId &&
                    s.id !== `${reqId}:post-partial-working` &&
                    (s.status === "done" || s.status === "failed" || s.status === "running") &&
                    !/^(thinking|thinking…)$/i.test(String(s.content || "").trim())
                );
                const hasToolWork = kept.some(
                  (s) => s.type === "search" || s.type === "tool" || s.type === "read"
                );
                return {
                  ...p,
                  at: toolsAfterPartialAt,
                  content: "working",
                  steps: hasToolWork
                    ? kept
                    : [
                        ...kept,
                        {
                          id: `${reqId}:post-partial-working`,
                          type: "tool" as const,
                          content: "checking…",
                          status: "running" as const,
                          at: toolsAfterPartialAt,
                        },
                      ],
                };
              })
            );
          } else if (evt.type === "thinking") {
            const content = (evt.content || "").trim();
            const reqId = eventRequestId(evt);
            if (content) {
              // Only nudge the single bootstrap label — never stack extra rows.
              setActivities((prev) =>
                prev.map((p) => {
                  if (p.kind !== "thinking" || p.request_id !== reqId) return p;
                  return {
                    ...p,
                    steps: (p.steps || []).map((s) =>
                      s.id === bootstrapStepId && s.status === "running"
                        ? { ...s, content: "thinking…" }
                        : s
                    ),
                  };
                })
              );
            }
          } else if (evt.type === "memory_saved") {
            setActivities((prev) => [
              ...prev,
              { kind: "memory", id: crypto.randomUUID(), memoryCount: evt.memory_count, at: Date.now() },
            ]);
            setMemoryCount(evt.memory_count);
            setEchoReaction("memory_saved");
            if (leftTab === "memory") {
            }
          } else if ((evt as any).type === "status" && (evt as any).agent_mode) {
            const mode = String((evt as any).agent_mode || "idle");
            setAgentMode(mode as any);
          } else if (evt.type === "error") {
            setStreaming(false);
            setLiveReplyDraft("");
            completeAllRunningSteps("failed");
            setActivities((prev) => [
              ...prev,
              { kind: "error", id: crypto.randomUUID(), message: evt.message, at: Date.now() },
            ]);
            setEchoReaction("error");
          } else if (evt.type === "final") {
            // Guard: stream can surface final more than once; never double-commit chat/TTS.
            if (finalHandled) continue;
            finalHandled = true;

            const liveDraft = liveReplyDraftRef.current.trim();
            const reply = mergeFinalReply(
              evt.response,
              liveDraft,
              partialReplies,
              Array.isArray(evt.partial_replies) ? evt.partial_replies : []
            );

            // Stale ownership: if the user switched Session/Project mid-stream,
            // keep durable backend history but do not paint the final into the wrong chat.
            // Own the Project captured at send time; compare against live refs (not stale closures).
            if (
              !canApplyFinalToChat({
                activeThreadId: String(activeThreadIdRef.current || ""),
                activeProjectId: String(activeProjectIdRef.current || ""),
                ownedThreadId: streamThreadId,
                ownedProjectId: streamProjectId,
                streamOpen: streamControllersRef.current.get(streamThreadId) === streamController,
              })
            ) {
              liveReplyDraftRef.current = "";
              setLiveReplyDraft("");
              setStreaming(false);
              continue;
            }

            if (evt.execution_id) {
              durableTurnId = String(evt.execution_id);
            }
            const executionStatus = String(evt.thread_state?.execution_status || "");
            // Only mark tool rows done when backend authority says complete — not on soft success.
            if (executionStatus === "complete" && evt.success) {
              completeAllRunningSteps("done");
            } else if (
              ["failed", "blocked", "retryable", "cancelled", "partially_complete", "in_progress", "needs_permission"].includes(
                executionStatus
              ) ||
              !evt.success
            ) {
              completeAllRunningSteps(executionStatus === "needs_permission" ? "done" : "failed");
            }
            if (typeof evt.memory_count === "number") {
              setMemoryCount(evt.memory_count);
            }
            setDocSources(Array.isArray(evt.doc_sources) ? evt.doc_sources : []);
            if (evt.thread_state) {
              setThreadState(evt.thread_state);
              setActiveProjectId(String(evt.thread_state.active_project_id || ""));
              setLatestExecutionId(String(evt.thread_state.last_execution_id || evt.execution_id || ""));
              setLatestTraceId(String(evt.thread_state.last_trace_id || evt.trace_id || ""));
            } else {
              if (evt.execution_id) setLatestExecutionId(String(evt.execution_id));
              if (evt.trace_id) setLatestTraceId(String(evt.trace_id));
            }
            if (Array.isArray(evt.research) && evt.research.length) {
              const finals = evt.research
                .map((item) => normalizeResearchRun(item))
                .filter((item): item is ResearchRun => Boolean(item));
              replaceResearchRuns(finals);
              for (const r of finals) {
                if (!turnResearchRuns.some((t) => t.id === r.id)) turnResearchRuns.push(r);
                if (r.query) turnSearchQueries.push(r.query);
              }
            }

            liveReplyDraftRef.current = "";
            setLiveReplyDraft("");
            setStreaming(false);

            if (reply) {
              const alreadyStreamed = liveDraft.length > 0;
              const ctxWindow = Number(providerInfo?.context_window || 0) || 32768;
              const renderPlan = buildResponseRenderPlan({
                answerText: reply,
                intent: evt.response_render,
                researchRuns: turnResearchRuns,
                searchQueries: turnSearchQueries,
              });
              const embeds = buildChatEmbeds({
                answerText: reply,
                researchRuns: turnResearchRuns,
                searchQueries: turnSearchQueries,
              });
              const finalExecId = String(evt.execution_id || durableTurnId || "").trim();
              // Scope Session thread_state actions to this execution only.
              const liveProjection = evt.execution_projection || {};
              const projectedChangedFiles = (Array.isArray(liveProjection.files_actually_changed)
                ? liveProjection.files_actually_changed
                : [])
                .flatMap((item: any) => [String(item?.path || "").trim(), String(item?.destination || "").trim()])
                .filter(Boolean);
              const liveOpState = evt.thread_state
                ? ({
                    ...evt.thread_state,
                    execution_status: String(liveProjection.status || evt.thread_state.execution_status || ""),
                    current_execution_id: finalExecId || evt.thread_state.current_execution_id,
                    last_execution_id: finalExecId || evt.thread_state.last_execution_id,
                    completed_actions: (evt.thread_state.completed_actions || []).filter(
                      (a: any) => !finalExecId || String(a?.execution_id || "") === finalExecId
                    ),
                    failed_actions: (evt.thread_state.failed_actions || []).filter(
                      (a: any) => !finalExecId || String(a?.execution_id || "") === finalExecId
                    ),
                    pending_actions: (evt.thread_state.pending_actions || []).filter(
                      (a: any) => !finalExecId || String(a?.execution_id || "") === finalExecId
                    ),
                    retry_target:
                      liveProjection.retry_target && typeof liveProjection.retry_target === "object"
                        ? liveProjection.retry_target
                        : {},
                    operation_details: {
                      ...(evt.thread_state.operation_details || {}),
                      files_changed: projectedChangedFiles,
                      memory_records: Array.isArray(liveProjection.memory_records)
                        ? liveProjection.memory_records.map((item: any) => String(item?.memory_id || item?.item_id || "")).filter(Boolean)
                        : [],
                    },
                  } as OperationalThreadState)
                : undefined;
              addMessage({
                id: crypto.randomUUID(),
                role: "assistant",
                text: reply,
                // Always after tools: toolsAfterPartialAt is 0 when no partial.
                at: Math.max(Date.now(), toolsAfterPartialAt + 1),
                skipTypewriter: alreadyStreamed || partialReplies.length > 0,
                streamBeat: "final",
                renderPlan,
                embeds: embeds.length ? embeds : undefined,
                executionId: finalExecId || undefined,
                clientRequestId: runRequestId,
                operation: liveOpState
                  ? {
                      state: liveOpState,
                      success: Boolean(evt.success),
                      executionId: finalExecId || undefined,
                    }
                  : undefined,
                docSources: Array.isArray(evt.doc_sources) ? evt.doc_sources : undefined,
                usage: buildMessageUsage(reply, useAppStore.getState().messages, ctxWindow, {
                  provider: providerInfo?.provider,
                  model: providerInfo?.model,
                }),
              });
              // The backend's spoken_text is the final remainder after any
              // already-spoken partial reply. Never fall back to the merged
              // display reply when a preamble has already been played.
              const spoken = (evt.spoken_text || "").trim();
              const speakVal = spoken || (partialReplies.length ? "" : reply);
              if (voiceReadAloud || voiceConversationMode) {
                const playbackScope = {
                  apiBase,
                  sessionId: String(activeThreadIdRef.current || ""),
                  projectId: String(activeProjectIdRef.current || ""),
                  clientTurnId: voiceTranscript?.clientTurnId || runRequestId,
                  requestId: runRequestId,
                  executionId: finalExecId,
                  taskRunId: activeTaskRunIdsRef.current.get(streamThreadId) || "",
                };
                const playback = speakVal
                  ? speakLocalText(speakVal, playbackScope)
                  : localVoicePlayback.complete(playbackScope)
                      .then(() => true)
                      .catch((error) => {
                        setVoicePhase("error");
                        setVoiceNotice(error instanceof Error ? error.message : "Voice playback completion could not be saved.");
                        return false;
                      });
                void playback.then((played) => {
                  if (
                    played &&
                    voiceConversationMode &&
                    activeThreadIdRef.current === streamThreadId &&
                    !streamControllersRef.current.has(streamThreadId)
                  ) {
                    void start();
                  }
                });
              }
            } else if (!partialReplies.length && !reply) {
              // True empty — still surface something so the turn doesn't ghost.
              addMessage({
                id: crypto.randomUUID(),
                role: "assistant",
                text: "(no response)",
                at: Date.now(),
                skipTypewriter: true,
                operation: evt.thread_state ? {
                  state: evt.thread_state,
                  success: Boolean(evt.success),
                  executionId: evt.execution_id,
                } : undefined,
              });
            }

            setEchoReaction(evt.success && executionStatus !== "needs_permission" ? "success" : evt.success ? null : "error");
            setAgentMode("idle");
            refreshPendingApproval(streamThreadId);
            refreshExecutions(streamThreadId);
            void loadCurrentWorkRuns(apiBase, streamThreadId, activeProjectIdRef.current || "");
            void refreshThreads();
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
      dispatchActivity({ type: "error", message: pretty });
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${pretty}`, at: Date.now() });
      setActivities((prev) => [
        ...prev,
        { kind: "error", id: crypto.randomUUID(), message: pretty, at: Date.now() },
      ]);
      setEchoReaction("error");
    } finally {
      const owned = ownsStreamCleanup(streamControllersRef.current.get(streamThreadId), streamController);
      const sameThread = isStreamThreadCurrent(streamThreadId, activeThreadIdRef.current);
      const aborted = streamController.signal.aborted;

      if (owned) {
        streamControllersRef.current.delete(streamThreadId);
        if (activeRequestIdsRef.current.get(streamThreadId) === runRequestId) {
          activeRequestIdsRef.current.delete(streamThreadId);
          activeExecutionIdsRef.current.delete(streamThreadId);
          activeTaskRunIdsRef.current.delete(streamThreadId);
        }
        setSessionInFlight(streamThreadId, false);
      }

      // A superseded controller owns no visible or durable projection cleanup.
      if (!owned) return;

      // Only the visible Session owns the current projection's phase machine.
      if (sameThread && aborted) {
        dispatchActivity({ type: "reset" });
      } else if (sameThread) {
        dispatchActivity({ type: "stream_end" });
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
      // If stream died without final but we already streamed tokens, promote draft once
      // only when the user did not cancel/abort mid-flight.
      if (!finalHandled && !aborted && liveReplyDraftRef.current.trim()) {
        const orphan = liveReplyDraftRef.current;
        const ctxWindow = Number(providerInfo?.context_window || 0) || 32768;
        addMessage({
          id: crypto.randomUUID(),
          role: "assistant",
          text: orphan,
          at: Date.now(),
          skipTypewriter: true,
          usage: buildMessageUsage(orphan, useAppStore.getState().messages, ctxWindow, {
            provider: providerInfo?.provider,
            model: providerInfo?.model,
          }),
        });
        // A stream without its canonical final event is incomplete. Keep the
        // visible recovered text, but do not speak it as if Echo finalized it.
      }
      liveReplyDraftRef.current = "";
      setLiveReplyDraft("");
      // An EOF without a final event is an interruption, never implicit success.
      // Only close running steps for THIS turn's thinking card.
      setActivities((prev) =>
        prev.map((p) => {
          if (p.kind !== "thinking" || p.request_id !== runRequestId || !p.steps?.length) return p;
          if (!p.steps.some((s) => s.status === "running")) return p;
          return {
            ...p,
            steps: p.steps.map((s) =>
              s.status === "running"
                ? { ...s, status: finalHandled && !aborted ? ("done" as const) : ("failed" as const) }
                : s
            ),
          };
        })
      );
      if (!finalHandled && streamThreadId && !aborted) {
        void refreshThreadState(streamThreadId);
        void refreshPendingApproval(streamThreadId);
        void refreshExecutions(streamThreadId);
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
  const toggleReadAloud = () => {
    const enabled = !voiceReadAloud;
    setVoiceReadAloud(enabled);
    if (!enabled) stopTts();
  };
  const toggleVoiceMode = () => {
    const enabled = !voiceConversationMode;
    setVoiceConversationMode(enabled);
    if (enabled && !streaming && !listening && voicePhase !== "transcribing") void start();
    if (!enabled) {
      void voiceInputRef.current?.stop(false);
      setListening(false);
      stopTts();
      setVoicePhase("idle");
      setVoiceNotice("");
    }
  };

  const toggleWakeWord = () => {
    setWakeWordEnabled((on) => {
      window.localStorage.setItem("echospeak.voice.wake", String(!on));
      if (on) setVoiceNotice("");
      return !on;
    });
  };
  // "Hey Echo": listen only while idle; release the mic during a voice turn,
  // while a reply streams or is read aloud, and when Wake is off.
  const wakeIdle = wakeWordEnabled && !listening && !streaming && (voicePhase === "idle" || voicePhase === "error");
  useEffect(() => {
    if (!wakeIdle) {
      wakeListenerRef.current?.stop();
      return;
    }
    if (!wakeListenerRef.current) wakeListenerRef.current = new WakeListener();
    const listener = wakeListenerRef.current;
    void listener
      .start({
        apiBase,
        onWake: () => {
          listener.stop();
          void start();
        },
        onUnavailable: (message) => {
          listener.stop();
          setWakeWordEnabled(false);
          window.localStorage.setItem("echospeak.voice.wake", "false");
          setVoiceNotice(message);
        },
      })
      .catch((error) => {
        setWakeWordEnabled(false);
        setVoiceNotice(error instanceof Error ? error.message : "The microphone is unavailable.");
      });
    return () => listener.stop();
  }, [wakeIdle, apiBase]); // eslint-disable-line react-hooks/exhaustive-deps

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

  const queueFollowUp = async () => {
    const sessionId = String(activeThreadIdRef.current || "").trim();
    const message = String(input || "").trim();
    if (!sessionId || !message) {
      textareaRef.current?.focus();
      return;
    }
    const response = await fetch(`${apiBase}/query/queue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: sessionId,
        message,
        client_request_id: crypto.randomUUID(),
      }),
    });
    if (!response.ok) return;
    setInput("");
    setUserIsTyping(false);
    dispatchActivity({ type: "recovery", reason: "Follow-up queued." });
  };

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
      setVoiceNotice("Stopped by Ty.");
      setInput("");
      return;
    }
    if (["canonical_steer", "canonical_continue", "canonical_inspect"].includes(transcript.controlHint)) {
      const taskRunId = activeTaskRunIdsRef.current.get(transcript.sessionId) || "";
      const requestId = activeRequestIdsRef.current.get(transcript.sessionId) || "";
      if (taskRunId && requestId) {
        const response = await fetch(`${apiBase}/query/steer`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            thread_id: transcript.sessionId,
            instruction: transcript.text,
            task_run_id: taskRunId,
            client_request_id: requestId,
            voice_turn_id: transcript.voiceTurnId,
          }),
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(String(payload?.detail || "The active run could not accept that direction."));
        }
        dispatchActivity({ type: "steer", instruction: transcript.text });
        setInput("");
        setVoicePhase("idle");
        setVoiceNotice(
          transcript.controlHint === "canonical_inspect"
            ? "Status question attached to the active run."
            : transcript.controlHint === "canonical_continue"
            ? "The active run will continue."
            : "Direction added to the active run.",
        );
        return;
      }
    }
    await sendText(transcript.text, transcript);
  };

  const start = async () => {
    const sessionId = String(activeThreadIdRef.current || "").trim();
    if (!sessionId || !voiceInputRef.current) {
      setVoicePhase("error");
      setVoiceNotice("Create or select a Session before using Voice.");
      return;
    }
    stopTts();
    setVoiceNotice("");
    try {
      await voiceInputRef.current.start(
        {
          apiBase,
          sessionId,
          projectId: String(activeProjectIdRef.current || ""),
        },
        {
          onPhase: (phase, detail) => {
            setVoicePhase(phase);
            setVoiceNotice(detail || "");
            setListening(phase === "listening" || phase === "requesting_permission");
          },
          onLevel: setVoiceInputLevel,
          onFinalTranscript: (transcript) => {
            setListening(false);
            setVoiceInputLevel(0);
            void submitVoiceTranscript(transcript).catch((error) => {
              setVoicePhase("error");
              setVoiceNotice(error instanceof Error ? error.message : "The spoken instruction could not be applied.");
            });
          },
          onFailure: (error) => {
            setListening(false);
            setVoiceInputLevel(0);
            setVoicePhase("error");
            setVoiceNotice(error.message || "Local transcription is unavailable.");
          },
        },
      );
    } catch (error) {
      setListening(false);
      setVoicePhase("error");
      setVoiceNotice(error instanceof Error ? error.message : "Local microphone capture is unavailable.");
    }
  };

  const stop = async () => {
    if (!voiceInputRef.current) return;
    setListening(false);
    try {
      const transcript = await voiceInputRef.current.stop(true);
      if (!transcript) return;
      await submitVoiceTranscript(transcript);
    } catch (error) {
      setVoicePhase("error");
      setVoiceNotice(error instanceof Error ? error.message : "Local transcription is unavailable.");
    } finally {
      setListening(false);
      setVoiceInputLevel(0);
    }
  };

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!event.ctrlKey || event.key.toLowerCase() !== "m") return;
      event.preventDefault();
      if (voiceInputRef.current?.active) void stop();
      else void start();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  });

  useEffect(() => {
    if (voiceInputRef.current?.active) void voiceInputRef.current.stop(false);
    setListening(false);
    setVoiceInputLevel(0);
    setVoicePhase("idle");
    setVoiceNotice("");
    stopTts();
  }, [activeThreadId, activeProjectId]);

  const refreshMonitor = async () => {
    try {
      setMonitorError(null);
      const resp = await fetchWithTimeout(`${apiBase}/vision/analyze`, { method: "POST" }, 6000);
      if (!resp.ok) {
        const t = await resp.text();
        throw new Error(t || `${resp.status} ${resp.statusText}`);
      }
      const data = (await resp.json()) as VisionAnalyzeResponse;
      setMonitorText(String(data?.text || ""));
      setMonitorAt(Date.now());
    } catch (e) {
      setMonitorError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    if (!initialHydrationComplete || !activeThreadId) return;
    refreshProviderInfo({ allowRetry: true });
  }, [apiBase, activeThreadId, initialHydrationComplete]);

  useEffect(() => {
  }, [activeProjectId, activeThreadId, leftTab]);

  useEffect(() => {
    const gatewayUrl = `${apiBase.replace(/^http/i, "ws")}/gateway/ws`;
    let disposed = false;

    const clearRetryTimer = () => {
      if (gatewayRetryTimerRef.current != null) {
        window.clearTimeout(gatewayRetryTimerRef.current);
        gatewayRetryTimerRef.current = null;
      }
    };

    const scheduleReconnect = () => {
      if (disposed || gatewayRetryTimerRef.current != null) return;
      const attempt = gatewayRetryAttemptRef.current + 1;
      gatewayRetryAttemptRef.current = attempt;
      const delay = Math.min(1000 * Math.pow(2, Math.max(0, attempt - 1)), 10000);
      gatewayRetryTimerRef.current = window.setTimeout(() => {
        gatewayRetryTimerRef.current = null;
        connectGateway();
      }, delay);
    };

    const connectGateway = () => {
      if (disposed) return;
      try {
        if (gatewaySocketRef.current) {
          try {
            gatewaySocketRef.current.close();
          } catch {
            // ignore
          }
          gatewaySocketRef.current = null;
        }

        const ws = createEchoSpeakWebSocket(gatewayUrl);
        gatewaySocketRef.current = ws;

        ws.onopen = () => {
          if (disposed) return;
          clearRetryTimer();
          gatewayRetryAttemptRef.current = 0;
          setDiscordGatewayConnected(true);
        };

        ws.onmessage = (evt: MessageEvent) => {
          if (disposed) return;
          let payload: GatewayEvent | null = null;
          try {
            payload = JSON.parse(String(evt.data || "")) as GatewayEvent;
          } catch {
            return;
          }
          if (!payload || typeof payload !== "object") return;

          if (payload.type === "gateway_ready") {
            setDiscordGatewayConnected(true);
            setDiscordGatewaySessionId(String(payload.session_id || ""));
            return;
          }

          if (payload.type === "discord_activity") {
            const at = normalizeTimestampMs(payload.at || Date.now());
            const tool = String(payload.tool || "unknown");
            const source = String(payload.source || "discord_bot");
            setDiscordLiveEvents((prev) => {
              const nextEvent: DiscordLiveEvent = {
                id: crypto.randomUUID(),
                kind: "activity",
                tool,
                source,
                at,
              };
              return [nextEvent, ...prev].slice(0, 25);
            });
            return;
          }

          if (payload.type === "spotify_playback") {
            setSpotifyPlaying({
              is_playing: !!payload.is_playing,
              track_id: String(payload.track_id || ""),
              track_name: String(payload.track_name || ""),
              track_artist: String(payload.track_artist || ""),
            });
            return;
          }

          if (payload.type === "error") {
            setDiscordLiveEvents((prev) => {
              const nextEvent: DiscordLiveEvent = {
                id: crypto.randomUUID(),
                kind: "error",
                message: String(payload.message || "Gateway error"),
                at: normalizeTimestampMs(payload.at || Date.now()),
              };
              return [nextEvent, ...prev].slice(0, 25);
            });
          }
        };

        ws.onerror = () => {
          if (disposed) return;
          setDiscordGatewayConnected(false);
        };

        ws.onclose = () => {
          if (disposed) return;
          setDiscordGatewayConnected(false);
          setDiscordGatewaySessionId("");
          setSpotifyPlaying(null);
          if (gatewaySocketRef.current === ws) {
            gatewaySocketRef.current = null;
          }
          scheduleReconnect();
        };
      } catch (e) {
        setDiscordGatewayConnected(false);
        scheduleReconnect();
      }
    };

    connectGateway();

    return () => {
      disposed = true;
      clearRetryTimer();
      setDiscordGatewayConnected(false);
      setDiscordGatewaySessionId("");
      if (gatewaySocketRef.current) {
        try {
          gatewaySocketRef.current.close();
        } catch {
          // ignore
        }
        gatewaySocketRef.current = null;
      }
    };
  }, [apiBase]);

  useEffect(() => {
    return () => {
      if (backendRetryRef.current.timer != null) {
        window.clearTimeout(backendRetryRef.current.timer);
        backendRetryRef.current.timer = null;
      }
    };
  }, []);

  useEffect(() => {
    if (backendOnline === false) return;
    if (providerDraft.provider === "openai") {
      setProviderModels(openaiModelOptions);
      return;
    }
    if (providerDraft.provider === "gemini") {
      setProviderModels(geminiModelOptions);
      return;
    }
    if (listableProviders.includes(providerDraft.provider)) {
      setProviderModels([]);
      refreshProviderModels(providerDraft.provider);
      return;
    }
    setProviderModels([]);
  }, [providerDraft.provider, backendOnline]);

  useEffect(() => {
    if (providerModels.length && (providerDraft.provider === "openai" || providerDraft.provider === "gemini" || listableProviders.includes(providerDraft.provider))) {
      if (!providerModels.includes(providerDraft.model)) {
        setProviderDraft((d) => ({ ...d, model: providerModels[0] }));
      }
    }
  }, [providerModels, providerDraft.provider, lmStudioOnly, switchingProvider]);

  useEffect(() => {
    if (lmStudioOnly) return;
    if (suppressAutoApplyRef.current) return;
    if (switchingProvider) return;

    const next = { provider: providerDraft.provider, model: providerDraft.model, base_url: providerDraft.base_url };
    const last = lastAppliedProviderRef.current;
    if (last && last.provider === next.provider && last.model === (next.model || "")) return;

    const t = window.setTimeout(() => {
      applyProviderSwitch(next);
    }, next.provider === "llama_cpp" ? 800 : 250);

    return () => window.clearTimeout(t);
  }, [providerDraft.provider, providerDraft.model, providerDraft.base_url, switchingProvider]);

  useEffect(() => {
    const listener = () => {
      void voiceInputRef.current?.stop(false);
      stopTts();
    };
    window.addEventListener("beforeunload", listener);
    return () => window.removeEventListener("beforeunload", listener);
  }, []);

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

  const showModelPicker =
    providerDraft.provider === "openai" ||
    providerDraft.provider === "gemini" ||
    providerModels.length > 0;
  const modelPickerOptions = showModelPicker
    ? (providerDraft.provider === "openai" ? openaiModelOptions : providerDraft.provider === "gemini" ? geminiModelOptions : providerModels)
    : [providerDraft.model || "Default model"];
  const modelPickerValue = showModelPicker ? providerDraft.model : modelPickerOptions[0];
  const studioOpen = desktopMode
    ? desktopSettingsOpen
    : leftTab !== "chat" && leftTab !== "research";
  const mediaWorkspaceOpen = !desktopMode && mediaRouteActive;
  const desktopContextualWorkspace = false;
  const activeWorkspaceLabel = desktopMode ? "Conversation" : "EchoSpeak";
  const activeChatTask = currentWorkRuns.find((run) =>
    !["completed", "cancelled", "superseded", "quarantined"].includes(String(run.status || "").toLowerCase())
  ) || null;
  const activeChatRequirementStates = activeChatTask
    ? Object.values(activeChatTask.requirement_statuses || {})
    : [];
  const activeChatSatisfied = activeChatRequirementStates.filter((status) => status === "satisfied").length;
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
  useEffect(() => savePanelWidth(panelWidth), [panelWidth]);
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
                  <div key={activeThreadId || "quick-chat"} className="chat-scroll" data-live={streaming ? "true" : undefined} style={{ flex: 1 }} ref={chatScrollRef} onScroll={onChatScroll}>
                    {activeRoom ? (
                      <RoomHeader room={activeRoom} agents={agents} onEdit={() => setRoomDialog({ open: true, room: activeRoom })} />
                    ) : null}
                    {activeChatTask ? (
                      <section style={{ margin: "4px 4px 12px", padding: "11px 13px", border: "1px solid rgba(255,255,255,.1)", background: "linear-gradient(115deg,rgba(255,255,255,.045),rgba(255,255,255,.012))", borderRadius: 5, display: "flex", alignItems: "center", gap: 12 }} aria-label="Current work">
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ color: "rgba(255,255,255,.38)", font: "600 8px ui-monospace,monospace", letterSpacing: ".12em", textTransform: "uppercase" }}>Current work · {String(activeChatTask.status || "running").replace(/_/g, " ")}</div>
                          <div style={{ marginTop: 5, color: "rgba(255,255,255,.86)", fontSize: 11.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{activeChatTask.objective}</div>
                          <div style={{ marginTop: 4, color: "rgba(255,255,255,.36)", fontSize: 9.5 }}>{activeChatSatisfied}/{activeChatRequirementStates.length || 1} requirements satisfied</div>
                        </div>
                      </section>
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
                    {/* Single Echo activity strip for the active Session stream only (legacy runtime). */}
                    {streaming && !lean.live ? (
                      <LiveChatActivityBar
                        activity={agentActivity}
                        showSpinner
                        onStop={() => {
                          stopTts();
                          setVoicePhase("idle");
                          setVoiceNotice("Stopped by Ty.");
                          cancelSessionTurn(activeThreadId, true);
                        }}
                        onSteer={activeTaskRunIdsRef.current.get(activeThreadId) ? () => setShowSteerModal(true) : undefined}
                        onQueue={() => void queueFollowUp()}
                        status={buildLiveOperationalStatus({
                          phase: agentActivity.phase,
                          streaming: true,
                          label: agentActivity.label,
                          activeToolName: agentActivity.activeToolName,
                          thinkingText: agentActivity.thinkingText,
                          taskDescription: (() => {
                            const plan =
                              taskPlans.find((entry) => entry.plan.active) ||
                              [...taskPlans].reverse().find((entry) => entry.plan.tasks.length);
                            const step =
                              plan?.plan.tasks.find((t) =>
                                ["running", "retrying", "awaiting_confirmation"].includes(t.status)
                              ) || plan?.plan.tasks.find((t) => t.status === "pending");
                            return step?.description || "";
                          })(),
                          searchHint: (() => {
                            const thinking = [...activities]
                              .reverse()
                              .find((a) => a.kind === "thinking") as Extract<ActivityItem, { kind: "thinking" }> | undefined;
                            const searchStep = [...(thinking?.steps || [])]
                              .reverse()
                              .find((s) => s.type === "search" && s.status === "running");
                            return searchStep?.content || "";
                          })(),
                        })}
                      />
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
                              setProviderDraft((d) => ({
                                ...d,
                                provider: p,
                                model:
                                  p === "openai"
                                    ? openaiModelOptions[0]
                                    : p === "gemini"
                                      ? geminiModelOptions[0]
                                      : providerModels[0] || d.model,
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

              {showSteerModal && (
                <div className="steer-backdrop" role="presentation">
                  <div className="steer-dialog" role="dialog" aria-modal="true" aria-labelledby="steer-dialog-title">
                    <div className="steer-dialog-copy">
                      <span>Active run</span>
                      <h3 id="steer-dialog-title">Guide Echo</h3>
                    </div>
                    <p>
                      Add a direction for Echo to use at the next safe boundary. Completed work stays intact.
                    </p>
                    <textarea
                      className="steer-input"
                      value={steerInput}
                      onChange={(e) => setSteerInput(e.target.value)}
                      placeholder="For example: focus on the Python files first"
                      rows={3}
                    />
                    <div className="steer-dialog-actions">
                      <button
                        className="steer-button"
                        type="button"
                        onClick={() => { setShowSteerModal(false); setSteerInput(""); }}
                      >
                        Cancel
                      </button>
                      <button
                        className="steer-button is-primary"
                        type="button"
                        disabled={!steerInput.trim() || steerSubmitting}
                        onClick={async () => {
                          if (!steerInput.trim() || !activeThreadId) return;
                          setSteerSubmitting(true);
                          try {
                            const res = await fetch(`${apiBase}/query/steer`, {
                              method: "POST",
                              headers: { "Content-Type": "application/json" },
                              body: JSON.stringify({
                                thread_id: activeThreadId,
                                instruction: steerInput.trim(),
                                task_run_id: activeTaskRunIdsRef.current.get(activeThreadId) || "",
                                client_request_id: activeRequestIdsRef.current.get(activeThreadId) || "",
                              }),
                            });
                            if (res.ok) {
                              dispatchActivity({ type: "steer", instruction: steerInput.trim() });
                              setShowSteerModal(false);
                              setSteerInput("");
                            }
                          } finally {
                            setSteerSubmitting(false);
                          }
                        }}
                      >
                        {steerSubmitting ? "Applying…" : "Apply direction"}
                      </button>
                    </div>
                  </div>
                </div>
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
                  onAvatarConfigChange={setAvatarConfig}
                />,
                desktopMode ? desktopStudioHost! : document.body
              )}
            </div>
          </div>
        </div>
      </div>
      <input
        type="file"
        ref={docInputRef}
        style={{ display: "none" }}
        onChange={(e) => setDocFile(e.target.files?.[0] || null)}
      />
    </div>
  );
};
