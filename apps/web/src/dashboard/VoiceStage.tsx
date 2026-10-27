import React from "react";
import { EchoFace, echoFaceStyles, type EchoFaceMode } from "../components/EchoFace";
import { useAvatarConfig } from "./useAvatarConfig";
import { ActionIcon } from "./MessageActions";

export function VoiceStage({ apiBase, phase, notice, listening, speaking, streaming, level, tool, online, onListen, onPause, onEnd, onSettings }: {
  apiBase: string; phase: string; notice: string; listening: boolean; speaking: boolean; streaming: boolean;
  native: boolean; level: number; tool: string; online: boolean; onListen(): void; onPause(): void; onEnd(): void; onSettings(): void;
}) {
  const config = useAvatarConfig(apiBase);
  const status = phase === "error" ? "Let's check voice setup" : speaking ? "Echo is speaking" : streaming ? (tool ? "Working with you" : "Thinking it through") : listening ? "I'm listening" : phase === "transcribing" ? "Making sense of that" : phase === "requesting_permission" ? "Allow microphone access" : notice.startsWith("Microphone paused") ? "Microphone paused" : "Ready when you are";
  return <section className="voice-stage" aria-label="Voice conversation">
    <style>{echoFaceStyles}</style>
    <div className="voice-stage-avatar"><div style={{ transform: `scale(${config.voice_avatar_scale})` }}><EchoFace size="clamp(128px, 28vh, 280px)" avatarConfig={config} aura level={listening ? level : 0} mode={(phase === "error" || !online ? "error" : speaking ? "speaking" : streaming ? (tool ? "working" : "thinking") : listening ? "listening" : "idle") as EchoFaceMode} /></div></div>
    <span className="welcome-accessible" role="status">{status}</span>
    {phase === "error" ? <p className="voice-error" role="alert">{notice || "Voice is unavailable. Check microphone access and voice settings."}</p> : null}
    <div className="voice-stage-controls">
      <button type="button" className={`es-btn ${listening ? "is-listening" : ""}`} aria-label={listening || speaking ? "Pause voice" : "Resume microphone"} onClick={listening || speaking ? onPause : onListen}><ActionIcon name={listening || speaking ? "stop" : "mic"} />{listening || speaking ? "Pause" : "Listen"}</button>
      <button type="button" className="es-btn es-btn-quiet" onClick={onEnd}>Back to chat</button>
      {phase === "error" ? <button type="button" className="es-btn" onClick={onSettings}>Voice settings</button> : null}
    </div>
  </section>;
}
