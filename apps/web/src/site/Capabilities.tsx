import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Face, Icon } from "./Chrome";
import "./capabilities.css";

/** Keep decorative loops asleep until their section is actually on screen. */
function Stage({ id, children, className = "" }: { id: string; children: React.ReactNode; className?: string }) {
  const ref = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!ref.current || typeof IntersectionObserver === "undefined") { setVisible(true); return; }
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { rootMargin: "80px" });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return <section ref={ref} id={id} className={`cap-stage shell ${className}`} data-in-view={visible}>
    {children}
  </section>;
}

function Landscape({ variant = 0 }: { variant?: number }) {
  return <div className={`cap-landscape cap-landscape-${variant}`} aria-hidden="true">
    <div className="cap-stars" /><div className="cap-sun" /><div className="cap-horizon" />
    <div className="cap-dune cap-dune-back" /><div className="cap-dune cap-dune-front" />
    <div className="cap-landscape-noise" />
  </div>;
}

const scenes = ["An impossible desert", "A quieter kind of orbit", "Somewhere after sunset"];

export function CreationsStory() {
  const [scene, setScene] = useState(0);
  const [local, setLocal] = useState(false);
  return <Stage id="creations" className="cap-creations">
    <div className="cap-copy">
      <span className="kicker"><span className="cap-dot" /> New in 10.3 · Creations</span>
      <h2>Say it.<br />See it.<br /><span className="dim">Keep it.</span></h2>
      <p>A picture in your head. A scene you want to bring to life. Ask Echo for an image or video and give your idea a place to land.</p>
      <div className="cap-feature-list"><span><Icon name="check" size={14} /> Images and videos, right in chat</span><span><Icon name="check" size={14} /> One library for everything you create</span><span><Icon name="check" size={14} /> Download, rename, archive, revisit</span></div>
      <Link className="text-link" to="/docs/creations">Explore Creations <Icon name="arrow" size={15} /></Link>
    </div>
    <div className="cap-studio">
      <div className="cap-studio-top"><span><Face size={18} /> Creations</span><span className="cap-small-label">Illustrative preview</span></div>
      <div className="cap-main-art" key={scene}><Landscape variant={scene} /><div className="cap-art-caption"><span>0{scene + 1} / {scenes[scene]}</span><Icon name="spark" size={18} /></div><span className="cap-frame cap-frame-tl" /><span className="cap-frame cap-frame-br" /></div>
      <div className="cap-thumbnails" role="group" aria-label="Preview ideas">{scenes.map((name, index) => <button key={name} type="button" aria-label={name} aria-pressed={scene === index} onClick={() => setScene(index)}><Landscape variant={index} /><span>0{index + 1}</span></button>)}</div>
      <div className="cap-prompt"><Icon name="spark" size={16} /><span>“Create a cinematic landscape from another world.”</span><span className="cap-prompt-send"><Icon name="arrow" size={14} /></span></div>
      <div className="cap-routing"><div role="group" aria-label="Generation options"><button type="button" aria-pressed={!local} onClick={() => setLocal(false)}>Cloud</button><button type="button" aria-pressed={local} onClick={() => setLocal(true)}>Local</button></div><span key={String(local)}>{local ? "ComfyUI · your computer" : "Gemini · Veo · MiniMax"}</span></div>
      <p className="cap-footnote">Choose your provider in the app. Cloud use may cost credits; local generation needs compatible hardware and optional model downloads.</p>
      <div className="cap-floating-tag"><span className="cap-dot" /> From a prompt to your library</div>
    </div>
  </Stage>;
}

const researchSteps = [
  { name: "Search", icon: "research" as const, title: "Start with better questions.", text: "Search different angles with DuckDuckGo, Brave, Tavily, or your own SearXNG server.", query: "What do we know? What is still missing?", status: "Finding useful sources", cards: ["The original research", "A different perspective", "The latest information"] },
  { name: "Read", icon: "book" as const, title: "Go beyond the headline.", text: "Open the useful pages, extract the main content, read PDFs, and follow links that help answer the question.", query: "Open the paper. Read the evidence.", status: "Inspecting the evidence", cards: ["Read the full page", "Inspect the PDF", "Follow the source link"] },
  { name: "Connect", icon: "memory" as const, title: "Keep the thread of the research.", text: "A notebook for each chat keeps sources, passages, findings, and unanswered questions together for seven days.", query: "Compare the findings. Check the gaps.", status: "Putting sources in context", cards: ["What the sources agree on", "Where they disagree", "What needs another search"] },
];

