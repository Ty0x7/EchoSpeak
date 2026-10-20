import React, { useState } from "react";
import type { WorkerRow, WorkersState } from "./types";

const STATUS: Record<WorkerRow["status"], string> = {
  queued: "Waiting",
  running: "Researching",
  done: "Done",
  partial: "Partly done",
  failed: "Failed",
  skipped: "Skipped",
  cancelled: "Stopped",
};

const seconds = (s: number) => (s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);
const tokens = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));
const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

function WorkerItem({ worker }: { worker: WorkerRow }) {
  const [open, setOpen] = useState(false);
  const opened = worker.sources.filter((s) => s.opened);
  const line = worker.status === "running" ? worker.activity || "Starting" : worker.note || (worker.answer ? worker.answer.split("\n")[0] : "");
  const hasDetail = Boolean(worker.answer || worker.sources.length || worker.open_questions.length);
  return (
    <div className="lm-worker" data-status={worker.status}>
      <button type="button" className="lm-worker-head" onClick={() => setOpen((v) => !v)} aria-expanded={open} disabled={!hasDetail}>
        <span className="lm-worker-index" aria-hidden>{worker.index}</span>
        <span className="lm-worker-text">
          <span className="lm-worker-goal">{worker.goal}</span>
          {line ? <span className="lm-worker-sub">{line}</span> : null}
        </span>
        <span className="lm-worker-meta">
          <span className="lm-worker-status">{STATUS[worker.status] || worker.status}</span>
          <span>
            {worker.steps ? `${worker.steps} steps` : ""}
            {opened.length ? ` · ${opened.length} source${opened.length === 1 ? "" : "s"}` : ""}
            {worker.elapsed_s ? ` · ${seconds(worker.elapsed_s)}` : ""}
          </span>
        </span>
      </button>
      {open ? (
        <div className="lm-worker-body">
          {worker.answer ? <p>{worker.answer}</p> : null}
          {worker.confidence ? <p className="lm-worker-small">Worker's confidence: {worker.confidence}</p> : null}
          {worker.sources.length ? (
            <ul className="lm-worker-sources">
              {worker.sources.map((source) => (
                <li key={source.url}>
                  <a href={source.url} target="_blank" rel="noreferrer noopener">{source.title || host(source.url)}</a>
                  {source.opened ? null : <span className="lm-worker-small"> · cited, not opened</span>}
                </li>
              ))}
            </ul>
          ) : null}
          {worker.open_questions.length ? <p className="lm-worker-small">Couldn't confirm: {worker.open_questions.join("; ")}</p> : null}
          {worker.tokens ? <p className="lm-worker-small">{tokens(worker.tokens)} tokens · {worker.tools} tool calls</p> : null}
        </div>
      ) : null}
    </div>
  );
}

/** One collapsible card under the lead's message: the research workers it ran (agent/lean/swarm.py). */
export function WorkersCard({ state }: { state: WorkersState }) {
  const running = state.workers.some((w) => w.status === "running" || w.status === "queued");
  const [open, setOpen] = useState<boolean | null>(null);
  const expanded = open ?? running;
  const done = state.workers.filter((w) => w.status === "done").length;
  const slowest = Math.max(0, ...state.workers.map((w) => w.elapsed_s || 0));
  return (
    <div className="lm-workers" data-running={running ? "true" : "false"}>
      <button type="button" className="lm-workers-head" onClick={() => setOpen(!expanded)} aria-expanded={expanded}>
        <span className="lm-workers-title">Research workers</span>
        <span className="lm-workers-summary">
          {running ? `${state.workers.length} working` : `${done} of ${state.workers.length} done`}
          {slowest ? ` · ${seconds(slowest)}` : ""}
          {state.used ? ` · ${tokens(state.used)} of ${tokens(state.budget)} tokens` : ""}
        </span>
      </button>
      {expanded ? (
        <div className="lm-workers-list">
          {state.workers.map((worker) => <WorkerItem key={worker.id} worker={worker} />)}
          {state.stop_reason ? <div className="lm-worker-small lm-workers-stop">Stopped early: {state.stop_reason}.</div> : null}
          <div className="lm-worker-small lm-workers-foot">
            Workers only look things up. The answer above is written by the agent from their findings.
          </div>
        </div>
      ) : null}
    </div>
  );
}
