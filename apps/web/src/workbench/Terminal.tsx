import React, { useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { request } from "../projects/model";
import { createEchoSpeakWebSocket } from "../desktop/bridge";
import { contextKey, useWorkbench, workbenchUrl, type WorkbenchContext } from "./store";
import { WorkbenchIcon } from "./icons";

type Snapshot = { id: string; state: string; cursor: number; gap: boolean; output: { seq: number; text: string }[] };

export function TerminalView({ apiBase, context, id }: { apiBase: string; context: WorkbenchContext; id: string }) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [state, setState] = useState("Connecting");
  useEffect(() => {
    if (!host.current) return;
    let live = true;
    setError(""); setState("Connecting");
    const term = new XTerm({ fontFamily: 'Consolas, "Cascadia Mono", monospace', fontSize: 12, cursorBlink: true,
      scrollback: 3000, allowProposedApi: false, theme: { background: "#0a0a0a", foreground: "#d4d4d4", cursor: "#b7c6fa" } });
    const fit = new FitAddon(); term.loadAddon(fit); term.open(host.current);
    term.options.disableStdin = true;
    const socket = createEchoSpeakWebSocket(workbenchUrl(apiBase, context, `terminals/${id}/socket`).replace(/^http/, "ws"));
    const send = (operation: string, extra = {}) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ operation, ...extra }));
    };
    // Bound pasted input and drain it without flooding the local API or socket.
    const pending: string[] = [];
    const input = term.onData(text => { for (let offset = 0; offset < text.length; offset += 4096) pending.push(text.slice(offset, offset + 4096)); });
    const drain = setInterval(() => { if (pending.length && socket.readyState === WebSocket.OPEN && socket.bufferedAmount < 65_536) send("write", { text: pending.shift() }); }, 20);
    const resize = new ResizeObserver(() => { fit.fit(); send("resize", { rows: term.rows, cols: term.cols }); });
    resize.observe(host.current);
    socket.onopen = () => { fit.fit(); send("resize", { rows: term.rows, cols: term.cols }); };
    socket.onmessage = event => {
      try {
        const data = JSON.parse(event.data) as Snapshot;
        if (!live) return;
        if (data.gap) term.write("\r\n[Earlier terminal output left the replay buffer]\r\n");
        data.output.forEach(chunk => term.write(chunk.text));
        setState(data.state); term.options.disableStdin = data.state !== "running";
      } catch (err) { if (live) setError(err instanceof Error ? err.message : "Terminal disconnected"); }
    };
    socket.onerror = () => { if (live) setError("Terminal connection unavailable. Reopen the panel to reconnect."); };
    socket.onclose = () => { if (live) { term.options.disableStdin = true; setState("Disconnected"); } };
    fit.fit(); term.focus();
    return () => { live = false; clearInterval(drain); socket.close(); resize.disconnect(); input.dispose(); term.dispose(); };
  }, [apiBase, id, context.project_id, context.external_session_id]);
  return <div className="wb-terminal-view"><div className="wb-terminal-screen" ref={host} aria-label="Interactive terminal" />
    <div className="wb-terminal-status">{state}{error ? <span role="alert"> · {error}</span> : null}</div></div>;
}

