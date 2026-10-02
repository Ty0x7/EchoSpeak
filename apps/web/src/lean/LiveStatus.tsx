import React, { useEffect, useState } from "react";
import { AgentAvatar } from "./LeanMessage";
import type { LeanAgentRef, LeanLiveState, LeanMessageData, LeanSegment } from "./types";

function activityOf(msg: LeanMessageData): string {
  const name = msg.agent.name || "Echo";
  const last = msg.segments[msg.segments.length - 1] as LeanSegment | undefined;
  if (last?.kind === "thinking") return `${name} is thinking`;
  if (last?.kind === "tool" && last.status === "running") return last.label;
  if (last?.kind === "approval" && !last.decision) return `${name} needs your OK`;
  if (last?.kind === "text") return `${name} is writing`;
  if (last) return `${name} is working`;
  return msg.role === "merge" ? `${name} is summarizing` : `${name} is starting`;
}

const joinNames = (names: string[]) =>
  names.length <= 2 ? names.join(" and ") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;

/** What the active agent(s) are doing right now, in a few words. */
export function describeLive(live: LeanLiveState): { agents: LeanAgentRef[]; activity: string; needsOk: boolean } {
  if (live.routing && !live.order.length) return { agents: [], activity: "Choosing who should answer", needsOk: false };
  const all = live.order.map((id) => live.messages[id]).filter(Boolean);
  if (!all.length) return { agents: [], activity: "Connecting to the model", needsOk: false };
  const working = all.filter((m) => m.status === "streaming");
  const needsOk = working.some((m) => {
    const last = m.segments[m.segments.length - 1];
    return last?.kind === "approval" && !last.decision;
  });
  // Several agents answering at once (a fan-out).
  if (working.length > 1) {
    const waiting = working.find((m) => activityOf(m).endsWith("needs your OK"));
    return {
      agents: working.map((m) => m.agent),
      activity: waiting ? activityOf(waiting) : `${joinNames(working.map((m) => m.agent.name || "Echo"))} are working`,
      needsOk,
    };
  }
  const current = working[0] || all[all.length - 1];
  const activity = activityOf(current);
  return {
    agents: [current.agent],
    // Make a handoff visible: "Echo → Scout · Searching …".
    activity: current.delegatedBy ? `${current.delegatedBy} → ${current.agent.name} · ${activity}` : activity,
    needsOk,
  };
}

const elapsed = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

/** Status pill pinned above the input bar while a turn is running. */
export function LiveStatusPill({ live, onStop }: { live: LeanLiveState | null; onStop(): void }) {
  const [, tick] = useState(0);
  const [shown, setShown] = useState<LeanLiveState | null>(live);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    if (live) {
      setShown(live);
      setLeaving(false);
      return;
    }
    if (!shown) return;
    setLeaving(true);
    const timer = window.setTimeout(() => {
      setShown(null);
      setLeaving(false);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [live]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => tick((v) => v + 1), 1000);
    return () => window.clearInterval(timer);
  }, [live]);

  if (!shown) return null;
  const { agents, activity, needsOk } = describeLive(shown);
  return (
    <div className="lm-status" data-leaving={leaving ? "true" : "false"} data-attention={needsOk ? "true" : "false"} role="status" aria-live="polite">
      {agents.length ? (
        <span className="es-stack">
          {agents.slice(0, 4).map((agent) => (
            <AgentAvatar key={agent.id} id={agent.id} name={agent.name} initials={agent.initials} size={20} />
          ))}
        </span>
      ) : (
        <span className="lm-status-dot" aria-hidden />
      )}
      <span className="lm-status-text lm-shimmer">{activity}</span>
      <span className="lm-status-time">{elapsed(Date.now() - shown.startedAt)}</span>
      <button type="button" className="lm-status-stop" onClick={onStop} title="Stop (Esc)" aria-label="Stop">
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden><rect x="1" y="1" width="8" height="8" rx="1.6" fill="currentColor" /></svg>
        <span>Stop</span>
      </button>
    </div>
  );
}
