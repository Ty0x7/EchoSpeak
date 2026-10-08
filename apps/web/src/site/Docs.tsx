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
            <><strong>Download and install.</strong> Download from the official releases. If Windows reports a threat or blocks the app, stop and check Windows Security’s Protection history. Report the app version and warning through GitHub issues; keep Windows protection enabled.</>,
            <><strong>Pick a brain (a model).</strong> Free and private: install <a href="https://lmstudio.ai" target="_blank" rel="noreferrer">LM Studio</a>, download <em>Gemma 4 E4B</em> (great on an 8 GB graphics card) and start its server. Or paste an OpenAI or Gemini key instead. The first-launch setup guides you through choosing a model, search and optional tools. You can reopen it from <em>Settings › General › Setup</em>.</>,
            <><strong>Say hi.</strong> Type in the box at the bottom, or turn on <em>Wake</em> and say “Hey Echo”.</>,
          ]}
        />
        <div className="doc-cta"><DownloadButton compact /></div>
        <Tip>EchoSpeak updates itself: when a new version is out, <em>Settings › About</em> shows an “Update” button.</Tip>
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
    id: "research",
    title: "Web research",
    icon: "research",
    body: <>
      <p>Ask a concrete question and let Echo search different angles, open useful sources, read webpages and PDFs, and compare what it finds.</p>
      <p>DuckDuckGo works without a key. Configure Brave, Tavily or a SearXNG server in <em>Settings › Web search</em> for another search index.</p>
      <p>A research notebook retains source passages and working notes for seven days within each chat. It is separate from personal memory. Source links help you check the evidence yourself.</p>
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
    id: "memory",
    title: "Memory",
    icon: "memory",
    body: (
      <>
        <p>Agents remember short, lasting facts about you, like “Prefers short answers” or “Partner is called Sam”. One fact per memory, so they stay easy to read and fix.</p>
        <div className="doc-loop" aria-label="How a memory is saved">
          <span>You mention something lasting</span><i>→</i><span>Echo saves one short fact</span><i>→</i><span>Recalled when it matters</span>
        </div>
        <ul className="doc-list">
          <li><strong>What isn't saved:</strong> one-off requests, reminders, questions, things that only matter in the current chat, web content, and anything that looks like a password or key.</li>
          <li><strong>Facts that change replace themselves:</strong> tell Echo you moved and the new city replaces the old one.</li>
          <li><strong>Pinned facts</strong> ride along in every chat; the rest are recalled only when they relate to what you asked.</li>
          <li><strong>You're in charge:</strong> <em>Settings › Advanced › Memory &amp; documents</em> shows everything, by kind. Edit, pin, change or delete any memory.</li>
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
        <p>Agents learn from their own checked work. When a task is verified, the agent writes down a short lesson about what to repeat or avoid next time. This is an experimental preview.</p>
        <ol className="doc-ladder" aria-label="How sure EchoSpeak is that a task worked">
          {[["Claimed", "The agent said it was done"], ["Ran", "A tool actually ran"], ["Checked", "The result was read back or tested"], ["Double-checked", "Checked a second way"], ["You confirmed", "You pressed Worked"]].map(([t, d], i) => (
            <li key={t} style={{ "--n": i + 1 } as React.CSSProperties}><b>{t}</b>{d}</li>
          ))}
        </ol>
        <ul className="doc-list">
          <li><strong>Lessons are advice only.</strong> They can never change permissions, approvals, tools, settings or code.</li>
          <li><strong>You review the risky ones.</strong> Anything learned from web pages, email or other outside content waits for your approval.</li>
          <li><strong>Everything has an undo.</strong> The <em>Learning</em> page shows each lesson, where it came from, and its history.</li>
          <li><strong>Tell it how it went:</strong> the thumbs under a reply mean Worked or Didn't work. Agents learn from that too.</li>
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
          <li><strong>Approvals:</strong> reading and searching just run. Deleting, sending messages, pushing code and controlling your desktop wait for your OK, and the card shows the exact command.</li>
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
              <p>Everything you need, in plain words. New here? Start with <Link to="/docs/getting-started">Getting started</Link>.</p>
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
