import React from "react";
import { EchoFace, echoFaceStyles, type EchoFaceMode } from "../components/EchoFace";
import { useAvatarConfig } from "./useAvatarConfig";
import { ActionIcon } from "./MessageActions";

export function VoiceStage({ apiBase, phase, notice, listening, speaking, streaming, native, level, tool, online, onListen, onPause, onEnd, onSettings }: {
  apiBase: string; phase: string; notice: string; listening: boolean; speaking: boolean; streaming: boolean;
  native: boolean; level: number; tool: string; online: boolean; onListen(): void; onPause(): void; onEnd(): void; onSettings(): void;
}) {
  const config = useAvatarConfig(apiBase);
  const status = phase === "error" ? "Let's check voice setup" : speaking ? "Echo is speaking" : streaming ? (tool ? "Working with you" : "Thinking it through") : listening ? "I'm listening" : phase === "transcribing" ? "Making sense of that" : phase === "requesting_permission" ? "Allow microphone access" : notice.startsWith("Microphone paused") ? "Microphone paused" : "Ready when you are";
  return <section className="voice-stage" aria-label="Voice conversation">
    <div className="voice-stage-heading"><span>Voice with Echo</span><small>{native ? "Gemini Live · native audio" : "Your selected model · voice conversation"}</small></div>
    <style>{echoFaceStyles}</style>
    <div className="voice-stage-avatar"><div style={{ transform: `scale(${config.voice_avatar_scale})` }}><EchoFace size="clamp(128px, 28vh, 280px)" avatarConfig={config} mode={(phase === "error" || !online ? "error" : speaking ? "speaking" : streaming ? (tool ? "working" : "thinking") : listening ? "listening" : "idle") as EchoFaceMode} /></div></div>
    <div className="voice-stage-status" role="status"><strong>{status}</strong><span>{notice || (tool ? tool.replace(/_/g, " ") : config.custom_status_text || "Your transcript and tool activity stay below.")}</span></div>
    <div className="voice-stage-controls">
      <button type="button" className={`es-btn ${listening ? "is-listening" : ""}`} aria-label={listening || speaking ? "Pause voice" : "Resume microphone"} onClick={listening || speaking ? onPause : onListen}><ActionIcon name={listening || speaking ? "stop" : "mic"} />{listening || speaking ? "Pause" : "Listen"}</button>
      <div className="voice-level" aria-label="Microphone level">{[0, 1, 2, 3, 4].map(n => <i key={n} style={{ height: `${6 + (listening ? Math.min(1, level) * (n === 2 ? 22 : n % 2 ? 16 : 10) : 0)}px` }} />)}</div>
      <button type="button" className="es-btn es-btn-quiet" onClick={onEnd}>Back to chat</button>
      {phase === "error" ? <button type="button" className="es-btn" onClick={onSettings}>Voice settings</button> : null}
    </div>
  </section>;
}
