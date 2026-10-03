import React, { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DownloadButton, Face, Icon, SiteFooter, SiteHeader, type IconName } from "./Chrome";
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
        <p>Three steps and you're chatting.</p>
        <Steps
          items={[
            <><strong>Download and install.</strong> Run the installer. Windows may show “Windows protected your PC” because the app is new: click <em>More info</em> → <em>Run anyway</em>.</>,
            <><strong>Pick a brain (a model).</strong> Free and private: install <a href="https://lmstudio.ai" target="_blank" rel="noreferrer">LM Studio</a>, download <em>Gemma 4 E4B</em> (great on an 8 GB graphics card) and start its server. Or paste an OpenAI or Gemini key instead. Choose it in <em>Settings › Models</em>.</>,
            <><strong>Say hi.</strong> Type in the box at the bottom, or turn on <em>Wake</em> and say “Hey Echo”.</>,
          ]}
        />
        <div className="doc-cta"><DownloadButton compact /></div>
        <Tip>EchoSpeak updates itself: when a new version is out, <em>Settings › About</em> shows an “Update” button.</Tip>
      </>
    ),
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
    id: "voice",
    title: "Voice",
    icon: "voice",
    body: (
      <ul className="doc-list">
        <li><em>Settings › Voice</em> downloads a speech model (75–480 MB) that runs on your PC, so nothing you say leaves your computer.</li>
        <li>Press the mic to talk, or turn on <strong>Wake</strong> and say “Hey Echo”.</li>
        <li><strong>Read aloud</strong> speaks Echo's replies.</li>
      </ul>
    ),
  },
  {
    id: "privacy",
    title: "Privacy & safety",
    icon: "shield",
    body: (
      <>
        <ul className="doc-list">
          <li><strong>Stays on your PC:</strong> chats, memory, agents, settings and files.</li>
          <li><strong>Leaves your PC only when needed:</strong> messages go to your model provider if you use a cloud one (OpenAI, Gemini), and web searches and look-ups go to those sites. With a local model and no web tools, nothing leaves.</li>
          <li><strong>Approvals:</strong> reading and searching just run. Deleting, sending messages and controlling your desktop wait for your OK.</li>
          <li><strong>Prompt-injection guard:</strong> after an agent reads a web page or email, anything that sends data out needs your approval, even if approvals are off. Your API keys are never sent anywhere. (Based on Meta's “Rule of Two”.)</li>
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
        <p>For the curious. Everything runs on your PC except the model, which can be local or in the cloud.</p>
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
          ["System", "Windows 10 or 11, 64-bit"],
          ["Disk", "About 1.5 GB for the app"],
          ["Memory", "8 GB RAM (16 GB for local models)"],
          ["Local models", "A GPU with 8 GB of VRAM runs 4B–9B models well (LM Studio or Ollama)"],
          ["Cloud models", "An OpenAI or Gemini API key instead of a GPU"],
          ["Optional", "Docker Desktop for the sandboxed terminal"],
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
          ["Mac or Linux?", "Not yet. Windows only for now."],
          ["Which model should I use?", "Gemma 4 E4B in LM Studio is what EchoSpeak is tested with every release. Bigger models are smarter if your GPU can fit them."],
          ["Does it need the internet?", "Only for web tools (search, weather, sports, shopping) or a cloud model. Chat and files work offline with a local model."],
          ["How do I update?", "Settings › About › Update. It downloads, checks the signature, and restarts."],
          ["Where's my data?", "In your Windows user folder, under AppData › Local."],
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
  useEffect(() => {
    if (!section) return;
    setActive(section);
    window.setTimeout(() => document.getElementById(`doc-${section}`)?.scrollIntoView({ block: "start" }), 50);
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
      <main className="docs shell">
        <aside className="docs-nav" aria-label="Docs sections">
          <p className="kicker">Docs</p>
          {SECTIONS.map((s) => (
            <Link key={s.id} to={`/docs/${s.id}`} className={active === s.id ? "is-on" : ""} aria-current={active === s.id ? "true" : undefined}>
              <Icon name={s.icon} size={16} />
              {s.title}
            </Link>
          ))}
        </aside>
        <article className="docs-body">
          <header className="docs-hero">
            <Face size={48} />
            <div>
              <h1>EchoSpeak docs</h1>
              <p>Everything you need, in plain words.</p>
            </div>
          </header>
          {SECTIONS.map((s) => (
            <section key={s.id} id={`doc-${s.id}`} className="doc-section" aria-labelledby={`doc-title-${s.id}`}>
              <h2 id={`doc-title-${s.id}`}><Icon name={s.icon} size={20} />{s.title}</h2>
              {s.body}
            </section>
          ))}
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