export function TerminalDock({ apiBase, side = false }: { apiBase: string; side?: boolean }) {
  const store = useWorkbench();
  const context = store.context;
  const ids = store.terminalIds[contextKey(context)] || [];
  const [selected, setSelected] = useState("");
  const [split, setSplit] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [shells, setShells] = useState<{ id: string; label: string }[] | null>(null);
  const [shell, setShell] = useState("");
  useEffect(() => {
    if (!context.project_id) return;
    let live = true;
    request<{ items: { id: string; label: string }[] }>(`${apiBase}/workbench/shells`).then(data => {
      if (!live) return;
      setShells(data.items);
      setShell(value => data.items.some(item => item.id === value) ? value : data.items[0]?.id || "");
    }).catch(err => { if (live) { setShells([]); setError(err.message); } });
    return () => { live = false; };
  }, [apiBase, context.project_id]);
  const shellLabel = shells?.find(item => item.id === shell)?.label;
  const canCreate = Boolean(context.project_id && shellLabel && !busy);
  const active = ids.includes(selected) ? selected : ids[ids.length - 1] || "";
  const create = async () => {
    setBusy(true); setError("");
    try {
      const result = await request<Snapshot & { shell: string }>(`${apiBase}/workbench/terminals`, { ...context, rows: 24, cols: 100, shell });
      store.addTerminal(result.id, context, result.shell); if (contextKey(useWorkbench.getState().context) === contextKey(context)) setSelected(result.id);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not open terminal"); }
    finally { setBusy(false); }
  };
  return <section className={`wb-terminal-dock${side ? " is-side" : ""}`} aria-label={side ? "Side terminal" : "Bottom terminal"}>
    {!side ? <div className="wb-terminal-resize" role="separator" aria-label="Resize terminal" aria-orientation="horizontal" tabIndex={0}
      onKeyDown={event => { if (event.key === "ArrowUp" || event.key === "ArrowDown") { event.preventDefault(); store.setBottomHeight(store.bottomHeight + (event.key === "ArrowUp" ? 24 : -24)); } }}
      onPointerDown={event => { const start = event.clientY, height = store.bottomHeight; event.currentTarget.setPointerCapture(event.pointerId);
        const target = event.currentTarget; const move = (e: PointerEvent) => store.setBottomHeight(height + start - e.clientY);
        const end = () => { target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", end); target.removeEventListener("pointercancel", end); };
        target.addEventListener("pointermove", move); target.addEventListener("pointerup", end); target.addEventListener("pointercancel", end); }} /> : null}
    <header><WorkbenchIcon kind="terminal" /><div className="wb-terminal-tabs" role="tablist" aria-label="Terminals">{ids.map((id, index) => <button key={id} role="tab" aria-selected={active === id} onClick={() => setSelected(id)}>{store.terminalLabels[id] || "Terminal"} {index + 1}</button>)}</div>
      {context.project_id && shells?.length ? <select className="wb-shell-select" aria-label="Terminal shell" title="Shell for the next terminal" value={shell} onChange={event => setShell(event.target.value)}>
        {shells.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select> : null}
      <button title="New terminal" aria-label="New terminal" disabled={!canCreate} onClick={() => void create()}>＋</button>
      <button title="Split terminals" aria-label="Split terminals" aria-pressed={split} disabled={ids.length < 2} onClick={() => setSplit(value => !value)}>◫</button>
      <button title={side ? "Move terminal to bottom" : "Move terminal to side"} aria-label={side ? "Move terminal to bottom" : "Move terminal to side"} onClick={() => {
        if (side) { useWorkbench.setState({ bottomOpen: true }); store.close("terminal"); }
        else { useWorkbench.setState({ bottomOpen: false }); store.open("terminal"); }
      }}><WorkbenchIcon kind={side ? "bottom" : "right"} /></button>
      <button title="Stop and close terminal" aria-label="Stop and close terminal" disabled={!active} onClick={() => void request(`${apiBase}/workbench/terminals/${active}`, { ...context, operation: "stop" })
        .then(() => store.removeTerminal(active, context)).catch(err => setError(err.message))}>⌫</button>
      {!side ? <button title="Hide terminal" aria-label="Hide terminal" onClick={store.toggleBottom}>×</button> : null}
    </header>
    {error ? <p role="alert" className="wb-error">{error}</p> : null}
    {ids.length ? <div className={`wb-terminal-panes${split ? " is-split" : ""}`}>{ids.map(id => <div key={id} hidden={id !== active && !(split && id === ids.find(item => item !== active))}>
      <TerminalView apiBase={apiBase} context={context} id={id} /></div>)}</div> : <div className="wb-empty"><strong>Terminal</strong><p>{!context.project_id ? "Choose a project folder to open its terminal."
      : shells === null ? "Checking installed shells…" : shells.length ? "Open a shell in this workspace." : "No supported shells found."}</p>
      {shellLabel ? <button className="es-btn" disabled={!canCreate} onClick={() => void create()}>Open {shellLabel}</button> : null}</div>}
  </section>;
}
