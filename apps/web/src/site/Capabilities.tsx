import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { EchoFace } from "../components/EchoFace";
import { Face, Icon } from "./Chrome";
import "./capabilities.css";

function Stage({ id, children, className = "" }: { id: string; children: React.ReactNode; className?: string }) {
  const ref = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!ref.current || typeof IntersectionObserver === "undefined") { setVisible(true); return; }
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { rootMargin: "80px" });
    observer.observe(ref.current); return () => observer.disconnect();
  }, []);
  return <section ref={ref} id={id} className={`cap-stage shell ${className}`} data-in-view={visible}>{children}</section>;
}

export function CreationImage() {
  return <div className="creation-photo" role="img" aria-label="A sculptural glass and metal object, photographed in black and white." />;
}
export function CreationsStory() {
  return <Stage id="creations" className="cap-creations">
    <div className="cap-copy"><span className="kicker">Meet Creations</span><h2>An idea.<br />An image.<br /><span className="dim">A place to keep it.</span></h2><p>Describe the scene you’re imagining. Echo brings your selected image or video model into the conversation, then keeps the result in your library.</p><div className="cap-feature-list"><span><Icon name="check" size={14} /> Create with local or cloud models</span><span><Icon name="check" size={14} /> See the result right in chat</span><span><Icon name="check" size={14} /> Save it. Find it. Make something next.</span></div><Link className="text-link" to="/docs/creations">Inside Creations <Icon name="arrow" size={15} /></Link></div>
    <div className="cap-studio"><div className="cap-studio-top"><span><Face size={18} /> Creations</span><span className="cap-small-label">Sample image</span></div>
      <div className="cap-creation-portrait"><CreationImage /></div>
    </div>
  </Stage>;
}

const setupSteps = [
  { name: "Choose your model", icon: "model" as const, detail: "Connect a model on your computer or bring an OpenAI, Gemini, Claude, or Grok API key. Check the connection before your first chat." },
  { name: "Choose your tools", icon: "research" as const, detail: "Prepare web search and, if you want to build software, the existing sandbox. Echo works with the tools you enable." },
  { name: "Make it yours", icon: "voice" as const, detail: "Add voice and creation models when you’re ready. Optional downloads start when you choose to install them." },
];
export function SetupStory() {
  const [active, setActive] = useState(0);
  return <Stage id="setup" className="cap-setup"><div className="cap-setup-demo"><div className="cap-setup-lines" aria-hidden="true"><i /><i /><i /></div><div className="cap-setup-window"><Face size={40} /><span className="kicker">Your first few minutes</span><h3>Hey. Let’s get you started.</h3><div className="cap-setup-options" role="group" aria-label="Setup preview">{setupSteps.map((step, index) => <button key={step.name} type="button" aria-pressed={active === index} onClick={() => setActive(index)}><span>0{index + 1}</span><Icon name={step.icon} size={18} /><strong>{step.name}</strong><Icon name="arrow" size={15} /></button>)}</div><p className="cap-setup-detail" key={active}>{setupSteps[active].detail}</p><span className="cap-setup-later">Optional steps can wait.</span></div><div className="cap-echo-peek" aria-hidden="true"><EchoFace size={75} /></div></div>
    <div className="cap-copy"><span className="kicker">A warmer welcome</span><h2>Less setting up.<br /><span className="dim">More saying hello.</span></h2><p>A guide for your first launch. Pick your model, check your tools, and get into a conversation. Come back to the extras whenever you’re ready.</p><div className="cap-feature-list"><span><Icon name="check" size={14} /> Detect what’s already running</span><span><Icon name="check" size={14} /> Keep optional features optional</span><span><Icon name="check" size={14} /> Reopen setup from Settings</span></div><Link className="text-link" to="/docs/getting-started">Get to know Echo <Icon name="arrow" size={15} /></Link></div></Stage>;
}

export function ScrollProgress() {
  const progress = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let frame = 0;
    const update = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => { const total = document.documentElement.scrollHeight - window.innerHeight; if (progress.current) progress.current.style.transform = `scaleX(${total > 0 ? Math.min(1, window.scrollY / total) : 0})`; }); };
    update(); window.addEventListener("scroll", update, { passive: true }); window.addEventListener("resize", update);
    return () => { cancelAnimationFrame(frame); window.removeEventListener("scroll", update); window.removeEventListener("resize", update); };
  }, []);
  return <div className="cap-scroll-progress" ref={progress} aria-hidden="true" />;
}
