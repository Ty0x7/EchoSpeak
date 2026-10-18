import React, { useEffect, useState } from "react";
import { leanApi } from "../lean/api";
import type { LeanPersona } from "../lean/types";
import { Row } from "./controls";

/** Fired after an agent's toolsets change here, so an open chat window refreshes its roster. */
export const AGENTS_CHANGED = "echospeak:agents-changed";

/** Keep everything the agent has, plus Screen & images (control aims with a fresh screenshot) and Desktop control. */
export function withDesktopControl(toolsets: string[]): string[] {
  return [...new Set([...toolsets, "vision", "desktop"])];
}

/**
 * The desktop permission alone doesn't let any agent click: each agent also needs its
 * Desktop control toolset (off by default). Offer to add it right where the permission is
 * turned on, so "Echo says it can't control the computer" can't happen silently.
 */
export function DesktopControlOffer({ apiBase }: { apiBase: string }) {
  const [agents, setAgents] = useState<LeanPersona[] | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    leanApi(apiBase).agents().then((items) => live && setAgents(items)).catch(() => live && setAgents([]));
    return () => { live = false; };
  }, [apiBase]);

  if (!agents) return null;
  const without = agents.filter((agent) => !agent.toolsets.includes("desktop"));
  const withIt = agents.filter((agent) => agent.toolsets.includes("desktop"));

  const add = async (agent: LeanPersona) => {
    setBusy(agent.id);
    setError("");
    try {
      const toolsets = withDesktopControl(agent.toolsets);
      const updated = await leanApi(apiBase).updateAgent(agent.id, { toolsets });
      setAgents((items) => (items || []).map((item) => (item.id === agent.id ? { ...item, ...updated, toolsets } : item)));
      window.dispatchEvent(new CustomEvent(AGENTS_CHANGED));
    } catch (e) {
      setError(`Couldn't update ${agent.name}: ${e instanceof Error ? e.message : "try again from the agent editor"}.`);
    } finally {
      setBusy("");
    }
  };

  return (
    <Row
      label="Agents that can use it"
      help={withIt.length
        ? `${withIt.map((a) => a.name).join(", ")} can use the mouse and keyboard. Each desktop action still asks first.`
        : "No agent can use the mouse and keyboard yet. This permission only allows it; each agent also needs Desktop control."}
      stack
    >
      {without.length ? (
        <div className="st-inline-actions">
          {without.map((agent) => (
            <button key={agent.id} type="button" className="es-btn" disabled={Boolean(busy)} onClick={() => void add(agent)}>
              {busy === agent.id ? "Adding…" : `Add to ${agent.name}`}
            </button>
          ))}
        </div>
      ) : null}
      {error ? <div className="st-muted" role="alert">{error}</div> : null}
    </Row>
  );
}
