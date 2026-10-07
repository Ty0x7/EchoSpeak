// Moved out of index.tsx (10.0 split). Kept verbatim.
import React, { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { buildLiveOperationalStatus } from "../chatPresentation";
import { ResponseRenderer } from "../features/responseRenderer/ResponseRenderer";
import { ChatEmbeds, ChatEmbedFooter } from "../features/embeds/ChatEmbeds";
import { LeanMessage } from "../lean/LeanMessage";
import { type AgentActivityState } from "../agentActivity";
import { type ActivityItem, type Message, type ThinkingStep } from "./types";
import { estimateTokens, formatTokenCount } from "./toolDisplay";
import { colors } from "./runtime";
import { safeUrl } from "../widgets/validate";

export const SquareLoader: React.FC<{ size?: number; color?: string; active?: boolean }> = ({
  size = 12,
  color = "rgba(var(--es-ink-rgb), 0.88)",
  active = true,
}) => (
  <span
    aria-hidden
    style={{
      display: "inline-block",
      width: size,
      height: size,
      borderRadius: 2,
      border: `2px solid ${color}`,
      borderTopColor: "transparent",
      animation: active ? "echo-square-spin 0.75s linear infinite" : "none",
      verticalAlign: "middle",
      flexShrink: 0,
    }}
  />
);

/** Compact live strip for the active assistant turn — operational status and interrupt controls. */
export const LiveChatActivityBar: React.FC<{
  status: ReturnType<typeof buildLiveOperationalStatus>;
  activity: AgentActivityState;
  showSpinner: boolean;
  onStop?: () => void;
  onQueue?: () => void;
}> = ({ status, activity, showSpinner, onStop, onQueue }) => {
  const [expanded, setExpanded] = useState(true);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!activity.streaming || !activity.startTime) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [activity.startTime, activity.streaming]);
  const elapsedSeconds = activity.startTime
    ? Math.max(0, Math.floor((now - activity.startTime) / 1000))
    : 0;
  const chips: { key: string; label: string; value: string }[] = [];
  if (status.task) chips.push({ key: "task", label: "Task", value: status.task });
  if (status.tool) chips.push({ key: "tool", label: "Tool", value: status.tool });
  if (status.skill) chips.push({ key: "skill", label: "Skill", value: status.skill });
  if (status.search) chips.push({ key: "search", label: "Search", value: status.search });
  if (status.verifying) chips.push({ key: "verify", label: "Status", value: "Verifying" });
  const requirementRows = activity.requirements.slice(0, 5);
  const timelineRows = activity.timeline.slice(-4);
  return (
    <div className="live-run" data-testid="chat-live-activity" data-expanded={expanded ? "true" : "false"}>
      <div className="live-run-header">
        <div className="live-run-heading">
          <SquareLoader size={12} color="rgba(var(--es-ink-rgb), 0.9)" active={showSpinner} />
          <strong>{status.headline}</strong>
          <span className="live-run-meta">
            {elapsedSeconds}s{activity.iteration ? ` · pass ${activity.iteration}` : ""}
          </span>
        </div>
        {(onStop || onQueue) && (
          <div className="live-run-actions">
            {onStop && (
              <button
                className="live-run-action is-stop"
                type="button"
                onClick={onStop}
                title="Stop current agent turn"
                aria-label="Stop current turn"
              >
                Stop
              </button>
            )}
            {onQueue && (
              <button
                className="live-run-action"
                type="button"
                onClick={onQueue}
                title="Queue follow-up instruction"
                aria-label="Queue follow-up instruction"
              >
                Queue
              </button>
            )}
            <button
              className="live-run-action is-toggle"
              type="button"
              onClick={() => setExpanded((value) => !value)}
              title={expanded ? "Collapse run details" : "Expand run details"}
              aria-label={expanded ? "Collapse run details" : "Expand run details"}
            >
              {expanded ? "Less" : "More"}
            </button>
          </div>
        )}
      </div>
      {(activity.replyDraft || activity.thinkingText || activity.tokenUsage?.reasoning) ? (
        <div className="live-run-live-text" aria-live="polite">
          {activity.thinkingText ? (
            <div className="live-run-live-thought">
              <span>Thinking summary</span>
              <p>{activity.thinkingText}</p>
            </div>
          ) : activity.tokenUsage?.reasoning ? (
            <div className="live-run-live-thought">
              <span>Thinking</span>
              <p>Generating a private reasoning trace · {formatTokenCount(activity.tokenUsage.reasoning)} tokens</p>
            </div>
          ) : null}
          {activity.replyDraft ? (
            <div className="live-run-live-reply">
              <span>Reply</span>
              <p>{activity.replyDraft}</p>
            </div>
          ) : null}
        </div>
      ) : null}
      {expanded && (activity.objective || activity.activeRequirement || activity.activeModel || activity.tokenUsage || activity.recoveryReason || activity.nextAction || activity.requirements.length) ? (
        <div className="live-run-details">
          {activity.objective ? <div className="live-run-detail"><span>Objective</span>{activity.objective}</div> : null}
          {activity.activeRequirement ? <div className="live-run-detail"><span>Current step</span>{activity.activeRequirement}</div> : null}
          {activity.activeModel ? <div className="live-run-detail"><span>Model</span>{activity.activeModel}</div> : null}
          {activity.thinkingText ? <div className="live-run-detail"><span>Reasoning summary</span>{activity.thinkingText}</div> : null}
          {!activity.thinkingText && activity.tokenUsage?.reasoning ? <div className="live-run-detail"><span>Reasoning</span>Generating a private reasoning trace</div> : null}
          {activity.tokenUsage?.reasoning ? <div className="live-run-detail"><span>Thinking tokens</span>{formatTokenCount(activity.tokenUsage.reasoning)}{activity.tokenUsage.approximate ? " ~" : ""}</div> : null}
          {activity.tokenUsage?.completion ? <div className="live-run-detail"><span>Reply tokens</span>{formatTokenCount(activity.tokenUsage.completion)}{activity.tokenUsage.approximate ? " ~" : ""}</div> : null}
          {activity.tokenUsage?.total ? <div className="live-run-detail"><span>Total tokens</span>{formatTokenCount(activity.tokenUsage.total)}{activity.tokenUsage.approximate ? " ~" : ""}</div> : null}
          {activity.recoveryReason ? <div className="live-run-detail"><span>Recovery</span>{activity.recoveryReason}</div> : null}
          {activity.nextAction ? <div className="live-run-detail"><span>Next</span>{activity.nextAction}</div> : null}
        </div>
      ) : null}
      {expanded && (activity.attemptCount || activity.retryCount || activity.sourceCount || activity.missingFields.length) ? (
        <div className="live-run-measures" aria-label="Current run coverage">
          <span><b>{activity.attemptCount}</b> attempts</span>
          <span><b>{activity.retryCount}</b> retries</span>
          <span><b>{activity.sourceCount}</b> sources</span>
          <span><b>{activity.missingFields.length}</b> gaps</span>
        </div>
      ) : null}
      {expanded && requirementRows.length ? (
        <div className="live-run-requirements" aria-label="Task requirements">
          {requirementRows.map((requirement, index) => (
            <div key={`${requirement.label}:${index}`} data-status={requirement.status}>
              <i aria-hidden />
              <span>{requirement.label}</span>
              <small>{requirement.status.replace(/_/g, " ")}</small>
            </div>
          ))}
        </div>
      ) : null}
      {expanded && activity.sources.length ? (
        <div className="live-run-sources" aria-label="Sources used in this run">
          <span>Sources</span>
          <div>
            {activity.sources.slice(-4).map((source, index) => source.url ? (
              <a key={`${source.url}:${index}`} href={safeUrl(source.url) || undefined} target="_blank" rel="noreferrer">
                {source.label}
              </a>
            ) : (
              <small key={`${source.label}:${index}`}>{source.label}</small>
            ))}
          </div>
        </div>
      ) : null}
      {expanded && chips.length ? (
        <div className="live-run-trace">
          {chips.map((chip) => (
            <div key={chip.key}>
              <span>{chip.label}</span>
              <strong>{chip.value}</strong>
            </div>
          ))}
        </div>
      ) : null}
      {expanded && timelineRows.length ? (
        <div className="live-run-timeline" aria-label="Recent run activity">
          {timelineRows.map((entry) => (
            <div key={entry.key}>
              <i data-status={entry.status} aria-hidden />
              <span>{entry.label}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
};
export const SettingsGroupIcon: React.FC<{ name: string }> = ({ name }) => {
  const common = {
    width: 17,
    height: 17,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  if (name === "Models") return <svg {...common}><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9zM9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M20 9h3M1 15h3M20 15h3"/></svg>;
  if (name === "Search & Research") return <svg {...common}><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5M7.5 10.5h6M10.5 7.5v6"/></svg>;
  if (name === "Connections") return <svg {...common}><path d="M9.5 14.5 14.5 9M7 17l-1.5 1.5a3.5 3.5 0 0 1-5-5L5 9a3.5 3.5 0 0 1 5 0M17 7l1.5-1.5a3.5 3.5 0 0 1 5 5L19 15a3.5 3.5 0 0 1-5 0"/></svg>;
  if (name === "Voice & Speech") return <svg {...common}><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"/><path d="M5 11v1a7 7 0 0 0 14 0v-1M12 19v3M8 22h8"/></svg>;
  if (name === "Local Tools") return <svg {...common}><path d="M14.5 6.5 17.5 3.5a4 4 0 0 1-5 5L5 16l3 3 7.5-7.5a4 4 0 0 1 5-5l-3 3z"/></svg>;
  if (name === "Skills") return <svg {...common}><path d="M12 3 4 7v10l8 4 8-4V7zM8 9h8M8 13h5"/></svg>;
  if (name === "MCP") return <svg {...common}><rect x="3" y="5" width="7" height="6" rx="1"/><rect x="14" y="13" width="7" height="6" rx="1"/><path d="M10 8h4a3 3 0 0 1 3 3v2M14 16h-4a3 3 0 0 1-3-3v-2"/></svg>;
  if (name === "Privacy & Permissions") return <svg {...common}><path d="M12 3 4.5 6v5.5c0 4.5 3 7.8 7.5 9.5 4.5-1.7 7.5-5 7.5-9.5V6L12 3Z"/><path d="m9 12 2 2 4-4"/></svg>;
  if (name === "Advanced") return <svg {...common}><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></svg>;
  return <svg {...common}><path d="M4 5h16v14H4zM8 9h8M8 13h5"/></svg>;
};

export const Toggle = ({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) => {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "4px 0" }}>
      <span style={{ fontSize: 14, color: colors.text }}>{label}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: "0.06em", color: checked ? colors.accent : colors.textDim }}>
          {checked ? "ON" : "OFF"}
        </span>
        <button
          type="button"
          onClick={() => onChange(!checked)}
          style={{
            position: "relative",
            width: 44,
            height: 24,
            borderRadius: 12,
            background: checked ? "linear-gradient(135deg, rgba(45,108,255,0.8), rgba(45,108,255,0.6))" : "linear-gradient(135deg, rgba(var(--es-wash-rgb), calc(0.1 * var(--es-wash-k))), rgba(var(--es-wash-rgb), calc(0.05 * var(--es-wash-k))))",
            border: checked ? "1px solid rgba(140,180,255,0.4)" : "1px solid rgba(var(--es-edge-rgb), calc(0.15 * var(--es-edge-k)))",
            boxShadow: checked ? "0 2px 8px rgba(45,108,255,0.4), inset 0 1px 0 rgba(255,255,255,0.2)" : "inset 0 1px 2px rgba(var(--es-shade-rgb), calc(0.2 * var(--es-shade-k)))",
            cursor: "pointer",
            transition: "all 0.3s cubic-bezier(0.16, 1, 0.3, 1)",
            padding: 0,
            display: "flex",
            alignItems: "center",
          }}
        >
          <div
            style={{
              width: 18,
              height: 18,
              borderRadius: "50%",
              background: "var(--es-emph)",
              position: "absolute",
              left: checked ? 24 : 2,
              transition: "all 0.2s cubic-bezier(0.4, 0, 0.2, 1)",
              boxShadow: "0 1px 3px rgba(var(--es-shade-rgb), calc(0.2 * var(--es-shade-k)))",
            }}
          />
        </button>
      </div>
    </div>
  );
};

export const settingsSectionStyle: React.CSSProperties = {
  background: "rgba(var(--es-wash-rgb), calc(0.02 * var(--es-wash-k)))",
  border: "1px solid rgba(var(--es-edge-rgb), calc(0.08 * var(--es-edge-k)))",
  borderRadius: "12px",
  padding: "20px",
  marginBottom: "20px",
};

export const platformCardStyle: React.CSSProperties = {
  padding: 16,
  background: "linear-gradient(135deg, rgba(var(--es-wash-rgb), calc(0.05 * var(--es-wash-k))), rgba(var(--es-wash-rgb), calc(0.015 * var(--es-wash-k))))",
  borderRadius: 16,
  border: "1px solid rgba(var(--es-edge-rgb), calc(0.08 * var(--es-edge-k)))",
  boxShadow: "0 10px 30px -20px rgba(var(--es-shade-rgb), calc(0.55 * var(--es-shade-k))), inset 0 1px 0 rgba(255,255,255,0.04)",
};

export const PlatformHeader = ({
  icon,
  title,
  subtitle,
  accent,
}: {
  icon: string;
  title: string;
  subtitle: string;
  accent: string;
}) => (
  <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
    <div
      style={{
        width: 42,
        height: 42,
        borderRadius: 14,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 20,
        background: `${accent}22`,
        border: `1px solid ${accent}44`,
        boxShadow: `0 10px 30px -18px ${accent}`,
      }}
    >
      {icon}
    </div>
    <div>
      <div style={{ fontSize: 14, fontWeight: 700, color: colors.text }}>{title}</div>
      <div style={{ fontSize: 12, color: colors.textDim }}>{subtitle}</div>
    </div>
  </div>
);

/**
 * How full the model's context window is: a small ring and percentage in the
 * composer, with a card on hover or focus that explains the numbers.
 */
export const ContextMeter: React.FC<{ messages: Message[]; contextWindow: number; model?: string }> = ({ messages, contextWindow, model }) => {
  const [open, setOpen] = React.useState(false);
  if (!contextWindow || contextWindow <= 0) return null;
  const used = messages.reduce((sum, m) => sum + (m.usage?.tokens ?? estimateTokens(m.text)), 0);
  const pct = Math.min(used / contextWindow, 1);
  const shown = Math.round(pct * 100);
  const level = pct > 0.85 ? "full" : pct > 0.6 ? "filling" : "ok";
  const status = level === "full" ? "Nearly full" : level === "filling" ? "Filling up" : "Plenty of room";
  const r = 7;
  const circumference = 2 * Math.PI * r;
  return (
    <div
      className="ctx"
      data-level={level}
      tabIndex={0}
      aria-label={`Context ${shown}% used: about ${formatTokenCount(used)} of ${formatTokenCount(contextWindow)} tokens`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <svg className="ctx-ring" width="18" height="18" viewBox="0 0 18 18" aria-hidden>
        <circle cx="9" cy="9" r={r} className="ctx-track" />
        <circle cx="9" cy="9" r={r} className="ctx-fill" strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - Math.max(pct, 0.02))} transform="rotate(-90 9 9)" />
      </svg>
      <span className="ctx-pct">{shown}%</span>
      {open ? (
        <div className="ctx-card" role="tooltip">
          <div className="ctx-card-head">
            <strong>Context window</strong>
            <span className="ctx-badge">{status}</span>
          </div>
          <div className="ctx-big"><b>{shown}%</b> used{model ? <small>{model}</small> : null}</div>
          <div className="ctx-bar"><i style={{ width: `${Math.max(shown, 1)}%` }} /></div>
          <dl className="ctx-rows">
            <div><dt>Used</dt><dd>~{formatTokenCount(used)}</dd></div>
            <div><dt>Remaining</dt><dd>~{formatTokenCount(Math.max(0, contextWindow - used))}</dd></div>
            <div><dt>Window</dt><dd>{formatTokenCount(contextWindow)}</dd></div>
            <div><dt>Messages</dt><dd>{messages.length}</dd></div>
          </dl>
          <p>Tokens are estimated. When the chat gets long, older turns are summarized so the agent keeps the thread.</p>
        </div>
      ) : null}
    </div>
  );
};

import { MessageActions, type MessageActionProps } from "../dashboard/MessageActions";

export const ChatBubble: React.FC<{
  msg: Message;
  streaming?: boolean;
  typewriter?: boolean;
  onQuickReply?: (text: string) => void;
  contextWindow?: number;
  providerLabel?: string;
  modelLabel?: string;
  actions?: MessageActionProps;
}> = ({ msg, streaming, typewriter = false, onQuickReply, contextWindow = 0, providerLabel, modelLabel, actions }) => {
  const isUser = msg.role === "user";
  // Approval controls are rendered only from an exact backend approval record.
  const isConfirmPrompt = false;
  const [shown, setShown] = useState(isUser || !typewriter ? msg.text : "");
  const [metaHover, setMetaHover] = useState(false);

  useEffect(() => {
    if (isUser || !typewriter) {
      setShown(msg.text);
      return;
    }
    const target = msg.text || "";
    if (!target) {
      setShown("");
      return;
    }
    // Progressive reveal independent of backend generation speed.
    let i = 0;
    setShown("");
    const tick = window.setInterval(() => {
      i = Math.min(target.length, i + Math.max(2, Math.ceil(target.length / 80)));
      setShown(target.slice(0, i));
      if (i >= target.length) window.clearInterval(tick);
    }, 18);
    return () => window.clearInterval(tick);
  }, [msg.id, msg.text, isUser, typewriter]);

  if (!isUser && msg.lean) {
    return (
      <div style={{ width: "100%", minWidth: 0 }} data-testid="lean-message">
        <LeanMessage
          data={msg.lean}
          at={actions ? undefined : msg.at}
          onContinue={!streaming && onQuickReply ? () => onQuickReply(`@${msg.lean!.agent.name || "Echo"} continue where you left off.`) : undefined}
        />
        {actions ? <MessageActions message={msg} {...actions} disabled={streaming}
          meta={msg.at ? <time dateTime={new Date(msg.at).toISOString()}>{new Date(msg.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time> : null} /> : null}
      </div>
    );
  }

  const canQuickReply = Boolean(isConfirmPrompt && onQuickReply && !streaming);
  const bodyText = isUser || !typewriter ? msg.text : shown;
  const stillTyping = !isUser && typewriter && shown.length < (msg.text || "").length;
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      style={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        position: "relative",
        width: "100%",
        minWidth: 0,
        /* Extra right inset on user rows so text never kisses the scrollbar */
        padding: isUser ? "8px 2px 6px 0" : "8px 4px 6px",
        boxSizing: "border-box",
      }}
    >
      <div
        className={`chat-flat${isUser ? " chat-user-bubble-wrap" : ""}`}
        style={{
          position: "relative",
          maxWidth: isUser ? "94%" : "100%",
          width: isUser ? "auto" : "100%",
          minWidth: 0,
          color: colors.text,
          padding: "0",
          overflow: "visible",
          boxSizing: "border-box",
        }}
      >
        {isUser ? (
          <div className="chat-text chat-line-user" style={{ fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: 13.5 }}>
            {bodyText}
          </div>
        ) : (
          <div className="chat-line-assistant" style={{ minWidth: 0, maxWidth: "100%" }}>
            <ResponseRenderer plan={msg.renderPlan} fallbackText={bodyText} colors={colors} stillTyping={stillTyping} />
            {stillTyping ? (
              <span
                style={{
                  display: "inline-block",
                  width: 8,
                  height: 15,
                  marginLeft: 3,
                  borderRadius: 1,
                  background: "rgba(var(--es-wash-rgb), calc(0.75 * var(--es-wash-k)))",
                  animation: "pulse 0.8s infinite",
                  verticalAlign: "text-bottom",
                }}
              />
            ) : null}
            {!stillTyping && msg.embeds?.length ? (
              <ChatEmbeds embeds={msg.embeds} colors={colors} />
            ) : null}
          </div>
        )}

        {!isUser && isConfirmPrompt ? (
          <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
            <button
              onClick={() => onQuickReply?.("confirm")}
              disabled={!canQuickReply}
              style={{
                padding: "7px 12px",
                borderRadius: 2,
                border: `1px solid ${canQuickReply ? "rgba(var(--es-edge-rgb), calc(0.35 * var(--es-edge-k)))" : colors.line}`,
                background: "transparent",
                color: colors.text,
                cursor: canQuickReply ? "pointer" : "not-allowed",
                fontSize: 12,
                fontWeight: 600,
                fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                letterSpacing: "0.04em",
                textTransform: "uppercase",
              }}
            >
              Confirm
            </button>
            <button
              onClick={() => onQuickReply?.("cancel")}
              disabled={!canQuickReply}
              style={{
                padding: "7px 12px",
                borderRadius: 2,
                border: `1px solid ${colors.line}`,
                background: "transparent",
                color: colors.textDim,
                cursor: canQuickReply ? "pointer" : "not-allowed",
                fontSize: 12,
                fontWeight: 600,
                fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                letterSpacing: "0.04em",
                textTransform: "uppercase",
              }}
            >
              Cancel
            </button>
          </div>
        ) : null}

        {/* Time · tokens · ctx · sources: on the action row (copy, edit, retry…) when it shows, else on its own. */}
        {(() => {
          const metaRow = (() => {
            const msgTokens = msg.usage?.tokens ?? estimateTokens(msg.text);
            const ctxUsed = msg.usage?.contextUsed ?? msgTokens;
            const ctxWindow = msg.usage?.contextWindow || contextWindow || 32768;
            const ctxPct = ctxWindow > 0 ? Math.min(100, Math.round((ctxUsed / ctxWindow) * 100)) : 0;
            const prov = msg.usage?.provider || providerLabel || "";
            const model = msg.usage?.model || modelLabel || "";
            return (
              <div
                data-testid="chat-bubble-meta"
                style={{
                  marginTop: 0,
                  fontSize: 10,
                  color: "rgba(var(--es-ink-rgb), 0.28)",
                  fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                  letterSpacing: "0.06em",
                  textAlign: isUser ? "right" : "left",
                  display: "flex",
                  justifyContent: isUser ? "flex-end" : "flex-start",
                  alignItems: "center",
                  gap: 6,
                  position: "relative",
                  flexWrap: "wrap",
                  rowGap: 4,
                }}
              >
                <span data-testid="chat-meta-time">{new Date(msg.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
                <span style={{ opacity: 0.45 }}>·</span>
                <span
                  data-testid="chat-meta-tokens"
                  onMouseEnter={() => setMetaHover(true)}
                  onMouseLeave={() => setMetaHover(false)}
                  style={{
                    cursor: "default",
                    borderBottom: "1px dotted rgba(var(--es-edge-rgb), calc(0.18 * var(--es-edge-k)))",
                    paddingBottom: 1,
                  }}
                >
                  ~{formatTokenCount(msgTokens)} tok
                </span>
                {!isUser ? (
                  <>
                    <span style={{ opacity: 0.45 }}>·</span>
                    <span data-testid="chat-meta-ctx">{ctxPct}% ctx</span>
                  </>
                ) : null}
                {!isUser && !stillTyping ? (
                  <ChatEmbedFooter
                    embeds={msg.embeds}
                    colors={colors}
                    extraSources={Array.isArray(msg.docSources) ? msg.docSources.length : 0}
                  />
                ) : null}
                {metaHover && (
                  <div
                    style={{
                      position: "absolute",
                      bottom: "calc(100% + 8px)",
                      [isUser ? "right" : "left"]: 0,
                      background: "rgba(var(--es-surface-rgb), 0.96)",
                      border: "1px solid rgba(var(--es-edge-rgb), calc(0.14 * var(--es-edge-k)))",
                      borderRadius: 8,
                      padding: "9px 11px",
                      zIndex: 50,
                      boxShadow: "0 8px 24px rgba(var(--es-shade-rgb), calc(0.5 * var(--es-shade-k)))",
                      backdropFilter: "blur(12px)",
                      fontSize: 11,
                      color: colors.text,
                      lineHeight: 1.55,
                      minWidth: 168,
                      letterSpacing: "0.02em",
                      textAlign: "left",
                      whiteSpace: "nowrap",
                    }}
                  >
                    <div style={{ fontWeight: 700, color: "var(--es-text-strong)", marginBottom: 4, letterSpacing: "-0.02em" }}>
                      {isUser ? "Message" : "Response"} usage
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 18 }}>
                      <span style={{ color: colors.textDim }}>This bubble</span>
                      <span style={{ fontWeight: 600 }}>~{formatTokenCount(msgTokens)}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 18 }}>
                      <span style={{ color: colors.textDim }}>Context used</span>
                      <span style={{ fontWeight: 600 }}>~{formatTokenCount(ctxUsed)}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 18 }}>
                      <span style={{ color: colors.textDim }}>Window</span>
                      <span style={{ fontWeight: 600 }}>{formatTokenCount(ctxWindow)}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 18 }}>
                      <span style={{ color: colors.textDim }}>Fill</span>
                      <span style={{ fontWeight: 600 }}>{ctxPct}%</span>
                    </div>
                    {(prov || model) && (
                      <div style={{ marginTop: 6, paddingTop: 6, borderTop: "1px solid rgba(var(--es-edge-rgb), calc(0.08 * var(--es-edge-k)))", color: colors.textDim, fontSize: 10 }}>
                        {[prov, model].filter(Boolean).join(" · ")}
                      </div>
                    )}
                    <div style={{ marginTop: 4, fontSize: 9, color: "rgba(var(--es-ink-rgb), 0.28)" }}>
                      Estimates (chars ÷ 3.5)
                    </div>
                  </div>
                )}
              </div>
            );
          })();
          return actions && !stillTyping
            ? <MessageActions message={msg} {...actions} disabled={streaming} meta={metaRow} />
            : <div style={{ marginTop: 4, minWidth: 0, width: "100%" }}>{metaRow}</div>;
        })()}
      </div>
    </motion.div>
  );
};

