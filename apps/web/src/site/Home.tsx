import React, { useLayoutEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { Face, PlatformActions, Icon, SiteFooter, SiteHeader } from "./Chrome";
import { AgentWalkthrough, WorkspaceShowcase } from "./Showcases";
import "./site.css";
import "./home.css";
import { useLatestRelease } from "./release";

function Hero() {
  return (
    <section className="h-hero shell" aria-labelledby="hero-title">
      <h1 id="hero-title"><span>Ask Echo.</span> <span>See the work.</span></h1>
      <p>Bring a question or attach a folder. EchoSpeak gives the model you choose a workspace on your computer, with tools and permissions you control.</p>
      <PlatformActions />
    </section>
  );
}

function OpeningIntro({ onEnd }: { onEnd: () => void }) {
  const opening = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => opening.current?.focus({ preventScroll: true }), []);

  return (
    <div ref={opening} className="h-opening" role="dialog" aria-modal="true" aria-labelledby="opening-title" tabIndex={-1}
      onKeyDown={(event) => { if (event.key === "Escape") onEnd(); }}
      onAnimationEnd={(event) => { if (event.target === event.currentTarget) onEnd(); }}>
      <div className="h-intro">
        <span className="h-intro-travel" aria-hidden="true"><Face className="h-intro-face" /></span>
        <p id="opening-title" className="h-opening-title">Hi, I’m <span>Echo.</span></p>
      </div>
      <button className="site-skip h-opening-skip" type="button" onClick={onEnd}>Skip intro</button>
    </div>
  );
}

function LocalWork() {
  return (
    <section className="shell h-local" aria-labelledby="local-title">
      <div className="h-local-copy">
        <h2 id="local-title">On your computer. At your discretion.</h2>
        <p>Chats, project context and memory are stored locally. Online tools and cloud models use the connections you configure.</p>
      </div>
      <dl className="h-local-choices">
        <div><Icon name="model" /><dt>Choose where the model runs.</dt><dd>Connect Ollama, LM Studio, a compatible local server or a supported cloud model.</dd></div>
        <div><Icon name="shield" /><dt>Choose what it can do.</dt><dd>Set the tools each agent can use and review actions that need your approval.</dd></div>
      </dl>
      <Link className="text-link" to="/docs/privacy">Read about privacy</Link>
    </section>
  );
}

const researchGuides = [
  { slug: "accessible-hardware", icon: "model", title: "The test machine", detail: "Why we chose an 8 GB GPU and 16 GB of RAM." },
  { slug: "evidence-based-execution", icon: "shield", title: "What counts as done?", detail: "How we check an agent’s claim against the work." },
  { slug: "research-methodology", icon: "branch", title: "Inside the method", detail: "Follow a task from the first prompt to the final check." },
] as const;

function ResearchTeaser() {
  return (
    <section className="shell h-research" aria-labelledby="research-title">
      <div className="h-research-copy">
        <div>
          <Link className="h-research-label" to="/research">EchoSpeak Research</Link>
          <h2 id="research-title">How much can<br />run <em>locally?</em></h2>
        </div>
        <p>We put local models to work on a single consumer GPU. Two experiments show what changes when you tune the software around them.</p>
      </div>
      <div className="h-research-links">
        <Link to="/research/same-model-better-harness" className="h-experiment">
          <div className="h-run-plot" role="group" aria-label="Task execution success before and after the changes">
            {([["Before", 48], ["After", 83]] as const).map(([label, value]) => (
              <div key={label} className="h-run-row">
                <span>{label}</span>
                <i aria-hidden="true"><i style={{ width: value + "%" }} /></i>
                <strong>{value}%</strong>
              </div>
            ))}
          </div>
          <h3>Same model. More tasks finished.</h3>
          <p>With the same 9B model, changes to the agent software raised execution success across 14 tasks.</p>
        </Link>
        <Link to="/research/bonsai-27b-on-8gb" className="h-experiment">
          <div className="h-chip-plot" aria-hidden="true">
            <span className="h-chip"><b>27B</b></span>
            <span className="h-chip-bus" />
            <span className="h-chip-memory"><b>8 GB</b><small>GPU memory</small></span>
          </div>
          <h3>A 27B model on an 8 GB card.</h3>
          <p>With compressed weights and a 4-bit cache, Bonsai held 32K tokens of context on our test machine.</p>
        </Link>
      </div>
      <ul className="h-research-studies" aria-label="More research articles">
        {researchGuides.map(({ slug, icon, title, detail }) => (
          <li key={slug}>
            <Link to={`/research/${slug}`}>
              <Icon name={icon} size={23} />
              <span><strong>{title}</strong><small>{detail}</small></span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function DownloadSection() {
  const release = useLatestRelease();
  return (
    <section className="shell h-download" aria-labelledby="download-title">
      <div className="h-folder-stage" aria-hidden="true">
        <div className="h-project-folder">
          <span className="h-folder-back" />
          <span className="h-folder-sheet" />
          <Face className="h-folder-echo" />
          <span className="h-folder-front"><Icon name="code" size={28} /></span>
        </div>
      </div>
      <div className="h-download-copy">
        <h2 id="download-title">Give Echo<br />a project.</h2>
        <p>Install EchoSpeak, choose your model and open a folder. Your first conversation starts there.</p>
        <PlatformActions />
        <a className="h-release-link" href={release.notesUrl}>{release.version ? `Public release v${release.version}` : "Public release notes"}</a>
      </div>
    </section>
  );
}

export function Home({ finishOpening }: { finishOpening?: () => void }) {
  const content = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (content.current) content.current.inert = Boolean(finishOpening);
  }, [finishOpening]);

  return (
    <div className="site site-home">
      <div ref={content} aria-hidden={finishOpening ? true : undefined}>
        <SiteHeader />
        <main id="main-content" tabIndex={-1}>
          <Hero />
          <WorkspaceShowcase />
          <AgentWalkthrough />
          <LocalWork />
          <ResearchTeaser />
          <DownloadSection />
        </main>
        <SiteFooter />
      </div>
      {finishOpening && <OpeningIntro onEnd={finishOpening} />}
    </div>
  );
}
