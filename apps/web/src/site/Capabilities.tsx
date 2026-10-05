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

const scenes = [
  { name: "A world beyond", prompt: "An astronaut beneath enormous mountains. Cinematic black and white.", position: "0%" },
  { name: "An object of imagination", prompt: "A sculptural glass and metal object. Dramatic studio photography.", position: "50%" },
  { name: "Somewhere quiet", prompt: "A tiny cabin beside an alpine lake. Moonlight, pine trees, and mist.", position: "100%" },
];
export function CreationImage({ scene = 0 }: { scene?: number }) {
  return <div className="creation-photo" role="img" aria-label={scenes[scene].prompt} style={{ "--scene-position": scenes[scene].position } as React.CSSProperties} />;
}
export function CreationsStory() {
  const [scene, setScene] = useState(0), [local, setLocal] = useState(false), [showPrompt, setShowPrompt] = useState(false);
  return <Stage id="creations" className="cap-creations">
    <div className="cap-copy"><span className="kicker">Meet Creations</span><h2>An idea.<br />An image.<br /><span className="dim">A place to keep it.</span></h2><p>Describe the scene you’re imagining. Echo brings your selected image or video model into the conversation, then keeps the result in your library.</p><div className="cap-feature-list"><span><Icon name="check" size={14} /> Create with local or cloud models</span><span><Icon name="check" size={14} /> See the result right in chat</span><span><Icon name="check" size={14} /> Save it. Find it. Make something next.</span></div><Link className="text-link" to="/docs/creations">Inside Creations <Icon name="arrow" size={15} /></Link></div>
    <div className="cap-studio"><div className="cap-studio-top"><span><Face size={18} /> Creations</span><span className="cap-small-label">Sample images · interactive preview</span></div>
      <button className="cap-main-art" type="button" aria-label="Show the image prompt" aria-pressed={showPrompt} onClick={() => setShowPrompt(v => !v)}><CreationImage scene={scene} key={scene} /><span className="cap-art-caption"><strong>{scenes[scene].name}</strong><span>{showPrompt ? "Hide prompt" : "See the prompt"} <Icon name="arrow" size={14} /></span></span>{showPrompt && <span className="cap-art-prompt">“{scenes[scene].prompt}”</span>}</button>
      <div className="cap-thumbnails" role="group" aria-label="Preview creations">{scenes.map((item, index) => <button key={item.name} type="button" aria-label={item.name} aria-pressed={scene === index} onClick={() => setScene(index)}><CreationImage scene={index} /><span>0{index + 1}</span></button>)}</div>
      <div className="cap-routing"><div role="group" aria-label="Generation options"><button type="button" aria-pressed={!local} onClick={() => setLocal(false)}>Cloud</button><button type="button" aria-pressed={local} onClick={() => setLocal(true)}>On your computer</button></div><span>{local ? "ComfyUI" : "Gemini · Veo · MiniMax"}</span></div><p className="cap-footnote">Images and video share one library. Cloud use may cost credits; local generation needs compatible hardware and optional downloads.</p>
    </div>
  </Stage>;
}

const researchSteps = [
  { name: "Search", title: "Find the useful starting points.", description: "Search from different angles with DuckDuckGo, Brave, Tavily, or SearXNG.", status: "Looking beyond the first result", findings: ["A gardening extension guide", "A guide to growing in containers", "A reference for light and watering"], note: "Three starting points. Now inspect the evidence." },
  { name: "Read", title: "Open the source. Read the substance.", description: "Echo reads pages and PDFs, extracts the relevant passages, and follows useful links.", status: "Reading the details", findings: ["Which herbs tolerate morning sun?", "How much drainage do containers need?", "What changes in hot weather?"], note: "Keep the useful passages, along with where they came from." },
  { name: "Connect", title: "Bring the findings together.", description: "A notebook in each chat keeps sources, passages, findings, and open questions for seven days.", status: "Checking what is still missing", findings: ["Start with herbs suited to the light", "Choose containers with good drainage", "Check the actual hours of sunlight"], note: "An answer with context. An open question worth checking." },
];
export function ResearchStory() {
  const [active, setActive] = useState(0); const step = researchSteps[active];
  return <Stage id="research" className="cap-research"><div className="cap-copy"><span className="kicker">Follow the question</span><h2>Find more.<br />Understand more.<br /><span className="dim">Keep the thread.</span></h2><p>Good research takes more than a list of links. Echo can search, read the evidence, compare findings, and return to what still needs an answer.</p><Link className="text-link" to="/docs/research">How Echo researches <Icon name="arrow" size={15} /></Link></div>
    <div className="cap-research-demo"><div className="cap-studio-top"><span><Face size={18} /> Research with Echo</span><span className="cap-small-label">Illustrative walkthrough</span></div><p className="cap-question">“Which herbs could I grow on a balcony with morning sun?”</p>
      <div className="cap-research-nav" role="tablist" aria-label="Research workflow">{researchSteps.map((item, index) => <button key={item.name} type="button" id={`research-tab-${index}`} role="tab" aria-selected={active === index} aria-controls="research-preview" onClick={() => setActive(index)}><span>0{index + 1}</span>{item.name}<Icon name="arrow" size={14} /></button>)}</div>
      <div className="cap-research-board" id="research-preview" role="tabpanel" aria-labelledby={`research-tab-${active}`} key={active}><div className="cap-research-status"><span className="cap-signal" aria-hidden="true" /><span>{step.status}</span></div><h3>{step.title}</h3><div className="cap-findings">{step.findings.map((finding, index) => <div key={finding} style={{ "--delay": `${index * 90}ms` } as React.CSSProperties}><span className="cap-evidence-index">{index + 1}</span><span>{finding}</span><Icon name={active === 2 ? "check" : "book"} size={16} /></div>)}</div><div className="cap-notebook"><Icon name="memory" size={18} /><div><strong>In this chat’s research notebook</strong><p>{step.note}</p></div><span>7 days</span></div><p className="cap-board-caption">{step.description}</p></div>
    </div></Stage>;
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

export function MotionControls({ paused, onToggle }: { paused: boolean; onToggle(): void }) {
  const progress = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let frame = 0;
    const update = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => { const total = document.documentElement.scrollHeight - window.innerHeight; if (progress.current) progress.current.style.transform = `scaleX(${total > 0 ? Math.min(1, window.scrollY / total) : 0})`; }); };
    update(); window.addEventListener("scroll", update, { passive: true }); window.addEventListener("resize", update);
    return () => { cancelAnimationFrame(frame); window.removeEventListener("scroll", update); window.removeEventListener("resize", update); };
  }, []);
  return <><div className="cap-scroll-progress" ref={progress} aria-hidden="true" /><button type="button" className="cap-motion-control" onClick={onToggle} aria-pressed={paused}><span aria-hidden="true">{paused ? "▷" : "Ⅱ"}</span>{paused ? "Resume motion" : "Pause motion"}</button></>;
}