export const ThinkingActivityCard: React.FC<{
  item: { kind: "thinking"; id: string; content: string; at: number; steps?: ThinkingStep[]; request_id?: string };
  /** Only one card owns the Echo spinner — other rows use static marks. */
  primarySpinner?: boolean;
}> = ({ item, primarySpinner = true }) => {
  // One clean list: drop pure thought dumps; keep at most one soft "thinking…" if nothing else yet.
  const rawSteps = item.steps || [];
  const workSteps = rawSteps.filter((s) => s.type !== "thought");
  const softPlaceholder = /^(understanding|planning|responding|thinking|waiting(?: for model)?|working|checking)(\s|\.|…)*$/i;
  // Soft placeholders are owned by LiveChatActivityBar — only real tool/search/task rows here.
  const steps: ThinkingStep[] = workSteps.filter(
    (s) => !softPlaceholder.test(String(s.content || "").trim())
  );
  const anyRunning = steps.some((s) => s.status === "running");
  const containerRef = useRef<HTMLDivElement>(null);
  // Prefer a single animated Echo mark on the newest running step (when this card owns spin).
  const primaryRunningId = [...steps].reverse().find((s) => s.status === "running")?.id;

  // No scrolling here: the chat view follows new content itself, and only while the
  // user is at the bottom (app/chatFollow.ts). This card used to pull the view down
  // from up to 240px away on every tool update, undoing a scroll-up to read.

  if (steps.length === 0) {
    return null;
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      style={{ display: "flex", justifyContent: "flex-start", width: "100%", padding: "2px 0 2px" }}
      ref={containerRef}
      data-testid="chat-thinking-activity"
    >
      <div className="chat-flat" style={{ width: "100%", maxWidth: "100%", color: colors.textDim }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {steps.map((step) => {
            const failed = step.status === "failed";
            const running = step.status === "running";
            const spinHere = primarySpinner && running && step.id === primaryRunningId;
            return (
              <div
                key={step.id}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 10,
                  fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                  fontSize: 12,
                  lineHeight: 1.5,
                  letterSpacing: "0.02em",
                  color: failed
                    ? "rgba(255,140,150,0.85)"
                    : running
                      ? "rgba(var(--es-ink-rgb), 0.72)"
                      : "rgba(var(--es-ink-rgb), 0.38)",
                }}
              >
                <span style={{ marginTop: 3, flexShrink: 0 }}>
                  {spinHere ? (
                    <SquareLoader size={9} color="rgba(var(--es-ink-rgb), 0.85)" active />
                  ) : failed ? (
                    <span
                      style={{
                        display: "inline-block",
                        width: 8,
                        height: 8,
                        borderRadius: 1,
                        background: "rgba(248,113,113,0.9)",
                      }}
                      title="failed"
                    />
                  ) : (
                    <span
                      style={{
                        display: "inline-block",
                        width: 7,
                        height: 7,
                        borderRadius: 1,
                        background: running ? "rgba(var(--es-ink-rgb), 0.55)" : "rgba(var(--es-ink-rgb), 0.28)",
                      }}
                    />
                  )}
                </span>
                <span style={{ flex: 1 }}>
                  {step.content}
                  {failed && !/fail/i.test(step.content) ? " — failed" : ""}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </motion.div>
  );
};

export const ActivityCard: React.FC<{ item: ActivityItem; primarySpinner?: boolean }> = ({ item, primarySpinner }) => {
  if (item.kind === "thinking") {
    return <ThinkingActivityCard item={item} primarySpinner={primarySpinner} />;
  }

  if (item.kind === "memory") {
    return (
      <motion.div
        layout
        initial={{ opacity: 0, y: 5 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.2 }}
        style={{ display: "flex", justifyContent: "flex-start", marginLeft: "0px", marginTop: "-6px", marginBottom: "4px" }}
      >
        <div style={{ fontSize: 11, color: "rgba(var(--es-ink-rgb), 0.4)", display: "flex", alignItems: "center", gap: 6, fontWeight: 500 }}>
          <span style={{ opacity: 0.7 }}>✓</span>
          <span>Memory saved ({item.memoryCount})</span>
        </div>
      </motion.div>
    );
  }

  if (item.kind === "error") {
    return (
      <motion.div
        layout
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.18 }}
        style={{ display: "flex", justifyContent: "flex-start", padding: "6px 0" }}
      >
        <div className="chat-flat" style={{ width: "100%", fontFamily: "'JetBrains Mono', ui-monospace, monospace" }}>
          <div style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--es-err-label)", marginBottom: 4 }}>
            error
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.55, color: "var(--es-err-soft)", whiteSpace: "pre-wrap" }}>{item.message}</div>
        </div>
      </motion.div>
    );
  }

  // Standalone tool activity items — flat digital lines (tools also appear in thinking steps).
  const body =
    item.status === "running"
      ? item.input || "running…"
      : item.output || (item.status === "error" ? "failed" : "done");
  const label = item.name || "tool";

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      style={{ display: "flex", justifyContent: "flex-start", padding: "1px 0 2px", width: "100%" }}
    >
      <div className="chat-flat" style={{ width: "100%", maxWidth: "100%" }}>
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 10,
            fontFamily: "'JetBrains Mono', ui-monospace, monospace",
            fontSize: 12,
            lineHeight: 1.5,
            letterSpacing: "0.02em",
            color: item.status === "running" ? "rgba(var(--es-ink-rgb), 0.7)" : "rgba(var(--es-ink-rgb), 0.38)",
          }}
        >
          <span style={{ marginTop: 3, flexShrink: 0 }}>
            {item.status === "running" ? (
              <SquareLoader size={9} color="rgba(var(--es-ink-rgb), 0.7)" />
            ) : (
              <span style={{ display: "inline-block", width: 7, height: 7, borderRadius: 1, background: "rgba(var(--es-wash-rgb), calc(0.28 * var(--es-wash-k)))" }} />
            )}
          </span>
          <span style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            <span style={{ color: "rgba(var(--es-ink-rgb), 0.45)" }}>{label}</span>
            {body ? `  ${String(body).slice(0, 240)}${String(body).length > 240 ? "…" : ""}` : ""}
          </span>
        </div>
      </div>
    </motion.div>
  );
};

