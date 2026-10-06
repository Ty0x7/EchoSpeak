import React, { useMemo } from "react";
import { getEchoSpeakApiBase } from "../desktop/bridge";
import { globalCss } from "../app/globalCss";
import leanCss from "../lean/lean.css?inline";
import settingsCss from "../settings/settings.css?inline";
import { FirstRunSetup } from "./FirstRunSetup";

/**
 * The desktop setup window: first-run setup on its own, so it never draws over
 * the main or Settings window. It loads only what setup needs, not the chat.
 * When setup ends it tells the main window (FirstRunSetup) and hides itself.
 */
export function SetupWindow() {
  const apiBase = useMemo(() => getEchoSpeakApiBase(), []);
  return (
    <div className="echo-root setup-window">
      <style>{globalCss}</style>
      <style>{leanCss}</style>
      <style>{settingsCss}</style>
      <FirstRunSetup apiBase={apiBase} windowed />
    </div>
  );
}
