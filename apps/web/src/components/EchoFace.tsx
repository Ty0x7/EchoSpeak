import React, { useEffect, useRef, useState } from "react";
import { normalizeAvatarConfig, type AvatarConfig } from "./avatarConfig";

export type EchoFaceMode = "idle" | "listening" | "thinking" | "speaking" | "working" | "error";

/**
 * Echo's face, drawn like the wordmark: a white rounded square with two dark
 * pill eyes. The eyes follow the pointer (or wander when it is still), Echo
 * blinks at random, and squints happily while a call-to-action is hovered.
 * Everything is still when the user prefers reduced motion.
 */
/** One-off reactions played when Echo's mode changes. */
type EchoReaction = "" | "perk" | "happy" | "shake";

function reactionFor(from: EchoFaceMode, to: EchoFaceMode): EchoReaction {
  if (to === "error") return from === "error" ? "" : "shake";
  if (to === "listening" && from !== "listening") return "perk";
  if (to === "idle" && (from === "speaking" || from === "thinking" || from === "working")) return "happy";
  return "";
}

/**
 * `size` is a px number or any CSS length (e.g. a clamp() so Echo scales on phones).
 * `aura` draws a glow in Echo's own shape behind him (the voice stage); `level`
 * (0–1, the microphone level) makes that glow and his eyes follow your voice.
 */
export function EchoFace({ size = 280, className = "", avatarConfig, mode = "idle", aura = false, level = 0 }: {
  size?: number | string; className?: string; avatarConfig?: Partial<AvatarConfig>; mode?: EchoFaceMode; aura?: boolean; level?: number;
}) {
  const appearance = avatarConfig ? normalizeAvatarConfig(avatarConfig) : null;
  const wander = !appearance || (appearance.enable_idle_activities && !["none", "breathe"].includes(appearance.idle_activity));
  const float = !appearance || (appearance.enable_idle_activities && appearance.idle_activity !== "none");
  const faceRef = useRef<HTMLDivElement | null>(null);
  const lastMode = useRef<EchoFaceMode>(mode);
  const [reaction, setReaction] = useState<EchoReaction>("");

  useEffect(() => {
    const next = reactionFor(lastMode.current, mode);
    lastMode.current = mode;
    if (!next) return;
    setReaction(next);
    const timer = window.setTimeout(() => setReaction(""), next === "happy" ? 1100 : 700);
    return () => window.clearTimeout(timer);
  }, [mode]);

  useEffect(() => {
    const face = faceRef.current;
    if (!face) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return;

    const target = { x: 0, y: 0 };
    const look = { x: 0, y: 0 };
    let lastMove = 0;
    let frame = 0;
    let blinkTimer = 0;

    const onMove = (event: PointerEvent) => {
      const rect = face.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      // Normalise by distance so the eyes still move when the pointer is far away.
      const reach = Math.max(window.innerWidth, window.innerHeight) * 0.42;
      target.x = Math.max(-1, Math.min(1, (event.clientX - cx) / reach));
      target.y = Math.max(-1, Math.min(1, (event.clientY - cy) / reach));
      lastMove = performance.now();
    };
    const onOver = (event: PointerEvent) => {
      const hot = (event.target as Element | null)?.closest?.(".button, .header-install, .btn, .header-download");
      face.dataset.mood = hot ? "happy" : "";
    };

    const tick = (now: number) => {
      if (now - lastMove > 3500 && wander) {
        // Idle: look around slowly on a lazy figure-eight.
        const t = now / 1000;
        target.x = Math.sin(t * 0.55) * 0.55;
        target.y = Math.sin(t * 0.9) * 0.25;
      }
      look.x += (target.x - look.x) * 0.12;
      look.y += (target.y - look.y) * 0.12;
      face.style.setProperty("--look-x", look.x.toFixed(4));
      face.style.setProperty("--look-y", look.y.toFixed(4));
      frame = requestAnimationFrame(tick);
    };

    const blink = () => {
      face.dataset.blink = "true";
      window.setTimeout(() => {
        face.dataset.blink = "";
        if (Math.random() < 0.22) {
          window.setTimeout(() => {
            face.dataset.blink = "true";
            window.setTimeout(() => (face.dataset.blink = ""), 120);
          }, 160);
        }
      }, 130);
      blinkTimer = window.setTimeout(blink, 2400 + Math.random() * 3600);
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("pointerover", onOver, { passive: true });
    frame = requestAnimationFrame(tick);
    blinkTimer = window.setTimeout(blink, 1800);
    return () => {
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerover", onOver);
      cancelAnimationFrame(frame);
      window.clearTimeout(blinkTimer);
    };
  }, [wander]);

  return (
    <div className={`echo-face-wrap ${className}`} data-mode={mode} data-react={reaction || undefined} style={{
      ["--face" as string]: typeof size === "number" ? `${size}px` : size,
      ["--echo-level" as string]: Math.max(0, Math.min(1, level)).toFixed(3),
      ...(appearance ? {
        ["--echo-body" as string]: appearance.body_color,
        ["--echo-eye" as string]: appearance.eye_color,
        ["--echo-round" as string]: `${Math.min(50, appearance.body_roundness * 1.93)}%`,
        ["--echo-eye-size" as string]: appearance.eye_size,
        ["--echo-glow" as string]: appearance.enable_glow ? appearance.glow_color : "transparent",
        ["--echo-duration" as string]: `${6 / appearance.breathing_speed}s`,
        ["--echo-float-play" as string]: float ? "running" : "paused",
      } : {}),
    }}>
      <div className="echo-face-float">
        {aura ? <div className="echo-face-aura" aria-hidden="true" /> : null}
        <div className="echo-face" ref={faceRef} role="img" aria-label={mode === "idle" ? "Echo" : `Echo ${mode}`}>
          <div className="echo-face-eyes" aria-hidden="true">
            <i />
            <i />
          </div>
          <span className="echo-face-sheen" aria-hidden="true" />
        </div>
      </div>
      <div className="echo-face-shadow" aria-hidden="true" />
    </div>
  );
}

const LINES = [
  "Hi, I'm Echo.",
  "Want me to ask Jarvis?",
  "Glados can build that.",
  "I run on your PC.",
  "@mention the whole team.",
];

/** A short speech bubble that cycles through what Echo can do. */
export function EchoSays() {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const timer = window.setInterval(() => setIndex((value) => (value + 1) % LINES.length), 3600);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <p className="echo-says" aria-live="off">
      <span key={index}>{LINES[index]}</span>
    </p>
  );
}

