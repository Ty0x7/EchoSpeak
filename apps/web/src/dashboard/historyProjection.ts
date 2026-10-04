import { normalizeResearchRun } from "../features/research/buildResearchRun";
import type { ResearchRun } from "../features/research/types";
import { buildResponseRenderPlan } from "../features/responseRenderer/buildResponseRenderPlan";
import { buildChatEmbeds } from "../features/embeds/buildChatEmbeds";
import type { OperationalThreadState } from "../features/operations/OperationalStateCard";
import { messageFromTimeline } from "../lean/liveReducer";
import type { LeanPersona } from "../lean/types";
import type { ActivityItem, Message, ProviderInfo } from "../app/types";
import { SILENT_CHAT_TOOLS, buildMessageUsage, previewToolInput } from "../app/toolDisplay";

export type HistoryProjection = { messages: Message[]; activities: ActivityItem[]; research: ResearchRun[] };

/** Rebuild a Session's chat from its durable turns (and ToolRuns when a turn omits them). Pure. */
export function projectSessionHistory(
  threadId: string,
  turns: any[],
  sessionToolRuns: any[],
  providerInfo: ProviderInfo | null,
  agents: LeanPersona[],
): HistoryProjection {
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
  return { messages: loadedMsgs, activities: loadedActs, research: hydratedResearch };
}
