import React, { useEffect } from "react";
import { HashRouter, Route, Routes, useLocation } from "react-router-dom";
import { Docs } from "./Docs";
import { Home } from "./Home";

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => window.scrollTo(0, 0), [pathname]);
  return null;
}

/** The static website. Hash routes (#/docs) so GitHub Pages never 404s on a deep link. */
export function SiteApp() {
  return (
    <HashRouter>
      <ScrollToTop />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/docs" element={<Docs />} />
        <Route path="/docs/:section" element={<Docs />} />
        <Route path="*" element={<Home />} />
      </Routes>
    </HashRouter>
  );
}