export type ConfirmationCardProps = {
  action: any;
  riskLevel?: string;
  riskColor?: string;
  policyFlags?: string[];
  sessionPermissions?: Record<string, boolean>;
  dryRunAvailable?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onDryRun?: () => void;
};

export const ConfirmationCard: React.FC<ConfirmationCardProps> = ({
  action,
  riskLevel = "safe",
  riskColor = "#22c55e",
  policyFlags = [],
  sessionPermissions = {},
  dryRunAvailable = false,
  onConfirm,
  onCancel,
  onDryRun,
}) => {
  const [decisionBusy, setDecisionBusy] = React.useState(false);
  const toolName = action?.tool || "unknown";
  const kwargs = action?.kwargs || {};
  const safeArgumentEntries = Object.entries(kwargs).filter(([key]) =>
    !/(content|text|message|password|token|secret|api[_-]?key|credential)/i.test(key)
  );
  const runDecision = (fn: () => void) => {
    if (decisionBusy) return;
    setDecisionBusy(true);
    try {
      fn();
    } catch {
      setDecisionBusy(false);
    }
  };
  const permissionForFlag = (flag: string) => {
    const upper = String(flag || "").toUpperCase();
    if (upper === "ENABLE_SYSTEM_ACTIONS") return "system_actions";
    if (upper === "ALLOW_FILE_WRITE") return "file_write";
    if (upper === "ALLOW_TERMINAL_COMMANDS") return "terminal";
    if (upper === "ALLOW_DESKTOP_AUTOMATION") return "desktop";
    if (upper === "ALLOW_PLAYWRIGHT") return "playwright";
    return upper.toLowerCase();
  };
  const missingPolicyFlags = policyFlags.filter((flag) => sessionPermissions[permissionForFlag(flag)] === false);

  const riskLabels: Record<string, string> = {
    safe: "Safe",
    moderate: "Moderate Risk",
    destructive: "High Risk",
  };

  const riskBgColors: Record<string, string> = {
    safe: "rgba(34,197,94,0.12)",
    moderate: "rgba(245,158,11,0.12)",
    destructive: "rgba(239,68,68,0.12)",
  };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      style={{ display: "flex", justifyContent: "flex-start" }}
    >
      <div
        data-testid="approval-confirmation-card"
        data-approval-tool={toolName}
        style={{
          maxWidth: "96%",
          width: "fit-content",
          background: colors.panel2,
          color: colors.text,
          border: `1px solid ${colors.line}`,
          borderRadius: 14,
          padding: "14px 16px",
          boxShadow: `0 0 20px ${riskColor}15`,
          position: "relative",
          zIndex: 20,
        }}
      >
        {/* Header with risk badge */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
          <div
            style={{
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: 0.5,
              textTransform: "uppercase",
              padding: "4px 10px",
              borderRadius: 999,
              color: riskColor,
              background: riskBgColors[riskLevel] || riskBgColors.safe,
              border: `1px solid ${riskColor}50`,
            }}
          >
            {riskLabels[riskLevel] || "Safe"}
          </div>
          <div style={{ fontSize: 13, fontWeight: 650 }}>Confirm Action</div>
        </div>

        {/* Tool name */}
        <div style={{
          fontSize: 12,
          fontFamily: "ui-monospace, monospace",
          color: colors.accent,
          marginBottom: 8,
          padding: "6px 10px",
          background: "rgba(var(--es-shade-rgb), calc(0.2 * var(--es-shade-k)))",
          borderRadius: 6,
        }}>
          {toolName}
        </div>

        {/* Action details */}
        <div style={{ fontSize: 12.5, lineHeight: 1.6, color: colors.textDim, marginBottom: 10 }}>
          {safeArgumentEntries.map(([key, value]) => (
            <div key={key} style={{ marginBottom: 4 }}>
              <span style={{ color: colors.text, fontWeight: 500 }}>{key}:</span>{" "}
              <span style={{ wordBreak: "break-word" }}>
                {typeof value === "string" && value.length > 100
                  ? value.slice(0, 100) + "…"
                  : String(value)}
              </span>
            </div>
          ))}
        </div>

        {/* Policy flags */}
        {policyFlags.length > 0 && (
          <div style={{ fontSize: 10, color: colors.textDim, marginBottom: 10 }}>
            Requires: {policyFlags.join(", ")}
          </div>
        )}
        {missingPolicyFlags.length > 0 ? (
          <div style={{ fontSize: 10.5, color: "var(--es-warn)", marginBottom: 10 }}>
            Configuration required: {missingPolicyFlags.join(", ")}. This is an EchoSpeak policy block, not a detected Windows administrator or signature failure.
          </div>
        ) : null}

        {/* Session permissions */}
        <div style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          marginBottom: 12,
          fontSize: 10,
        }}>
          {Object.entries(sessionPermissions).map(([key, enabled]) => (
            <span
              key={key}
              style={{
                padding: "2px 6px",
                borderRadius: 4,
                background: enabled ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
                color: enabled ? "#22c55e" : "#ef4444",
              }}
            >
              {enabled ? "✓" : "✗"} {key}
            </span>
          ))}
        </div>

        {/* Action buttons */}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            data-testid="approval-confirm-button"
            onClick={() => runDecision(onConfirm)}
            disabled={missingPolicyFlags.length > 0 || decisionBusy}
            aria-busy={decisionBusy}
            style={{
              flex: 1,
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: 600,
              borderRadius: 8,
              border: "none",
              background: riskLevel === "destructive" ? "#ef4444" : colors.accent,
              color: "var(--es-text-strong)",
              cursor: missingPolicyFlags.length > 0 || decisionBusy ? "not-allowed" : "pointer",
              minWidth: 80,
              opacity: decisionBusy ? 0.7 : 1,
            }}
          >
            {decisionBusy ? "Working…" : "Confirm"}
          </button>
          {dryRunAvailable && onDryRun && (
            <button
              type="button"
              onClick={onDryRun}
              disabled={decisionBusy}
              style={{
                flex: 1,
                padding: "8px 16px",
                fontSize: 13,
                fontWeight: 600,
                borderRadius: 8,
                border: `1px solid ${colors.line}`,
                background: "transparent",
                color: colors.text,
                cursor: decisionBusy ? "not-allowed" : "pointer",
                minWidth: 80,
              }}
            >
              Dry Run
            </button>
          )}
          <button
            type="button"
            data-testid="approval-cancel-button"
            onClick={() => runDecision(onCancel)}
            disabled={decisionBusy}
            style={{
              flex: 1,
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: 600,
              borderRadius: 8,
              border: `1px solid ${colors.line}`,
              background: "transparent",
              color: colors.textDim,
              cursor: decisionBusy ? "not-allowed" : "pointer",
              minWidth: 80,
              opacity: decisionBusy ? 0.7 : 1,
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </motion.div>
  );
};

