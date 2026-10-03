import React, { useLayoutEffect, useMemo, useRef, useState } from "react";
import { ExternalLink } from "./env";
import type { ChartData } from "./types";

/** Categorical slots, fixed order (dataviz reference palette, dark steps; validated on #141415). */
export const SERIES_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => setWidth(Math.round(entries[0].contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** "Nice" axis ticks (1, 2, 2.5, 5 × 10^n). */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0];
  if (min === max) {
    const pad = Math.abs(min) || 1;
    min -= pad / 2;
    max += pad / 2;
  }
  const raw = (max - min) / Math.max(1, count - 1);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
  const start = Math.floor(min / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= max + step * 0.5 && ticks.length < 12; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return ticks;
}

export function formatValue(v: number | null | undefined, unit = ""): string {
  if (v == null || !Number.isFinite(v)) return "–";
  const abs = Math.abs(v);
  const text = abs >= 1e9 ? `${(v / 1e9).toFixed(1)}B` : abs >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : abs >= 1e4 ? `${(v / 1e3).toFixed(1)}k` : abs >= 100 ? v.toFixed(0) : abs >= 1 ? v.toFixed(2).replace(/\.?0+$/, "") : v.toPrecision(2);
  if (unit === "%") return `${v > 0 ? "+" : ""}${text}%`;
  if (unit === "USD" || unit === "$") return `$${text}`;
  return unit ? `${text} ${unit}` : text;
}

function shortLabel(label: string): string {
  const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(label);
  if (!iso) return label;
  const d = new Date(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3]));
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function ChartTable({ data }: { data: ChartData }) {
  return (
    <div className="wg-table-wrap">
      <table className="wg-table">
        <thead>
          <tr>
            <th scope="col">{data.kind === "pie" ? "Item" : "Label"}</th>
            {data.series.map((s) => <th key={s.name} scope="col">{s.name || "Value"}</th>)}
          </tr>
        </thead>
        <tbody>
          {data.labels.map((label, i) => (
            <tr key={`${label}-${i}`}>
              <th scope="row">{label}</th>
              {data.series.map((s) => <td key={s.name}>{formatValue(s.values[i], data.unit)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Legend({ data }: { data: ChartData }) {
  const names = data.kind === "pie" ? data.labels : data.series.map((s) => s.name);
  if (names.length < 2) return null;
  return (
    <ul className="wg-legend">
      {names.map((name, i) => (
        <li key={`${name}-${i}`}>
          <i style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} aria-hidden />
          {name || `Series ${i + 1}`}
        </li>
      ))}
    </ul>
  );
}

function Donut({ data }: { data: ChartData }) {
  const values = data.series[0].values.map((v) => Math.max(0, v || 0));
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const [hover, setHover] = useState<number | null>(null);
  let angle = -Math.PI / 2;
  const r = 70;
  const inner = 46;
  const arcs = values.map((v, i) => {
    const sweep = (v / total) * Math.PI * 2;
    const a0 = angle + 0.012;
    const a1 = angle + sweep - 0.012;
    angle += sweep;
    const large = a1 - a0 > Math.PI ? 1 : 0;
    const p = (rad: number, a: number) => `${100 + rad * Math.cos(a)} ${100 + rad * Math.sin(a)}`;
    const d = sweep <= 0.03 ? "" : `M ${p(r, a0)} A ${r} ${r} 0 ${large} 1 ${p(r, a1)} L ${p(inner, a1)} A ${inner} ${inner} 0 ${large} 0 ${p(inner, a0)} Z`;
    return { d, i, v };
  });
  const shown = hover ?? values.indexOf(Math.max(...values));
  return (
    <div className="wg-donut">
      <svg viewBox="0 0 200 200" role="img" aria-label={data.title || "Pie chart"}>
        {arcs.map((a) =>
          a.d ? (
            <path key={a.i} d={a.d} fill={SERIES_COLORS[a.i % SERIES_COLORS.length]} opacity={hover === null || hover === a.i ? 1 : 0.45} onMouseEnter={() => setHover(a.i)} onMouseLeave={() => setHover(null)} />
          ) : null
        )}
        <text x="100" y="96" textAnchor="middle" className="wg-donut-value">{Math.round(((values[shown] || 0) / total) * 100)}%</text>
        <text x="100" y="116" textAnchor="middle" className="wg-donut-label">{(data.labels[shown] || "").slice(0, 18)}</text>
      </svg>
    </div>
  );
}

export function ChartView({ data }: { data: ChartData }) {
  const [wrapRef, width] = useWidth<HTMLDivElement>();
  const [showTable, setShowTable] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const pie = data.kind === "pie" && data.series.length === 1 && data.labels.length <= 8;
  const kind = data.kind === "pie" && !pie ? "bar" : data.kind;

  const geometry = useMemo(() => {
    const W = Math.max(260, width || 560);
    const H = W < 420 ? 200 : 240;
    const direct = (kind === "line" || kind === "area") && data.series.length > 1 && data.series.length <= 4;
    const pad = { top: 12, right: direct ? 56 : 14, bottom: 26, left: 44 };
    const all = data.series.flatMap((s) => s.values).filter((v): v is number => v != null);
    let lo = Math.min(...all);
    let hi = Math.max(...all);
    if (kind === "bar" || kind === "area") lo = Math.min(0, lo);
    const ticks = niceTicks(lo, hi);
    lo = Math.min(lo, ticks[0]);
    hi = Math.max(hi, ticks[ticks.length - 1]);
    const innerW = W - pad.left - pad.right;
    const innerH = H - pad.top - pad.bottom;
    const n = data.labels.length;
    const x = (i: number) => (kind === "bar" ? pad.left + (innerW / n) * (i + 0.5) : pad.left + (n <= 1 ? innerW / 2 : (innerW * i) / (n - 1)));
    const y = (v: number) => pad.top + innerH - ((v - lo) / (hi - lo || 1)) * innerH;
    const labelEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(innerW / 70))));
    return { W, H, pad, ticks, x, y, innerW, innerH, n, labelEvery, direct, lo };
  }, [width, data, kind]);

  const { W, H, pad, ticks, x, y, innerW, innerH, n, labelEvery, direct, lo } = geometry;
  const unit = data.unit || "";
  const onMove = (event: React.PointerEvent<SVGRectElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * innerW;
    const i = kind === "bar" ? Math.floor((px / innerW) * n) : Math.round((px / innerW) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, i)));
  };

  return (
    <figure className="wg wg-chart" aria-label={data.title || "Chart"}>
      <header className="wg-head">
        {data.title ? <figcaption>{data.title}</figcaption> : <span />}
        <button type="button" className="wg-ghost" onClick={() => setShowTable((v) => !v)} aria-pressed={showTable}>
          {showTable ? "Chart" : "Table"}
        </button>
      </header>
      {!showTable ? <Legend data={data} /> : null}
      <div ref={wrapRef} className="wg-chart-plot">
        {showTable ? (
          <ChartTable data={data} />
        ) : pie ? (
          <Donut data={data} />
        ) : (
          <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={data.title || "Chart"}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={pad.left} x2={W - pad.right} y1={y(t)} y2={y(t)} className={t === 0 ? "wg-axis-zero" : "wg-grid"} />
                <text x={pad.left - 6} y={y(t) + 3.5} textAnchor="end" className="wg-tick">{formatValue(t, unit === "%" ? "%" : "")}</text>
              </g>
            ))}
            {data.labels.map((label, i) =>
              i % labelEvery === 0 || i === n - 1 ? (
                <text key={`${label}-${i}`} x={x(i)} y={H - 8} textAnchor={i === 0 && kind !== "bar" ? "start" : i === n - 1 && kind !== "bar" ? "end" : "middle"} className="wg-tick">
                  {shortLabel(label)}
                </text>
              ) : null
            )}
            {kind === "bar"
              ? data.series.map((s, si) => {
                  const groupW = (innerW / n) * 0.72;
                  const barW = Math.max(2, groupW / data.series.length - 2);
                  return s.values.map((v, i) => {
                    if (v == null) return null;
                    const x0 = x(i) - groupW / 2 + si * (barW + 2);
                    const top = Math.min(y(v), y(Math.max(lo, 0)));
                    const h = Math.max(1, Math.abs(y(v) - y(Math.max(lo, 0))));
                    return <rect key={`${si}-${i}`} x={x0} y={top} width={barW} height={h} rx={Math.min(4, barW / 2)} fill={SERIES_COLORS[si % SERIES_COLORS.length]} opacity={hover === null || hover === i ? 1 : 0.55} />;
                  });
                })
              : data.series.map((s, si) => {
                  const pts = s.values.map((v, i) => (v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`));
                  const segments: string[] = [];
                  let current: string[] = [];
                  for (const p of pts) {
                    if (p) current.push(p);
                    else if (current.length) {
                      segments.push(current.join(" "));
                      current = [];
                    }
                  }
                  if (current.length) segments.push(current.join(" "));
                  const color = SERIES_COLORS[si % SERIES_COLORS.length];
                  const lastIndex = s.values.map((v, i) => (v == null ? -1 : i)).reduce((a, b) => Math.max(a, b), -1);
                  return (
                    <g key={s.name || si}>
                      {kind === "area" && data.series.length === 1
                        ? segments.map((seg, k) => (
                            <polygon key={k} points={`${seg.split(" ")[0].split(",")[0]},${y(Math.max(lo, 0))} ${seg} ${seg.split(" ").slice(-1)[0].split(",")[0]},${y(Math.max(lo, 0))}`} fill={color} opacity={0.16} />
                          ))
                        : null}
                      {segments.map((seg, k) => <polyline key={k} points={seg} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />)}
                      {direct && lastIndex >= 0 ? (
                        <text x={x(lastIndex) + 6} y={y(s.values[lastIndex] as number) + 4} className="wg-direct">{s.name}</text>
                      ) : null}
                    </g>
                  );
                })}
            {hover !== null && kind !== "bar" ? <line x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + innerH} className="wg-crosshair" /> : null}
            {hover !== null && kind !== "bar"
              ? data.series.map((s, si) =>
                  s.values[hover] != null ? <circle key={si} cx={x(hover)} cy={y(s.values[hover] as number)} r={4} fill={SERIES_COLORS[si % SERIES_COLORS.length]} className="wg-dot" /> : null
                )
              : null}
            <rect x={pad.left} y={pad.top} width={innerW} height={innerH} fill="transparent" onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
          </svg>
        )}
        {hover !== null && !showTable && !pie ? (
          <div className="wg-tooltip" style={{ left: Math.min(W - 150, Math.max(0, x(hover) + 10)) }} role="status">
            <strong>{data.labels[hover]}</strong>
            {data.series.map((s, si) => (
              <span key={si}>
                <i style={{ background: SERIES_COLORS[si % SERIES_COLORS.length] }} aria-hidden />
                {s.name || "Value"} <b>{formatValue(s.values[hover], unit)}</b>
              </span>
            ))}
          </div>
        ) : null}
      </div>
      {data.source || data.as_of ? (
        <footer className="wg-foot">
          {data.source_url ? <ExternalLink href={data.source_url}>{data.source || "Source"}</ExternalLink> : data.source}
          {data.as_of ? ` · as of ${data.as_of}` : ""}
        </footer>
      ) : null}
    </figure>
  );
}
