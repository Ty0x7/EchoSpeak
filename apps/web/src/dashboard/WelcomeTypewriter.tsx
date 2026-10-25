import React, { useEffect, useState } from "react";

const PHRASES = [
  "Chat with Echo, ask a question, or explore an idea.",
  "Write, research, and turn a thought into something useful.",
  "Build with your projects, plan tasks, and work through problems.",
];

const CODE_PHRASES = ["Build a feature, fix a bug, or explore your code.", "Review changes and work through ideas with your agent.", "Keep your project, tools, and progress in one workspace."];
export function WelcomeTypewriter({ coding = false }: { coding?: boolean }) {
  const phrases = coding ? CODE_PHRASES : PHRASES;
  const [reduced, setReduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [frame, setFrame] = useState({ phrase: 0, count: 0, deleting: false });
  useEffect(() => setFrame({ phrase: 0, count: 0, deleting: false }), [coding]);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (reduced) return;
    const text = phrases[frame.phrase];
    const delay = frame.deleting ? 18 : frame.count === text.length ? 2200 : frame.count === 0 ? 350 : 34;
    const timer = window.setTimeout(() => setFrame(current => {
      if (!current.deleting && current.count === text.length) return { ...current, deleting: true };
      if (current.deleting && current.count === 0) return { phrase: (current.phrase + 1) % phrases.length, count: 0, deleting: false };
      return { ...current, count: current.count + (current.deleting ? -1 : 1) };
    }), delay);
    return () => window.clearTimeout(timer);
  }, [frame, reduced, phrases]);
  return <p className="welcome-typewriter"><span className="welcome-accessible" style={{ position:"absolute", width:1, height:1, overflow:"hidden", clipPath:"inset(50%)", whiteSpace:"nowrap" }}>{phrases.join(" ")}</span><span aria-hidden="true">{reduced ? phrases[0] : phrases[frame.phrase].slice(0, frame.count)}{!reduced ? <i className="welcome-caret" /> : null}</span></p>;
}
