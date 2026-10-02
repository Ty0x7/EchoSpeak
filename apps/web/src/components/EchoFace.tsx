import React, { useEffect, useRef, useState } from "react";

/**
 * Echo's face, drawn like the wordmark: a white rounded square with two dark
 * pill eyes. The eyes follow the pointer (or wander when it is still), Echo
 * blinks at random, and squints happily while a call-to-action is hovered.
 * Everything is still when the user prefers reduced motion.
 */
/** `size` is a px number or any CSS length (e.g. a clamp() so Echo scales on phones). */
export function EchoFace({ size = 280, className = "" }: { size?: number | string; className?: string }) {
  const faceRef = useRef<HTMLDivElement | null>(null);

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
      const hot = (event.target as Element | null)?.closest?.(".button, .header-install");
      face.dataset.mood = hot ? "happy" : "";
    };

    const tick = (now: number) => {
      if (now - lastMove > 3500) {
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
  }, []);

  return (
    <div className={`echo-face-wrap ${className}`} style={{ ["--face" as string]: typeof size === "number" ? `${size}px` : size }}>
      <div className="echo-face-float">
        <div className="echo-face" ref={faceRef} role="img" aria-label="Echo">
          <div className="echo-face-eyes" aria-hidden="true">
            <i />
            <i />
          </div>
        </div>
      </div>
      <div className="echo-face-shadow" aria-hidden="true" />
    </div>
  );
}

const LINES = [
  "Hi, I'm Echo.",
  "Want me to ask Scout?",
  "Forge can build that.",
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
  .echo-face-float { animation: echoFloat 6s ease-in-out infinite; }
  .echo-face {
    --look-x: 0;
    --look-y: 0;
    width: var(--face);
    height: var(--face);
    border-radius: 27%;
    background: #f4f4f2;
    box-shadow: inset 0 calc(var(--face) * .012) 0 #ffffff, inset 0 calc(var(--face) * -.02) 0 rgba(0,0,0,.06), 0 calc(var(--face) * .09) calc(var(--face) * .22) rgba(0,0,0,.55);
    display: grid;
    place-items: center;
    transform: rotateY(calc(var(--look-x) * 11deg)) rotateX(calc(var(--look-y) * -9deg));
    transform-style: preserve-3d;
    will-change: transform;
  }
  .echo-face-eyes {
    display: flex;
    gap: calc(var(--face) * .15);
    transform: translate(calc(var(--look-x) * var(--face) * .15), calc(var(--look-y) * var(--face) * .11));
  }
  .echo-face-eyes i {
    width: calc(var(--face) * .115);
    height: calc(var(--face) * .2);
    border-radius: 999px;
    background: #070707;
    transform-origin: 50% 60%;
    transition: transform 110ms ease, height 180ms ease, border-radius 180ms ease;
  }
  .echo-face[data-blink="true"] .echo-face-eyes i { transform: scaleY(.08); }
  .echo-face[data-mood="happy"] .echo-face-eyes i { height: calc(var(--face) * .09); border-radius: 999px 999px 40% 40%; transform: translateY(calc(var(--face) * -.03)); }
  .echo-face-shadow { position: absolute; bottom: 0; width: 62%; height: calc(var(--face) * .06); border-radius: 50%; background: rgba(0,0,0,.55); filter: blur(calc(var(--face) * .03)); animation: echoShadow 6s ease-in-out infinite; }
  @keyframes echoFloat { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(calc(var(--face) * -.045)); } }
  @keyframes echoShadow { 0%, 100% { transform: scaleX(1); opacity: .9; } 50% { transform: scaleX(.84); opacity: .55; } }
  .echo-says { margin: 0; min-height: 40px; overflow: hidden; padding: 10px 15px; border: 1px solid #2c2c2c; border-radius: 14px 14px 14px 4px; background: #111; color: #e6e6e2; font-size: 14px; font-weight: 550; letter-spacing: -.01em; box-shadow: 0 10px 30px rgba(0,0,0,.35); }
  .echo-says span { display: inline-block; animation: echoSay 3.6s ease both; }
  @keyframes echoSay { 0% { transform: translateY(120%); } 10%, 88% { transform: none; } 100% { transform: translateY(-120%); } }
  @media (prefers-reduced-motion: reduce) {
    .echo-face-float, .echo-face-shadow, .echo-says span { animation: none !important; }
    .echo-face { transform: none; }
  }
`;
