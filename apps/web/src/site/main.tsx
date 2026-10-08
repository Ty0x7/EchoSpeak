import React from "react";
import ReactDOM from "react-dom/client";
import "../theme/tokens.css";
import { installThemeSync } from "../theme/theme";
import { SiteApp } from "./SiteApp";

// Same light/dark setting as the app, applied before the first paint.
installThemeSync();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <SiteApp />
  </React.StrictMode>,
);
