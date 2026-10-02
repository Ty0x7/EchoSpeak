import React, { useEffect, useState } from "react";
import { AgentAvatar } from "./LeanMessage";
import type { LeanLiveState, LeanSegment } from "./types";

/** What the active agent is doing right now, in a few words. */
export function describeLive(live: LeanLiveState): { agent?: { id: string; name: string; initials?: string }; activity: string } {
  if (live.routing && !live.order.length) return { activity: "Choosing who should answer" };
  const current = live.order.length ? live.messages[live.order[live.order.length - 1]] : undefined;
  if (!current) return { activity: "Connecting to the model" };
  const name = current.agent.name || "Echo";
  const last = current.segments[current.segments.length - 1] as LeanSegment | undefined;
  let activity = `${name} is starting`;
  if (last?.kind === "thinking") activity = `${name} is thinking`;
  else if (last?.kind === "tool" && last.status === "running") activity = last.label;
  else if (last?.kind === "approval" && !last.decision) activity = `${name} needs your OK`;
  else if (last?.kind === "text") activity = `${name} is writing`;
  else if (last) activity = `${name} is working`;
  return { agent: current.agent, activity };
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
  const { agent, activity } = describeLive(shown);
  const needsOk = activity.endsWith("needs your OK");
  return (
    <div className="lm-status" data-leaving={leaving ? "true" : "false"} data-attention={needsOk ? "true" : "false"} role="status" aria-live="polite">
      {agent ? <AgentAvatar id={agent.id} name={agent.name} initials={agent.initials} size={20} /> : <span className="lm-status-dot" aria-hidden />}
      <span className="lm-status-text lm-shimmer">{activity}</span>
      <span className="lm-status-time">{elapsed(Date.now() - shown.startedAt)}</span>
      <button type="button" className="lm-status-stop" onClick={onStop} title="Stop (Esc)" aria-label="Stop">
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden><rect x="1" y="1" width="8" height="8" rx="1.6" fill="currentColor" /></svg>
        <span>Stop</span>
      </button>
    </div>
  );
}
