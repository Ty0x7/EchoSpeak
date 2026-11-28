import React from "react";

/**
 * Research figures drawn from the published numbers (sources in each post's evidence list).
 * Colours come from the site tokens, so they follow light and dark themes.
 */

type Row = { label: string; a: number; b: number };

function Pair({ rows, max, aName, bName, caption }: {
  rows: Row[]; max: number; aName: string; bName: string; caption: string;
}) {
  return (
    <figure className="r-fig">
      <div className="r-chart-key" aria-hidden="true"><span>{aName}</span><span>{bName}</span></div>
      <dl className="r-pair-chart">
        {rows.map(({ label, a, b }) => (
          <div key={label}>
            <dt>{label}</dt>
            {([[aName, a], [bName, b]] as const).map(([name, value]) => (
              <dd key={name}>
                <span aria-hidden="true"><i style={{ width: `${(value / max) * 100}%` }} /></span>
                <b><span className="r-chart-series">{name}: </span>{value}</b>
              </dd>
            ))}
          </div>
        ))}
      </dl>
      <figcaption>{caption}</figcaption>
    </figure>
  );
}

function VramBudget() {
  const total = 8192;
  const parts = [
    { label: "Desktop", mib: 1300, cls: "r-seg-desk" },
    { label: "Model weights", mib: 5440, cls: "r-seg-w" },
    { label: "4-bit context cache", mib: 576, cls: "r-seg-kv" },
    { label: "Recurrent state", mib: 150, cls: "r-seg-rs" },
    { label: "Compute buffers", mib: 184, cls: "r-seg-cb" },
  ];
  const used = parts.reduce((sum, part) => sum + part.mib, 0);
  const budget = [...parts, { label: "Headroom", mib: total - used, cls: "r-seg-free" }];
  return (
    <figure className="r-fig">
      <div className="r-stacked-chart" aria-hidden="true">
        {budget.map(({ label, mib, cls }) => <span key={label} className={cls} style={{ width: `${mib / total * 100}%` }} />)}
      </div>
      <ul className="r-chart-budget">
        {budget.map(({ label, mib, cls }) => <li key={label}><i className={cls} aria-hidden="true" />{label} · {mib.toLocaleString()} MiB</li>)}
      </ul>
      <figcaption>8,192 MiB card, Bonsai 2.5 27B at 32K. Headroom varies with desktop memory use.</figcaption>
    </figure>
  );
}

function Ladder() {
  const steps = [
    ["V0", "Claimed", "The model says so"],
    ["V1", "Observed", "A tool reported success"],
    ["V2", "Checked", "A check ran after the final change"],
    ["V3", "Corroborated", "Two kinds of evidence agree"],
    ["V4", "Confirmed", "The owner accepted it"],
  ];
  return (
    <figure className="r-fig">
      <ol className="r-ladder">
        {steps.map(([level, name, note]) => <li key={level}><b>{level}</b><strong>{name}</strong><span>{note}</span></li>)}
      </ol>
      <figcaption>The verification ladder records the evidence behind a result.</figcaption>
    </figure>
  );
}

function Pipeline() {
  return (
    <figure className="r-fig">
      <ol className="r-pipeline">
        <li><strong>Task fixture</strong><span>Disposable project and synthetic data</span></li>
        <li><strong>Isolated agent</strong><span>Private model server and recorded calls</span></li>
        <li><strong>Independent judge</strong><span>Tests, file hashes and server logs</span></li>
        <li><strong>Scorecard</strong><span>Outcomes, resource use and security findings</span></li>
      </ol>
      <figcaption>The judge checks the agent's claims against the project, recorded calls and server logs.</figcaption>
    </figure>
  );
}

function Platform() {
  return (
    <figure className="r-fig">
      <dl className="r-platform">
        <div><dt>Research platform</dt><dd>8 GB graphics memory and 16 GB system memory, with the desktop running alongside the model.</dd></div>
        <div><dt>Supported setups</dt><dd>Local models sized for your machine and optional cloud providers.</dd></div>
      </dl>
      <figcaption>The test platform focuses these experiments on accessible hardware.</figcaption>
    </figure>
  );
}

export function Figure({ name }: { name: string }) {
  switch (name) {
    case "harness-lift":
      return <Pair aName="Before" bName="After harness changes" max={10}
        caption="Scores out of 10 with Qwen 3.5 9B on the same 14 tasks, each run three times."
        rows={[{ label: "Judge score", a: 7.94, b: 8.95 }, { label: "Held-out score", a: 7.41, b: 9.38 }]} />;
    case "bonsai-vs-qwen":
      return <Pair aName="Qwen 3.5 9B" bName="Bonsai 2.5 27B" max={10}
        caption="Same harness, 59 tasks × 2 runs, independent judge. Scores out of 10 by capability."
        rows={[{ label: "Reasoning", a: 8.65, b: 9.55 }, { label: "Agent tasks", a: 8.5, b: 9.47 }, { label: "Coding", a: 8.89, b: 9.23 },
               { label: "Context & memory", a: 8.54, b: 8.77 }, { label: "Security", a: 9.56, b: 9.64 }]} />;
    case "efficiency":
      return <Pair aName="Qwen 3.5 9B" bName="Bonsai 2.5 27B" max={30}
        caption="Useful work per unit of compute (same 59 tasks): successful tasks per million tokens and per hour of task time."
        rows={[{ label: "Per 1M tokens", a: 6.5, b: 12.4 }, { label: "Per hour", a: 20.5, b: 26.2 }]} />;
    case "vram-budget": return <VramBudget />;
    case "verification-ladder": return <Ladder />;
    case "eval-pipeline": return <Pipeline />;
    case "platform": return <Platform />;
    default: return null;
  }
}