export function ResearchStory() {
  const [active, setActive] = useState(0);
  const step = researchSteps[active];
  return <Stage id="research" className="cap-research">
    <div className="cap-section-heading"><span className="kicker">Curiosity, with a paper trail</span><h2>More than a search.<br /><span className="dim">A way to follow through.</span></h2><p>Search. Read. Compare. Keep going when there’s more to find.</p></div>
    <div className="cap-research-layout">
      <div className="cap-research-nav" role="tablist" aria-label="Research workflow">{researchSteps.map((item, index) => <button key={item.name} type="button" id={`research-tab-${index}`} role="tab" aria-selected={active === index} aria-controls="research-preview" onClick={() => setActive(index)}><span className="cap-step-number">0{index + 1}</span><Icon name={item.icon} size={21} /><span><strong>{item.name}</strong><small>{item.title}</small></span><Icon name="arrow" size={16} /></button>)}</div>
      <div className="cap-research-board" id="research-preview" role="tabpanel" aria-labelledby={`research-tab-${active}`}>
        <div className="cap-board-grid" aria-hidden="true" />
        <div className="cap-search-bar" key={`query-${active}`}><Icon name={step.icon} size={17} /><span>{step.query}</span><span className="cap-search-cursor" aria-hidden="true" /></div>
        <div className="cap-source-network" key={active}>
          <svg className="cap-connections" viewBox="0 0 600 180" preserveAspectRatio="none" aria-hidden="true"><path d="M300 20V65Q300 85 100 85V160M300 20V160M300 20V65Q300 85 500 85V160" /></svg>
          <div className="cap-source-origin"><Face size={28} /><span>{step.status}</span><div className="cap-thinking-dots" aria-hidden="true"><i /><i /><i /></div></div>
          <div className="cap-sources">{step.cards.map((title, index) => <div className="cap-source" key={title} style={{ "--delay": `${index * 180}ms` } as React.CSSProperties}><span className="cap-source-index">{index + 1}<Icon name="book" size={15} /></span><strong>{title}</strong><div className="cap-source-lines" aria-hidden="true"><i /><i /><i /></div><span className="cap-source-status"><Icon name="check" size={12} /> {active === 0 ? "Source discovered" : active === 1 ? "Passage inspected" : "Finding retained"}</span></div>)}</div>
        </div>
        <div className="cap-notebook"><Icon name="memory" size={17} /><span><strong>Research notebook</strong><small>Sources stay with this chat. Your personal memory stays separate.</small></span><span className="cap-small-label">7 days</span></div>
        <p className="cap-board-caption">{step.text}</p>
      </div>
    </div>
  </Stage>;
}

const setupSteps = [
  { name: "Choose a brain", icon: "model" as const, detail: "Connect a model on your computer or bring a cloud API key. Detect running local model apps and test the connection." },
  { name: "Find your tools", icon: "research" as const, detail: "Set up web search, then choose whether to prepare the existing sandbox for building and running software." },
  { name: "Make it yours", icon: "voice" as const, detail: "Choose voice and creation options when you want them. Optional downloads start only when you choose to install." },
];

export function SetupStory() {
  const [active, setActive] = useState(0);
  return <Stage id="setup" className="cap-setup">
    <div className="cap-setup-demo">
      <div className="cap-setup-orbits" aria-hidden="true"><i /><i /><i /></div>
      <div className="cap-setup-window"><span className="cap-window-dots" aria-hidden="true"><i /><i /><i /></span><Face size={44} /><span className="kicker">A little setup. A lot of possibility.</span><h3>Hey. Let’s get you started.</h3><div className="cap-setup-progress" aria-hidden="true">{setupSteps.map((_, index) => <i key={index} data-done={index <= active} />)}</div><div className="cap-setup-options" role="group" aria-label="Setup preview">{setupSteps.map((step, index) => <button key={step.name} type="button" aria-pressed={active === index} onClick={() => setActive(index)}><span>{index < active ? <Icon name="check" size={16} /> : `0${index + 1}`}</span><Icon name={step.icon} size={18} /><strong>{step.name}</strong><Icon name="arrow" size={15} /></button>)}</div><p className="cap-setup-detail" key={active}>{setupSteps[active].detail}</p><span className="cap-setup-later">Optional steps can wait. Echo will be here.</span></div>
      <span className="cap-setup-chip cap-setup-chip-one"><Icon name="model" size={14} /> Local or cloud</span><span className="cap-setup-chip cap-setup-chip-two"><Icon name="shield" size={14} /> You choose the permissions</span>
    </div>
    <div className="cap-copy"><span className="kicker">A warmer welcome</span><h2>Less figuring it out.<br /><span className="dim">More getting into it.</span></h2><p>Your first launch has a guide. Pick your model, check your tools, and choose what you want Echo to do. Come back to the rest whenever you’re ready.</p><div className="cap-feature-list"><span><Icon name="check" size={14} /> Detect what’s already running</span><span><Icon name="check" size={14} /> Keep optional features optional</span><span><Icon name="check" size={14} /> Reopen setup from Settings</span></div><Link className="text-link" to="/docs/getting-started">Your first few minutes <Icon name="arrow" size={15} /></Link></div>
  </Stage>;
}

export function MotionControls({ paused, onToggle }: { paused: boolean; onToggle(): void }) {
  const progress = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const total = document.documentElement.scrollHeight - window.innerHeight;
        if (progress.current) progress.current.style.transform = `scaleX(${total > 0 ? Math.min(1, window.scrollY / total) : 0})`;
      });
    };
    update(); window.addEventListener("scroll", update, { passive: true }); window.addEventListener("resize", update);
    return () => { cancelAnimationFrame(frame); window.removeEventListener("scroll", update); window.removeEventListener("resize", update); };
  }, []);
  return <><div className="cap-scroll-progress" ref={progress} aria-hidden="true" /><button type="button" className="cap-motion-control" onClick={onToggle} aria-pressed={paused}><span aria-hidden="true">{paused ? "▷" : "Ⅱ"}</span>{paused ? "Resume motion" : "Pause motion"}</button></>;
}
