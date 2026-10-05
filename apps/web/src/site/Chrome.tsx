import React from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { GITHUB_URL, RELEASES_URL, useLatestRelease } from "./release";

export type IconName = "chat" | "team" | "research" | "code" | "memory" | "voice" | "model" | "shield" | "windows" | "github" | "arrow" | "spark" | "house" | "book" | "check" | "chart";

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const c = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.65, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "chat": return <svg {...c}><path d="M5 18.5 3.5 21l.7-4A8.5 8.5 0 1 1 12 20.5H8" /><path d="M8 10h8M8 14h5" /></svg>;
    case "team": return <svg {...c}><circle cx="8" cy="8" r="3" /><circle cx="16.5" cy="9.5" r="2.5" /><path d="M2.5 19a5.5 5.5 0 0 1 11 0M13.5 18.5a4.5 4.5 0 0 1 8-2.8" /></svg>;
    case "research": return <svg {...c}><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4.5 4.5" /></svg>;
    case "code": return <svg {...c}><path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14" /></svg>;
    case "memory": return <svg {...c}><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" /><circle cx="12" cy="12" r="3" /></svg>;
    case "voice": return <svg {...c}><rect x="8" y="3" width="8" height="12" rx="4" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" /></svg>;
    case "model": return <svg {...c}><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M9 9h6v6H9zM9 1.5V4M15 1.5V4M9 20v2.5M15 20v2.5M1.5 9H4M1.5 15H4M20 9h2.5M20 15h2.5" /></svg>;
    case "shield": return <svg {...c}><path d="M12 3 4.5 6v5.5c0 4.4 3 7.7 7.5 9.5 4.5-1.8 7.5-5.1 7.5-9.5V6L12 3Z" /><path d="m9 12 2 2 4-4" /></svg>;
    case "windows": return <svg {...c}><path d="M3 5.5 10.5 4v7H3v-5.5ZM13 3.5 21 2v9h-8V3.5ZM3 13h7.5v7L3 18.5V13ZM13 13h8v9l-8-1.5V13Z" /></svg>;
    case "github": return <svg {...c}><path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21" /></svg>;
    case "spark": return <svg {...c}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /></svg>;
    case "house": return <svg {...c}><path d="M3.5 11 12 4l8.5 7" /><path d="M5.5 9.5V20h13V9.5" /></svg>;
    case "book": return <svg {...c}><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5zM4 20.5A2.5 2.5 0 0 0 6.5 21H20" /></svg>;
    case "check": return <svg {...c}><path d="m5 12.5 4.5 4.5L19 7.5" /></svg>;
    case "chart": return <svg {...c}><path d="M4 19h16M7 16V9M12 16V5M17 16v-4" /></svg>;
    default: return <svg {...c}><path d="M5 12h14M13 6l6 6-6 6" /></svg>;
  }
}

/** Echo's face drawn in CSS: white with dark eyes; teammates dark with white eyes. */
export function Face({ tone = "light", size = 28, className = "" }: { tone?: "light" | "dark"; size?: number; className?: string }) {
  return (
    <span className={`face face-${tone} ${className}`} style={{ ["--s" as string]: `${size}px` }} aria-hidden="true">
      <i />
      <i />
    </span>
  );
}

export function DownloadButton({ variant = "primary", compact = false }: { variant?: "primary" | "light" | "header"; compact?: boolean }) {
  const release = useLatestRelease();
  if (variant === "header") {
    return (
      <a className="header-download" href={release.exeUrl}>
        <Icon name="windows" size={15} /> Download
      </a>
    );
  }
  return (
    <a className={`btn btn-${variant}`} href={release.exeUrl}>
      <Icon name="windows" size={18} />
      <span className="btn-text">
        <strong>Download for Windows</strong>
        {!compact ? <small>{[release.version ? `v${release.version}` : "", release.sizeMb ? `${release.sizeMb} MB` : "", "Free"].filter(Boolean).join(" · ")}</small> : null}
      </span>
    </a>
  );
}

/** Scroll to a section on the home page (works with hash routing). */
export function useScrollTo() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  return (id: string) => {
    const go = () => document.getElementById(id)?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    if (pathname !== "/") {
      navigate("/");
      window.setTimeout(go, 80);
    } else go();
  };
}

export function SiteHeader() {
  const scrollTo = useScrollTo();
  return (
    <header className="site-header">
      <Link className="wordmark" to="/" aria-label="EchoSpeak home">
        <Face size={26} />
        <span>EchoSpeak</span>
      </Link>
      <nav aria-label="Main">
        <button type="button" onClick={() => scrollTo("ways")}>What Echo does</button>
        <button type="button" onClick={() => scrollTo("creations")}>Creations</button>
        <button type="button" onClick={() => scrollTo("research")}>Research</button>
        <button type="button" onClick={() => scrollTo("about")}>About</button>
        <Link to="/docs">Docs</Link>
      </nav>
      <div className="header-right">
        <a className="header-github" href={GITHUB_URL} target="_blank" rel="noreferrer" aria-label="EchoSpeak on GitHub" title="GitHub">
          <Icon name="github" size={18} />
        </a>
        <DownloadButton variant="header" />
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <Link className="wordmark" to="/">
        <Face size={24} />
        <span>EchoSpeak</span>
      </Link>
      <p>A free AI assistant that lives on your PC.</p>
      <div>
        <Link to="/docs">Docs</Link>
        <a href={RELEASES_URL} target="_blank" rel="noreferrer">Releases</a>
        <a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub</a>
      </div>
    </footer>
  );
}
