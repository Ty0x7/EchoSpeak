import React, { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { PlatformActions, Face, Icon, SiteFooter, SiteHeader, type IconName } from "./Chrome";
import { GroupChatDiagram, MessageTimeline, SystemDiagram } from "./diagrams";
import "./diagrams.css";
import { GITHUB_URL, RELEASES_URL } from "./release";
import "./site.css";

type Section = { id: string; title: string; icon: IconName; body: React.ReactNode };

const Steps = ({ items }: { items: React.ReactNode[] }) => (
  <ol className="doc-steps">
    {items.map((item, i) => (
      <li key={i}><span>{i + 1}</span><div>{item}</div></li>
    ))}
  </ol>
);

const Tip = ({ children }: { children: React.ReactNode }) => <p className="doc-tip"><Icon name="spark" size={15} /> <span>{children}</span></p>;

const SECTIONS: Section[] = [
  {
    id: "getting-started",
    title: "Getting started",
    icon: "spark",
    body: (
      <>
        <p>Install a client, confirm that your model can answer, and start with a simple prompt.</p>
        <Steps
          items={[
            <><strong>Install EchoSpeak</strong><p>Download the Windows installer or follow the Linux desktop build guide.</p>
              <PlatformActions linuxUrl={`${GITHUB_URL}/blob/main/apps/desktop/LINUX.md`} /></>,
            <><strong>Connect and verify a model</strong><p>Choose Ollama, <a href="https://lmstudio.ai" target="_blank" rel="noreferrer">LM Studio</a>, a compatible local server or a supported cloud provider. In <em>Settings › Models</em>, select the model, then choose <em>Test response</em> under <em>Check provider</em>.</p></>,
            <><strong>Start a conversation</strong><p>Type a prompt in the composer. Attach a folder when you want Echo to work with project files, or choose <em>Voice</em> to speak.</p>
              <div className="doc-example"><Face size={30} /><div><b>Try asking Echo</b><span>Help me plan a 25-minute focus session.</span></div></div></>,
          ]}
        />
        <Tip>Check the release notes for the features available in your build.</Tip>
      </>
    ),
  },
  {
    id: "terminal-setup",
    title: "Terminal setup",
    icon: "terminal",
    body: (
      <>
        <p>The terminal client can use the same backend, settings and project sessions as the desktop.</p>
        <Steps items={[
          <><strong>Install from a checkout</strong><p>Follow the <a href={`${GITHUB_URL}/blob/main/apps/terminal/README.md#install`}>terminal install guide</a>. The provided script creates a Python 3.12 environment with pinned dependencies.</p></>,
          <><strong>Connect to your backend</strong><p>Run <code>echospeak backend status</code> to check the active endpoint. Start <code>echospeak</code> and select a model when prompted.</p></>,
          <><strong>Pick up a session</strong><p>Open <code>/sessions</code> inside the terminal or run <code>echospeak resume &lt;id-or-prefix&gt;</code> to continue an existing conversation.</p></>,
        ]} />
        <p><a href={`${GITHUB_URL}/blob/main/apps/terminal/README.md`}>Terminal reference and keyboard shortcuts</a></p>
      </>
    ),
  },
  {
    id: "creations",
    title: "Creations",
    icon: "spark",
    body: <>
      <p>Images and videos you make with Echo have their own home: <em>Creations</em>, in the sidebar.</p>
      <Steps items={[<>Open <em>Creations › Creation settings</em> and enable creation. Choose Gemini for images, Veo or MiniMax for videos, or ComfyUI locally.</>, <>Add your provider key or connect a local server. Optional managed setup detects Windows NVIDIA hardware and downloads an isolated runtime and starter models after you choose to install.</>, <>Ask Echo to create an image or make a video in chat. Cloud requests ask approval before submission. The finished result appears in chat and your library.</>]} />
      <p>Preview, download, rename, archive and restore your creations. Open the original chat to return to the idea behind them.</p>
      <Tip>Cloud providers may charge for generation. Local models are large optional downloads and need compatible hardware. Stopping a job does not guarantee that provider processing or billing stops.</Tip>
    </>,
  },
  {
    id: "whats-new",
    title: "Current features",
    icon: "clock",
    body: <>
      <p>The current source tree includes project sessions, local model selection and Echo Companion. Check Settings › About for your installed version.</p>
      <ul className="doc-list">
        <li><strong>Project work.</strong> Attach a folder, inspect the files a task uses and review its tool activity and checks.</li>
        <li><strong>Local models.</strong> Choose a model per chat and inspect the context window the server actually allocates.</li>
        <li><strong>Consent for corrections.</strong> Approve a proposed update before Echo changes a recalled memory.</li>
        <li><strong>Desktop updates.</strong> Echo Companion can report activity from supported agent sessions.</li>
      </ul>
      <p><a href={RELEASES_URL}>Published releases</a></p>
    </>,
  },
  {
    id: "research",
    title: "Web research",
    icon: "research",
    body: <>
      <p>Ask a concrete question and let Echo search different angles, open useful sources, read webpages and PDFs, and compare what it finds.</p>
      <p>The built-in DuckDuckGo option does not need an API key. Configure Brave, Tavily or a SearXNG server in <em>Settings › Web search</em> for another search index.</p>
      <p>A research notebook retains source passages and working notes for seven days within each chat, separately from personal memory. Open the shared right panel’s Research tab to inspect sources and notes. Clicking a supported citation opens its retained passage; you can export findings or explicitly save them to the attached project.</p>
    </>,
  },
  {
    id: "agents",
    title: "Agents & group chats",
    icon: "team",
    body: (
      <>
        <div className="doc-team">
          <span><Face size={22} /> <b>Echo</b> your personal agent</span>
          <span><Face tone="dark" size={22} /> <b>Jarvis</b> research</span>
          <span><Face tone="dark" size={22} /> <b>Glados</b> building and code</span>
        </div>
        <ul className="doc-list">
          <li><strong>Chat with one agent:</strong> click it in the sidebar under <em>Agents</em>.</li>
          <li><strong>Group chats:</strong> <em>Group chats › New</em>, pick the members. Type <code>@Jarvis</code> to choose who answers, or <code>@all</code> for everyone.</li>
          <li><strong>Handoffs:</strong> agents pass work to each other (“Echo, get Glados to save that”). Each run ends with <em>✓ Done</em> or <em>Stopped</em> and the reason, so you always know if the job really finished.</li>
          <li><strong>Discussion mode:</strong> agents take turns on a question and the lead writes a conclusion.</li>
          <li><strong>Make your own agent:</strong> the <em>+</em> next to Agents. Give it a name, personality, model and allowed tools.</li>
        </ul>
        <GroupChatDiagram />
      </>
    ),
  },
  {
    id: "companion",
    title: "Echo Companion",
    icon: "voice",
    body: <>
      <p>Echo is the personal agent you chat with in EchoSpeak. Echo Companion is a separate desktop observer that can speak brief updates from EchoSpeak and supported Codex and Claude Code sessions. The Companion attributes each report to its source.</p>
      <ul className="doc-list">
        <li>Review voice and check-in options in <em>Settings › Echo Companion</em>.</li>
        <li>Check-ins can summarize observed work. A reported outcome and an EchoSpeak verification result carry different evidence.</li>
        <li>The Companion observes sessions. Tool use and approvals stay with the agent doing the work.</li>
      </ul>
    </>,
  },
  {
    id: "memory",
    title: "Memory",
    icon: "memory",
    body: (
      <>
        <p>Agents remember short, lasting facts about you, like “Prefers short answers” or “Partner is called Sam”. One fact per memory, so they stay easy to read and fix.</p>
        <div className="doc-loop" aria-label="How a memory is saved">
          <span>You ask Echo to remember</span><i aria-hidden="true">→</i><span>Echo saves a scoped fact</span><i aria-hidden="true">→</i><span>Recalled when relevant</span>
        </div>
        <ul className="doc-list">
          <li><strong>What isn't saved:</strong> one-off requests, reminders, questions, things that only matter in the current chat, web content, and anything that looks like a password or key.</li>
          <li><strong>Corrections require consent:</strong> when you correct a recalled fact, Echo shows the proposed update for your approval. The replacement stays in the original memory scope.</li>
          <li><strong>Pinned facts</strong> ride along in every chat; the rest are recalled only when they relate to what you asked.</li>
          <li><strong>You're in charge:</strong> <em>Settings › Memory</em> shows everything, by kind. Edit, pin, change or delete any memory.</li>
          <li><strong>Safety:</strong> if an agent read a web page or email in that chat, it asks before saving a memory, so nothing online can plant one.</li>
        </ul>
        <Tip>Your chats themselves are kept separately and searchable. Memory is only for the facts worth carrying between chats.</Tip>
      </>
    ),
  },
  {
    id: "learning",
    title: "Learning",
    icon: "learn",
    body: (
      <>
        <p>Echo learns on its own from checked work in Chat, Agents and Code, with whichever model you use. When a task is verified, a short lesson about what to repeat or avoid is written down, tried on similar tasks, and kept only if tasks go better with it. You don't have to manage any of it.</p>
        <ol className="doc-ladder" aria-label="How sure EchoSpeak is that a task worked">
          {[["Claimed", "The agent said it was done"], ["Ran", "A tool actually ran"], ["Checked", "The result was read back or tested"], ["Double-checked", "Checked a second way"], ["You confirmed", "You pressed Worked"]].map(([t, d], i) => (
            <li key={t} style={{ "--n": i + 1 } as React.CSSProperties}><b>{t}</b>{d}</li>
          ))}
        </ol>
        <ul className="doc-list">
          <li><strong>Lessons are advice only.</strong> They can never change permissions, approvals, tools, settings or code.</li>
          <li><strong>Only the risky ones wait for you.</strong> Lessons from a task that changed tests or EchoSpeak's own files, lessons about sending things, and lessons from web pages that name sites or say what to trust. Anything else learned while reading the web is tried with extra care and never shared between agents.</li>
          <li><strong>Tell it how it went, any way you like:</strong> the thumbs under a reply, or just say "that worked" or "that's wrong" in your next message.</li>
          <li><strong>Diagnostics</strong> in Settings › Advanced › Learning show each lesson, where it came from and whether it helps, and let you pause or reset learning.</li>
        </ul>
      </>
    ),
  },
  {
    id: "routines",
    title: "Routines",
    icon: "clock",
    body: (
      <>
        <p>Routines are tasks an agent runs on a schedule, or when you press Run. Each one gets its own chat, so the results are easy to find.</p>
        <Steps items={[<>Open <em>Routines</em> in the sidebar and choose <em>New routine</em>.</>, <>Say what to do (“Every morning, summarize today's weather and top tech news”), pick a time and the agent.</>, <>Turn it on. Use the switch on its row to pause it, or <em>Run</em> to try it now.</>]} />
      </>
    ),
  },
  {
    id: "answers",
    title: "Answers you can see",
    icon: "chart",
    body: (
      <>
        <p>When the data fits, answers come with a card. Every card is built from the real source, so prices, links and numbers are never made up.</p>
        <div className="doc-cards">
          {[
            ["Weather", "Today plus a 7-day forecast"],
            ["Stocks", "Price charts, compare two companies"],
            ["Shopping", "Real prices and store links, with an “as of” time"],
            ["Videos & pictures", "Video cards with length, image gallery"],
            ["Sports", "Scores, schedules, standings, team logos"],
            ["Sources", "Numbered links for anything Echo looked up"],
            ["Diagrams & math", "Flowcharts and formulas, drawn for you"],
            ["Tables", "Sort by any column, copy to a spreadsheet"],
          ].map(([t, d]) => <div key={t}><strong>{t}</strong><span>{d}</span></div>)}
        </div>
      </>
    ),
  },
  {
    id: "artifacts",
    title: "Artifacts & the side panel",
    icon: "code",
    body: (
      <ul className="doc-list">
        <li><strong>Artifacts</strong> are things you'll use or keep: mini apps, calculators, games, documents, diagrams, code files. Ask for one (“build me a budget planner”) and it opens in the panel on the right.</li>
        <li><strong>Versions:</strong> ask for a change and you get version 2. Use the arrows to go back, and <em>Restore</em> to bring an old one back.</li>
        <li><strong>Buttons:</strong> copy, download, open in your browser, full screen. Every artifact is listed on the <em>Artifacts</em> page.</li>
        <li><strong>Safe by design:</strong> apps run sealed off, with no internet and no access to your files or to EchoSpeak.</li>
        <li><strong>Activity tab:</strong> see every command, file change and search the agents made. Drag the panel's edge to resize it.</li>
      </ul>
    ),
  },
  {
    id: "projects",
    title: "Projects & coding",
    icon: "code",
    body: (
      <ul className="doc-list">
        <li><strong>Attach a folder</strong> (<em>Projects › +</em>, or drop a folder on the message box). Chats in that project work on those files.</li>
        <li><strong>Agents can</strong> read, search, edit and create files, and run commands to test their work.</li>
        <li><strong>Sandbox:</strong> if Docker Desktop is running, commands run in a sealed container. EchoSpeak asks before going online, running on your PC directly, or deleting project files.</li>
        <li><strong>Undo:</strong> file edits are checkpointed, so “undo that” works.</li>
      </ul>
    ),
  },
  {
    id: "git",
    title: "Git & GitHub",
    icon: "branch",
    body: (
      <>
        <p>In a project folder that is a git repository, agents work the way a careful developer does.</p>
        <ul className="doc-list">
          <li><strong>They look first:</strong> agents see the branch and what changed before they touch anything.</li>
          <li><strong>They work on a branch,</strong> stage the files they changed by name, and write clear commit messages.</li>
          <li><strong>Pushing waits for you.</strong> Push, merge or close pull requests, create releases, rebase or throw away changes: each of these asks first.</li>
          <li><strong>Never sneaky:</strong> no force-pushing and no discarding work they didn't make, unless you ask for exactly that.</li>
          <li><strong>GitHub:</strong> install the GitHub CLI and run <code>gh auth login</code> once. Agents can then open pull requests, read issues and check why CI failed.</li>
        </ul>
      </>
    ),
  },
  {
    id: "voice",
    title: "Voice",
    icon: "voice",
    body: <>
      <ul className="doc-list">
        <li><strong>Dictation</strong> turns speech into a prompt. <strong>Voice</strong> opens a continuous conversation with Echo’s avatar and a live transcript.</li>
        <li><strong>Settings › Voice</strong> configures speech recognition and playback. Local speech models are optional downloads; cloud speech services send audio or text to the selected provider.</li>
        <li><strong>Gemini Live</strong> uses native audio with compatible API models. Other chat models use configured speech recognition, normal chat and speech playback.</li>
        <li><strong>Read aloud</strong> is available under assistant replies. Voice mode includes microphone and playback controls; optional wake word is in the composer’s more menu.</li>
        <li>Microphone permission and provider access are required where applicable. Voice tools follow the same permissions and approval rules as text chat.</li>
      </ul>
    </>,
  },
  {
    id: "privacy",
    title: "Privacy & safety",
    icon: "shield",
    body: (
      <>
        <ul className="doc-list">
          <li><strong>Stays on your PC:</strong> chats, memory, agents, settings and files.</li>
          <li><strong>Network use depends on your choices:</strong> cloud models receive request context, search contacts online services, and cloud speech or media generation sends the relevant audio, text or approved references. Local storage alone does not make every feature offline.</li>
          <li><strong>Private mode</strong> (Settings › Privacy): your messages, files, memories and searches never go to a service outside your machines. Local models (LM Studio, Ollama) and your own SearXNG keep working. <strong>Offline mode</strong> allows only this PC and your own network. Both are enforced: a backstop refuses internet connections nothing approved, and the Privacy page runs a check that probes your setup and lists anything still reaching outside.</li>
          <li><strong>Permissions and approvals:</strong> tool access depends on the agent, project scope and configured policy. Review the proposed action and arguments when approval is required. Terminal isolation depends on the operating system and the configured runner. Host terminal mode runs with your account’s access.</li>
          <li><strong>Outside content is untrusted:</strong> after reading a webpage or email, actions covered by the external action policy require approval or are refused where no one can approve. These checks run outside the model. Provider credentials authenticate requests to the selected service; never include keys in public screenshots or issues.</li>
          <li><strong>Strangers on Discord</strong> only get look-up tools, never your files, memory or terminal.</li>
        </ul>
      </>
    ),
  },
  {
    id: "how-it-works",
    title: "How it works",
    icon: "model",
    body: (
      <>
        <p>EchoSpeak is the harness around your selected model: a desktop host, chat workspace and local agent runtime that prepares context, checks tool requests and records execution results. Models can run locally or through cloud APIs; configured online integrations also use network services.</p>
        <p>The desktop and terminal clients can use the same backend. Echo Companion observes supported sessions independently.</p>
        <SystemDiagram />
        <MessageTimeline />
      </>
    ),
  },
  {
    id: "requirements",
    title: "Requirements",
    icon: "windows",
    body: (
      <dl className="doc-reqs">
        {[
          ["System", "Windows 10 or 11 (x64 installer), Linux x86-64 (build from source)"],
          ["Disk", "App storage plus separate space for optional speech and generation models"],
          ["Memory", "Local model requirements depend on model size, quantization and runtime"],
          ["Local models", "Ollama, LM Studio or a compatible model server. Hardware requirements depend on the model and allocated context."],
          ["Cloud models", "OpenAI, Gemini, Claude or Grok API access, with billing where required"],
        ].map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
    ),
  },
  {
    id: "faq",
    title: "FAQ",
    icon: "book",
    body: (
      <div className="doc-faq">
        {[
          ["Is it free?", "Yes. EchoSpeak is free and open source (MIT). Local models are free too; cloud models bill you directly."],
          ["Mac or Linux?", "Linux x86-64 can be built from source. There is a published Windows installer. A macOS package is currently unavailable."],
          ["Which model should I use?", "Choose a compatible model that supports the tools you need and fits your machine or API account. Verify its response in setup or Settings before starting work."],
          ["Does it need the internet?", "Configured local chat and file tools can work offline. Downloads, web tools, cloud models, cloud speech and cloud generation need network access."],
          ["How do I update?", "Use Settings › About when your installed build offers an update, or install a new build from the appropriate release or source tree."],
          ["Where's my data?", "EchoSpeak stores its local data under your user account. The data directory depends on the platform and launch configuration."],
        ].map(([q, a]) => (
          <details key={q}><summary>{q}</summary><p>{a}</p></details>
        ))}
        <p className="doc-more">Something else? <a href={`${GITHUB_URL}/issues`} target="_blank" rel="noreferrer">Ask on GitHub</a> · <a href={RELEASES_URL} target="_blank" rel="noreferrer">All releases</a></p>
      </div>
    ),
  },
];

export function Docs() {
  const { section } = useParams();
  const [active, setActive] = useState(section || SECTIONS[0].id);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    setActive(section || SECTIONS[0].id);
  }, [section]);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        const top = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (top) setActive(top.target.id.replace(/^doc-/, ""));
      },
      { rootMargin: "-90px 0px -60% 0px" },
    );
    SECTIONS.forEach((s) => {
      const el = document.getElementById(`doc-${s.id}`);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);
  return (
    <div className="site site-docs">
      <SiteHeader />
      <main id="main-content" className="docs shell" tabIndex={-1}>
        <header className="docs-hero">
          <h1>EchoSpeak docs</h1>
          <p>Set up a model, start a project and configure the tools you want Echo to use.</p>
        </header>
        <aside className="docs-toc" data-expanded={expanded}>
          <button type="button" className="docs-toc-toggle" aria-controls="docs-sections" aria-expanded={expanded} onClick={() => setExpanded((open) => !open)}>
            <Icon name="book" size={16} /> On this page <Icon name="down" size={15} />
          </button>
          <nav className="docs-nav" id="docs-sections" aria-label="Docs sections">
            {SECTIONS.map((s) => (
              <Link key={s.id} to={`/docs/${s.id}`} onClick={() => setExpanded(false)} className={active === s.id ? "is-on" : ""} aria-current={active === s.id ? "location" : undefined}>
                <Icon name={s.icon} size={16} />
                {s.title}
              </Link>
            ))}
          </nav>
        </aside>
        <article className="docs-body">
          {SECTIONS.map((s) => (
            <section key={s.id} id={`doc-${s.id}`} className="doc-section" aria-labelledby={`doc-title-${s.id}`}>
              <h2 id={`doc-title-${s.id}`} tabIndex={-1}><Icon name={s.icon} size={20} />{s.title}</h2>
              {s.body}
            </section>
          ))}
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
