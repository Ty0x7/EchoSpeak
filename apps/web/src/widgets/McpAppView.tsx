import React, { useCallback, useEffect, useRef, useState } from "react";
import { openExternal } from "./env";
import type { McpAppRef } from "./types";

/** Fired when a view asks to put a message in the composer (ui/message); the person sends it. */
export const COMPOSE_EVENT = "echospeak:compose";

type Rpc = { jsonrpc: "2.0"; id?: number | string; method?: string; params?: any; result?: unknown; error?: unknown };
const MAX_HEIGHT = 1600;

/** The view's tool result as a CallToolResult, whatever shape the backend stored. */
export function asToolResult(result: unknown): { content: unknown[]; structuredContent?: unknown; isError?: boolean } {
  if (result && typeof result === "object" && Array.isArray((result as any).content)) return result as any;
  return { content: [{ type: "text", text: typeof result === "string" ? result : JSON.stringify(result ?? null) }] };
}

/** What the host tells a view about itself in reply to ui/initialize (MCP Apps, SEP-1865). */
export function hostInitResult(protocolVersion: string, theme: "light" | "dark") {
  return {
    protocolVersion: protocolVersion || "2026-01-26",
    hostInfo: { name: "EchoSpeak", version: "11.8" },
    hostCapabilities: { openLinks: {}, serverTools: {}, serverResources: {}, logging: {} },
    hostContext: {
      theme,
      displayMode: "inline",
      availableDisplayModes: ["inline"],
      containerDimensions: { maxHeight: MAX_HEIGHT },
      locale: navigator.language || "en-US",
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      platform: "desktop",
    },
  };
}

const currentTheme = (): "light" | "dark" => (document.documentElement.dataset.esTheme === "dark" ? "dark" : "light");

/**
 * An MCP App: the server's own interactive page for a tool result (backend: agent/mcp_apps.py).
 * It runs in a sandboxed frame from the backend's origin under the CSP its server declared, and
 * talks to EchoSpeak only through postMessage JSON-RPC handled here.
 */
export function McpAppView({ data, apiBase }: { data: McpAppRef; apiBase: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const detail = useRef<{ input: unknown; result: unknown } | null>(null);
  const ready = useRef(false);
  const [src, setSrc] = useState("");
  const [error, setError] = useState("");
  const [height, setHeight] = useState(320);
  const base = `${apiBase}/lean/mcp-apps/${encodeURIComponent(data.id)}`;
  const scope = `session_id=${encodeURIComponent(data.session_id)}`;

  useEffect(() => {
    let live = true;
    ready.current = false;
    Promise.all([
      fetch(`${base}?${scope}`).then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status === 404 ? "This view has expired." : `HTTP ${r.status}`)))),
      fetch(`${base}/frame-token?${scope}`, { method: "POST" }).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))),
    ])
      .then(([info, token]) => {
        if (!live) return;
        detail.current = { input: info.input, result: info.result };
        setSrc(`${base}/frame?t=${encodeURIComponent(token.token)}`);
      })
      .catch((e) => live && setError(e instanceof Error ? e.message : "The view couldn't load."));
    return () => { live = false; };
  }, [base, scope]);

  const send = useCallback((message: Rpc) => {
    // The frame has an opaque origin (sandbox without allow-same-origin), so "*" is the only target;
    // what it receives is only this view's own tool input and result.
    frame.current?.contentWindow?.postMessage(message, "*");
  }, []);

  useEffect(() => {
    const post = async (path: string, body: unknown) => {
      const r = await fetch(`${base}/${path}?${scope}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const payload = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(String(payload?.detail || `HTTP ${r.status}`));
      return payload;
    };
    const reply = (id: Rpc["id"], result: unknown) => id !== undefined && send({ jsonrpc: "2.0", id, result });
    const fail = (id: Rpc["id"], message: string, code = -32000) => id !== undefined && send({ jsonrpc: "2.0", id, error: { code, message } });

    const onMessage = async (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return; // only this view's frame
      const msg = event.data as Rpc;
      if (!msg || msg.jsonrpc !== "2.0" || !msg.method) return;
      const { id, method, params } = msg;
      try {
        switch (method) {
          case "ui/initialize":
            return reply(id, hostInitResult(String(params?.protocolVersion || ""), currentTheme()));
          case "ui/notifications/initialized":
            if (ready.current || !detail.current) return;
            ready.current = true; // nothing is sent to a view before it says it is ready
            send({ jsonrpc: "2.0", method: "ui/notifications/tool-input", params: { arguments: detail.current.input ?? {} } });
            send({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: asToolResult(detail.current.result) });
            return;
          case "ui/notifications/size-changed":
            if (Number.isFinite(params?.height)) setHeight(Math.max(80, Math.min(MAX_HEIGHT, Math.ceil(params.height))));
            return;
          case "tools/call":
            return reply(id, await post("call", { name: String(params?.name || ""), arguments: params?.arguments || {} }));
          case "resources/read":
            return reply(id, await post("read", { uri: String(params?.uri || "") }));
          case "ui/update-model-context":
            await post("context", { content: params?.content ?? null, structuredContent: params?.structuredContent ?? null });
            return reply(id, {});
          case "ui/open-link": {
            const url = String(params?.url || "");
            if (!/^https?:\/\//i.test(url)) return fail(id, "Only web links can be opened.");
            openExternal(url);
            return reply(id, {});
          }
          case "ui/message": {
            const text = String(params?.content?.text || "").slice(0, 4000);
            if (text) window.dispatchEvent(new CustomEvent(COMPOSE_EVENT, { detail: text })); // you press send
            return reply(id, {});
          }
          case "ui/request-display-mode":
            return reply(id, { mode: "inline" });
          case "notifications/message":
            return;
          default:
            return fail(id, `${method} is not supported by EchoSpeak`, -32601);
        }
      } catch (e) {
        fail(id, e instanceof Error ? e.message : "Request failed");
      }
    };
    window.addEventListener("message", onMessage);
    return () => {
      window.removeEventListener("message", onMessage);
      if (ready.current) send({ jsonrpc: "2.0", id: "teardown", method: "ui/resource-teardown", params: { reason: "closed" } });
    };
  }, [base, scope, send]);

  return (
    <figure className={`wg es-mcp-app${data.prefers_border ? " has-border" : ""}`}>
      <figcaption><strong>{data.server}</strong><small>{data.tool}</small></figcaption>
      {error ? <p className="es-mcp-app-note" role="status">{error}</p>
        : !src ? <p className="es-mcp-app-note" role="status">Loading the app…</p>
        : <iframe ref={frame} src={src} title={`${data.server}: ${data.tool}`} sandbox="allow-scripts allow-forms" referrerPolicy="no-referrer" allow="" style={{ height }} />}
    </figure>
  );
}
