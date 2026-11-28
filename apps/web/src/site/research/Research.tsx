import React, { useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link, useParams } from "react-router-dom";
import { SiteFooter, SiteHeader } from "../Chrome";
import { EchoBlueprint, StudyArt } from "./StudyArt";
import { Figure } from "./Figures";
import { POSTS, REPO_BLOB, STATUS_LABEL, findPost, type Post } from "./posts";
import "./research.css";

function Badge({ status }: { status: Post["status"] }) {
  return <span className={`r-badge r-badge-${status}`}>{STATUS_LABEL[status]}</span>;
}

function useTitle(title: string) {
  useEffect(() => {
    const previous = document.title;
    document.title = title;
    return () => { document.title = previous; };
  }, [title]);
}

const FINDINGS: { status: Post["status"]; title: string; text: string; slug: string }[] = [
  { status: "verified", title: "Harness changes raised task completion", slug: "same-model-better-harness",
    text: "Overall execution success rose from 48% to 83% across 14 tasks with the same 9B model." },
  { status: "measured", title: "A 27B model fits an 8 GB card", slug: "bonsai-27b-on-8gb",
    text: "At 1.76 bits per weight with a 4-bit cache: 32K of context, 96% of agent tasks completed, vision 18 of 18." },
  { status: "measured", title: "Completion claims need evidence", slug: "evidence-based-execution",
    text: "The judge flagged 9 of 177 runs for the 9B model and 1 of 177 for the 27B. The 27B flag was disputed." },
  { status: "measured", title: "Quality and speed can disagree", slug: "bonsai-27b-on-8gb",
    text: "The stronger model writes 2.6× faster but thinks longer, so a typical task takes longer. We report both." },
  { status: "negative", title: "A listed skill went unused", slug: "same-model-better-harness",
    text: "A 9B model never opened an available written procedure in 182 requests. We kept nothing from that experiment." },
  { status: "hypothesis", title: "Does the harness matter less as models grow?", slug: "accessible-hardware",
    text: "Open. Planned: the same tasks with other agent software and on larger hardware, same weights and judge." },
];

export function ResearchIndex() {
  useTitle("Research · EchoSpeak");
  return (
    <div className="site">
      <SiteHeader />
      <main id="main-content" className="shell r-main" tabIndex={-1}>
        <header className="r-hero">
          <div className="r-hero-copy">
            <span className="kicker">EchoSpeak Research</span>
            <h1>What makes a local agent reliable?</h1>
            <p className="r-lede">We give an agent real tasks on a desktop graphics card, change one thing, and check what it actually finishes.</p>
          </div>
          <figure className="r-echo-blueprint">
            <EchoBlueprint />
            <figcaption>Echo · Research illustration</figcaption>
          </figure>
        </header>

        <section className="r-section" aria-labelledby="articles-title">
          <h2 id="articles-title">Inside the studies</h2>
          <ol className="r-list">
            {POSTS.map((p) => (
              <li key={p.slug}>
                <Link to={`/research/${p.slug}`}>
                  <StudyArt slug={p.slug} />
                  <div className="r-study-copy">
                    <Badge status={p.status} />
                    <h3>{p.title}</h3>
                    <p>{p.summary}</p>
                  </div>
                </Link>
              </li>
            ))}
          </ol>
        </section>

        <section className="r-section" aria-labelledby="findings-title">
          <h2 id="findings-title">Findings so far</h2>
          <div className="r-findings">
            {FINDINGS.map((f) => (
              <details key={f.title}>
                <summary><Badge status={f.status} /><span>{f.title}</span></summary>
                <p>{f.text} <Link to={`/research/${f.slug}`}>Read the study</Link></p>
              </details>
            ))}
          </div>
        </section>

        <section className="r-section r-next" aria-labelledby="next-title">
          <h2 id="next-title">What we're investigating next</h2>
          <ul>
            <li><strong>EchoSpeak compared with other configurations.</strong> Planned comparisons use the same 27B weights and tasks where the runtime and available capabilities allow.</li>
            <li><strong>Scaling.</strong> The same tasks and judge on larger hardware and larger models.</li>
            <li><strong>Learning on stronger models.</strong> Whether lessons an agent keeps help on fresh tasks once the model is capable of using them.</li>
          </ul>
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}

function formatDate(iso: string) {
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

export function ResearchPost() {
  const { slug } = useParams();
  const post = findPost(slug);
  useTitle(post ? `${post.title} · EchoSpeak research` : "Research · EchoSpeak");
  if (!post) {
    return (
      <div className="site"><SiteHeader /><main id="main-content" className="shell r-main" tabIndex={-1}><h1>Article unavailable</h1><Link className="text-link" to="/research">All research</Link></main><SiteFooter /></div>
    );
  }
  const index = POSTS.findIndex((p) => p.slug === post.slug);
  const next = POSTS[index + 1];
  return (
    <div className="site">
      <SiteHeader />
      <main id="main-content" className="shell r-main" tabIndex={-1}>
        <article className="r-article">
          <Link to="/research" className="r-back">All research</Link>
          <div className="r-list-meta"><Badge status={post.status} /><time dateTime={post.date}>{formatDate(post.date)}</time></div>
          <h1>{post.title}</h1>
          <p className="r-lede">{post.summary}</p>
          <StudyArt slug={post.slug} />
          <div className="r-prose">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                p({ children }) {
                  const text = React.Children.toArray(children).join("");
                  const m = /^::figure ([a-z0-9-]+)$/.exec(text.trim());
                  return m ? <Figure name={m[1]} /> : <p>{children}</p>;
                },
                a({ href, children }) {
                  const internal = href?.startsWith("#/");
                  return <a href={href} {...(internal ? {} : { target: "_blank", rel: "noreferrer" })}>{children}</a>;
                },
                table({ children }) { return <div className="r-table"><table>{children}</table></div>; },
              }}
            >
              {post.body}
            </ReactMarkdown>
          </div>
          {post.evidence.length ? (
            <aside className="r-evidence" aria-label="Evidence">
              <h2>Evidence</h2>
              <p>The technical reports behind this article, with methods, raw numbers and limitations:</p>
              <ul>{post.evidence.map((e) => <li key={e}><a href={REPO_BLOB + e} target="_blank" rel="noreferrer"><code>{e}</code></a></li>)}</ul>
            </aside>
          ) : null}
          {next ? (
            <Link to={`/research/${next.slug}`} className="r-nextpost">
              <span>Next</span><strong>{next.title}</strong>
            </Link>
          ) : null}
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
