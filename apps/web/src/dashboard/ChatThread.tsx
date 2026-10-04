import React from "react";
import { AnimatePresence } from "framer-motion";
import { OperationalStateCard } from "../features/operations/OperationalStateCard";
import { LeanMessage } from "../lean/LeanMessage";
import { RoomHeader } from "../lean/Dialogs";
import type { LeanLiveState, LeanPersona, LeanRoom } from "../lean/types";
import { ActivityCard, ChatBubble } from "../app/chatComponents";
import type { PendingActionEnvelope, ProviderInfo, ThreadSessionState, TimelineItem } from "../app/types";
import { stopTts } from "../app/runtime";

type ChatThreadProps = {
  activeThreadId: string;
  streaming: boolean;
  scrollRef: React.Ref<HTMLDivElement>;
  onScroll: React.UIEventHandler<HTMLDivElement>;
  onWheel: React.WheelEventHandler<HTMLDivElement>;
  onKeyDown: React.KeyboardEventHandler<HTMLDivElement>;
  onTouchStart: React.TouchEventHandler<HTMLDivElement>;
  onTouchEnd: React.TouchEventHandler<HTMLDivElement>;
  activeRoom: LeanRoom | null;
  agents: LeanPersona[];
  onEditRoom(room: LeanRoom): void;
  timeline: TimelineItem[];
  live: LeanLiveState | null;
  providerInfo: ProviderInfo | null;
  onQuickReply(text: string): void;
  pendingApproval: PendingActionEnvelope | null;
  threadState: ThreadSessionState | null;
  approvalDecisionBusy: boolean;
  onApprovalDecision(id: string, decision: "confirm" | "cancel"): void;
  onLeanApproval(id: string, decision: "allow" | "deny" | "always"): void;
};

/** The message timeline owns only presentation; Dashboard owns session changes. */
export function ChatThread({
  activeThreadId, streaming, scrollRef, onScroll, onWheel, onKeyDown, onTouchStart, onTouchEnd,
  activeRoom, agents, onEditRoom, timeline, live, providerInfo, onQuickReply,
  pendingApproval, threadState, approvalDecisionBusy, onApprovalDecision, onLeanApproval,
}: ChatThreadProps) {
  return (
    <div key={activeThreadId || "quick-chat"} className="chat-scroll" data-live={streaming ? "true" : undefined}
      style={{ flex: 1 }} ref={scrollRef} onScroll={onScroll} onWheel={onWheel} onKeyDown={onKeyDown}
      onTouchStart={onTouchStart} onTouchEnd={onTouchEnd} onTouchCancel={onTouchEnd}>
      {activeRoom ? <RoomHeader room={activeRoom} agents={agents} onEdit={() => onEditRoom(activeRoom)} /> : null}
      {!timeline.length && !streaming && !live ? (
        <div className="es-chat-empty">
          <strong>{activeRoom ? activeRoom.name : "What can I help with?"}</strong>
          <span>{activeRoom?.kind === "group"
            ? "Write to the whole group, or @mention an agent to pick who answers."
            : "Ask anything, or drop a folder on the composer to work inside a project."}</span>
        </div>
      ) : null}
      <AnimatePresence initial={false}>
        {timeline.map((item) => item.kind === "message" ? (
          <ChatBubble key={`msg-${item.id}`} msg={item.msg} streaming={streaming}
            typewriter={item.msg.role === "assistant" && !item.msg.skipTypewriter}
            contextWindow={Number(providerInfo?.context_window || 0) || 32768}
            providerLabel={providerInfo?.provider} modelLabel={providerInfo?.model}
            onQuickReply={(text) => { stopTts(); onQuickReply(text); }} />
        ) : (
          <ActivityCard key={`act-${item.id}`} item={item.item} primarySpinner={!streaming} />
        ))}
      </AnimatePresence>
      {pendingApproval?.has_pending && pendingApproval.action ? (
        <div style={{ width: "100%", padding: "2px 4px 4px", position: "relative", zIndex: 20 }}
          data-testid="chat-pending-approval"
          data-approval-id={String(pendingApproval.approval_id || pendingApproval.action.id || "")}>
          <OperationalStateCard state={threadState} approval={{
            ...pendingApproval.action,
            id: String(pendingApproval.approval_id || pendingApproval.action.id || ""),
            status: String(pendingApproval.action.status || "pending"),
            policy_flags: pendingApproval.policy_flags || pendingApproval.action.policy_flags,
            session_permissions: pendingApproval.session_permissions || pendingApproval.action.session_permissions,
          }} busy={approvalDecisionBusy} onDecision={onApprovalDecision} compact />
        </div>
      ) : null}
      {live ? (
        <div data-testid="lean-live-turn">
          {live.routing && !live.order.length ? <div className="lm-routing" aria-hidden><span className="lm-dots"><i /><i /><i /></span></div> : null}
          {live.order.map((id) => {
            const item = live.messages[id];
            return item ? <LeanMessage key={id} data={item} live onDecide={onLeanApproval} at={item.startedAt} /> : null;
          })}
          {!live.order.length && !live.routing ? <div className="lm-routing" aria-hidden><span className="lm-dots"><i /><i /><i /></span></div> : null}
        </div>
      ) : null}
    </div>
  );
}
