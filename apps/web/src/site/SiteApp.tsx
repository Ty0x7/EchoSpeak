import React, { useLayoutEffect, useState } from "react";
import { HashRouter, Route, Routes, useLocation } from "react-router-dom";
import { Docs } from "./Docs";
import { Home } from "./Home";
import { ResearchIndex, ResearchPost } from "./research/Research";

function ScrollToRoute() {
  const { pathname, hash, key } = useLocation();
  useLayoutEffect(() => {
    const anchor = hash.slice(1) || (pathname.startsWith("/docs/") ? `doc-${pathname.slice(6)}` : "");
    const target = anchor && document.getElementById(anchor);
    if (target) {
      target.scrollIntoView({ block: "start", behavior: "instant" });
      const focus = target.hasAttribute("tabindex") ? target : target.querySelector<HTMLElement>("h2[tabindex]");
      focus?.focus({ preventScroll: true });
    } else {
      window.scrollTo({ top: 0, behavior: "instant" });
    }
  }, [pathname, hash, key]);
  return null;
}

function SiteRoutes() {
  const { pathname, hash } = useLocation();
  const atHome = pathname === "/" && hash === "";
  const [opening, setOpening] = useState(() => atHome && !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useLayoutEffect(() => {
    if (!atHome) setOpening(false);
  }, [atHome]);

  return (
    <>
      <ScrollToRoute />
      <Routes>
        <Route path="/" element={<Home finishOpening={opening && atHome ? () => setOpening(false) : undefined} />} />
        <Route path="/docs" element={<Docs />} />
        <Route path="/docs/:section" element={<Docs />} />
        <Route path="/research" element={<ResearchIndex />} />
        <Route path="/research/:slug" element={<ResearchPost />} />
        <Route path="*" element={<Home />} />
      </Routes>
    </>
  );
}

/** The static website. Hash routes (#/docs) so GitHub Pages never 404s on a deep link. */
export function SiteApp() {
  return <HashRouter><SiteRoutes /></HashRouter>;
}