export const echoFaceStyles = `
  .echo-face-wrap { position: relative; width: var(--face); height: calc(var(--face) * 1.18); display: grid; justify-items: center; perspective: calc(var(--face) * 3.2); }
  .echo-face-float { position: relative; animation: echoFloat var(--echo-duration, 6s) ease-in-out infinite; animation-play-state: var(--echo-float-play, running); }
  .echo-face {
    --look-x: 0;
    --look-y: 0;
    position: relative;
    z-index: 1;
    overflow: hidden;
    width: var(--face);
    height: var(--face);
    border-radius: var(--echo-round, 27%);
    background: var(--echo-body, #f4f4f2);
    box-shadow: inset 0 calc(var(--face) * -.02) 0 rgba(0,0,0,.06), 0 calc(var(--face) * .09) calc(var(--face) * .22) rgba(0,0,0,.55);
    display: grid;
    place-items: center;
    transform: rotateY(calc(var(--look-x) * 11deg)) rotateX(calc(var(--look-y) * -9deg));
    transform-style: preserve-3d;
    transition: scale 260ms cubic-bezier(.3, 1.4, .5, 1);
    will-change: transform;
  }
  .echo-face-eyes {
    display: flex;
    gap: calc(var(--face) * .15);
    transform: translate(calc(var(--look-x) * var(--face) * .15), calc(var(--look-y) * var(--face) * .11));
    transition: translate 320ms ease, scale 120ms ease;
  }
  .echo-face-eyes i {
    width: calc(var(--face) * .115 * var(--echo-eye-size, 1));
    height: calc(var(--face) * .2 * var(--echo-eye-size, 1));
    border-radius: 999px;
    background: var(--echo-eye, #070707);
    transform-origin: 50% 60%;
    transition: transform 110ms ease, height 180ms ease, border-radius 180ms ease, scale 160ms ease, translate 180ms ease;
  }
  .echo-face[data-blink="true"] .echo-face-eyes i { transform: scaleY(.08); }
  .echo-face[data-mood="happy"] .echo-face-eyes i { height: calc(var(--face) * .09); border-radius: 999px 999px 40% 40%; transform: translateY(calc(var(--face) * -.03)); }
  .echo-face-shadow { position: absolute; bottom: 0; width: 62%; height: calc(var(--face) * .06); border-radius: 50%; background: rgba(0,0,0,.55); filter: blur(calc(var(--face) * .03)); animation: echoShadow var(--echo-duration, 6s) ease-in-out infinite; animation-play-state: var(--echo-float-play, running); }

  /* The aura: a glow in Echo's own shape (never a circle), behind him. */
  .echo-face-aura {
    position: absolute;
    inset: 0;
    z-index: 0;
    border-radius: var(--echo-round, 27%);
    background: var(--echo-aura, var(--echo-glow, #ffffff));
    filter: blur(calc(var(--face) * .16));
    opacity: var(--echo-aura-strength, .38);
    pointer-events: none;
    animation: echoAuraBreathe 4.8s ease-in-out infinite;
    transition: opacity 240ms ease, background 400ms ease;
  }
  /* A soft light sweep across the face while Echo is thinking or working. */
  .echo-face-sheen {
    position: absolute;
    top: -20%;
    bottom: -20%;
    left: -60%;
    width: 45%;
    background: linear-gradient(100deg, transparent, rgba(255, 255, 255, .8), transparent);
    opacity: 0;
    transform: skewX(-14deg);
    pointer-events: none;
  }

  /* Listening: Echo leans in, his eyes open wide and the glow follows your voice. */
  .echo-face-wrap[data-mode="listening"] .echo-face { scale: 1.03; }
  .echo-face-wrap[data-mode="listening"] .echo-face-eyes { scale: calc(1.06 + var(--echo-level) * .14) calc(1.1 + var(--echo-level) * .24); }
  .echo-face-wrap[data-mode="listening"] .echo-face-aura { animation: none; scale: calc(1.03 + var(--echo-level) * .16); opacity: calc(var(--echo-aura-strength, .38) * (1.05 + var(--echo-level) * .5)); transition: scale 90ms linear, opacity 90ms linear; }
  /* Thinking: eyes drift up and around, a slow tilt, a light sweep. */
  .echo-face-wrap[data-mode="thinking"] .echo-face { animation: echoTilt 2.6s ease-in-out infinite; }
  .echo-face-wrap[data-mode="thinking"] .echo-face-eyes { animation: echoPonder 2.6s ease-in-out infinite; }
  .echo-face-wrap[data-mode="thinking"] .echo-face-aura { animation: echoAuraBreathe 1.8s ease-in-out infinite; }
  .echo-face-wrap[data-mode="thinking"] .echo-face-sheen { animation: echoSheen 2.6s ease-in-out infinite; }
  /* Working (a tool is running): focused eyes that scan like reading, a busy bob. */
  .echo-face-wrap[data-mode="working"] .echo-face-float { animation: echoBusy .9s ease-in-out infinite; }
  .echo-face-wrap[data-mode="working"] .echo-face-eyes { animation: echoScan 1.5s ease-in-out infinite; }
  .echo-face-wrap[data-mode="working"] .echo-face-eyes i { scale: 1 .62; }
  .echo-face-wrap[data-mode="working"] .echo-face-aura { animation: echoAuraBreathe 1.1s ease-in-out infinite; }
  .echo-face-wrap[data-mode="working"] .echo-face-sheen { animation: echoSheen 1.5s ease-in-out infinite; }
  /* Speaking: a lively bob, eyes bounce with the words, the glow pulses along. */
  .echo-face-wrap[data-mode="speaking"] .echo-face-float { animation: echoSpeaking .7s ease-in-out infinite; }
  .echo-face-wrap[data-mode="speaking"] .echo-face-eyes i { animation: echoTalk .36s ease-in-out infinite alternate; }
  .echo-face-wrap[data-mode="speaking"] .echo-face-eyes i + i { animation-delay: .06s; }
  .echo-face-wrap[data-mode="speaking"] .echo-face-aura { animation: echoAuraPulse .7s ease-in-out infinite; }
  /* Error: a worried tilt and a warm red glow. */
  .echo-face-wrap[data-mode="error"] .echo-face-eyes { rotate: -8deg; translate: 0 calc(var(--face) * .03); }
  .echo-face-wrap[data-mode="error"] .echo-face-eyes i { scale: 1 .8; }
  .echo-face-wrap[data-mode="error"] .echo-face-aura { background: var(--echo-aura-error, #e5484d); animation: none; }

  /* One-off reactions when the mode changes. */
  .echo-face-wrap[data-react="perk"] .echo-face { animation: echoPerk .5s cubic-bezier(.3, 1.6, .5, 1); }
  .echo-face-wrap[data-react="happy"] .echo-face { animation: echoHop .9s cubic-bezier(.3, 1.5, .5, 1); }
  .echo-face-wrap[data-react="happy"] .echo-face-eyes i { height: calc(var(--face) * .09); border-radius: 999px 999px 40% 40%; translate: 0 calc(var(--face) * -.03); animation: none; }
  .echo-face-wrap[data-react="happy"] .echo-face-aura { animation: echoAuraPulse .45s ease-out 2; }
  .echo-face-wrap[data-react="shake"] .echo-face { animation: echoShake .5s ease-in-out; }

  @keyframes echoSpeaking { 0%,100% { transform: translateY(0) rotate(-1deg); } 50% { transform: translateY(calc(var(--face) * -.025)) rotate(1deg); } }
  @keyframes echoTalk { from { scale: 1 1; } to { scale: 1.06 .72; } }
  @keyframes echoPonder { 0%,100% { translate: calc(var(--face) * .03) calc(var(--face) * -.05); } 35% { translate: calc(var(--face) * -.035) calc(var(--face) * -.06); } 70% { translate: calc(var(--face) * .015) calc(var(--face) * -.02); } }
  @keyframes echoTilt { 0%,100% { rotate: 0deg; } 50% { rotate: 3deg; } }
  @keyframes echoScan { 0%,100% { translate: calc(var(--face) * -.05) calc(var(--face) * .01); } 50% { translate: calc(var(--face) * .05) calc(var(--face) * .01); } }
  @keyframes echoBusy { 0%,100% { transform: translateY(0); } 50% { transform: translateY(calc(var(--face) * -.018)); } }
  @keyframes echoSheen { 0%, 45% { left: -60%; opacity: 0; } 55% { opacity: .8; } 100% { left: 120%; opacity: 0; } }
  @keyframes echoAuraBreathe { 0%,100% { scale: 1.02; } 50% { scale: 1.1; opacity: calc(var(--echo-aura-strength, .38) * 1.25); } }
  @keyframes echoAuraPulse { 0%,100% { scale: 1.04; } 50% { scale: 1.16; opacity: calc(var(--echo-aura-strength, .38) * 1.5); } }
  @keyframes echoPerk { 0%,100% { translate: 0 0; } 40% { translate: 0 calc(var(--face) * -.06); } }
  @keyframes echoHop { 0%,100% { translate: 0 0; rotate: 0deg; } 30% { translate: 0 calc(var(--face) * -.08); rotate: -3deg; } 60% { translate: 0 0; rotate: 2deg; } 80% { translate: 0 calc(var(--face) * -.02); rotate: 0deg; } }
  @keyframes echoShake { 0%,100% { rotate: 0deg; } 20% { rotate: -5deg; } 40% { rotate: 4deg; } 60% { rotate: -3deg; } 80% { rotate: 2deg; } }
  @keyframes echoFloat { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(calc(var(--face) * -.045)); } }
  @keyframes echoShadow { 0%, 100% { transform: scaleX(1); opacity: .9; } 50% { transform: scaleX(.84); opacity: .55; } }
  .echo-says { margin: 0; min-height: 40px; overflow: hidden; padding: 10px 15px; border: 1px solid #2c2c2c; border-radius: 14px 14px 14px 4px; background: #111; color: #e6e6e2; font-size: 14px; font-weight: 550; letter-spacing: -.01em; box-shadow: 0 10px 30px rgba(0,0,0,.35); }
  .echo-says span { display: inline-block; animation: echoSay 3.6s ease both; }
  @keyframes echoSay { 0% { transform: translateY(120%); } 10%, 88% { transform: none; } 100% { transform: translateY(-120%); } }
  @media (prefers-reduced-motion: reduce) {
    .echo-face-float, .echo-face-shadow, .echo-says span, .echo-face-eyes, .echo-face-eyes i, .echo-face, .echo-face-aura, .echo-face-sheen { animation: none !important; }
    .echo-face { transform: none; }
  }
`;
