import React, { useLayoutEffect, useRef, useState } from "react";
import { ExternalLink, RemoteImage } from "./env";
import type { ComparisonData, MapData, StatData, TimelineData } from "./types";

export function Timeline({ data }: { data: TimelineData }) {
  const List = data.ordered ? "ol" : "ul";
  return (
    <section className={`wg wg-timeline${data.ordered ? " is-steps" : ""}`} aria-label={data.title || (data.ordered ? "Steps" : "Timeline")}>
      {data.title ? <header className="wg-head"><figcaption>{data.title}</figcaption></header> : null}
      <List className="wg-timeline-list">
        {data.items.map((item, i) => (
          <li key={i}>
            <span className="wg-timeline-mark" aria-hidden>{data.ordered ? i + 1 : ""}</span>
            <div>
              {item.when ? <small>{item.when}</small> : null}
              <strong>{item.title}</strong>
              {item.detail ? <p>{item.detail}</p> : null}
            </div>
          </li>
        ))}
      </List>
    </section>
  );
}

export function Comparison({ data }: { data: ComparisonData }) {
  const keys = Array.from(new Set(data.items.flatMap((item) => Object.keys(item.specs))));
  return (
    <section className="wg wg-compare" aria-label={data.title || "Comparison"}>
      {data.title ? <header className="wg-head"><figcaption>{data.title}</figcaption></header> : null}
      <div className="wg-table-wrap">
        <table className="wg-table wg-compare-table">
          <thead>
            <tr>
              <th scope="col"><span className="wg-sr">Feature</span></th>
              {data.items.map((item) => (
                <th key={item.name} scope="col">
                  {item.image ? <RemoteImage src={item.image} alt="" className="wg-compare-img" fit="contain" /> : null}
                  {item.url ? <ExternalLink href={item.url}>{item.name}</ExternalLink> : item.name}
                  {item.summary ? <small>{item.summary}</small> : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr key={key}>
                <th scope="row">{key}</th>
                {data.items.map((item) => <td key={item.name}>{item.specs[key] || "–"}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function StatTiles({ data }: { data: StatData }) {
  return (
    <section className="wg wg-stats" aria-label={data.title || "Key numbers"}>
      {data.title ? <header className="wg-head"><figcaption>{data.title}</figcaption></header> : null}
      <div className="wg-stat-grid">
        {data.items.map((item, i) => {
          const tone = /^[+▲]/.test(item.change || "") ? "up" : /^[-−▼]/.test(item.change || "") ? "down" : "";
          return (
            <div key={i} className="wg-stat">
              <small>{item.label}</small>
              <strong>{item.value}{item.unit ? <span> {item.unit}</span> : null}</strong>
              {item.change ? <em data-tone={tone}>{tone === "up" ? "▲ " : tone === "down" ? "▼ " : ""}{item.change.replace(/^[+\-−▲▼]\s*/, "")}</em> : null}
              {item.note ? <p>{item.note}</p> : null}
            </div>
          );
        })}
      </div>
      {data.source ? <footer className="wg-foot">{data.source}</footer> : null}
    </section>
  );
}

const TILE = 256;
const worldX = (lon: number, z: number) => ((lon + 180) / 360) * TILE * 2 ** z;
const worldY = (lat: number, z: number) => {
  const rad = (lat * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * TILE * 2 ** z;
};

/** Places on OpenStreetMap tiles (fetched through the image proxy), numbered to match the list. */
export function MapCard({ data }: { data: MapData }) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(560);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth || 560);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => setWidth(Math.round(entries[0].contentRect.width) || 560));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const height = 220;
  const lats = data.places.map((p) => p.lat);
  const lons = data.places.map((p) => p.lon);
  let zoom = 15;
  while (zoom > 2) {
    const w = worldX(Math.max(...lons), zoom) - worldX(Math.min(...lons), zoom);
    const h = worldY(Math.min(...lats), zoom) - worldY(Math.max(...lats), zoom);
    if (w <= width - 70 && h <= height - 60) break;
    zoom -= 1;
  }
  if (data.places.length === 1) zoom = 13;
  const cx = (worldX(Math.max(...lons), zoom) + worldX(Math.min(...lons), zoom)) / 2;
  const cy = (worldY(Math.max(...lats), zoom) + worldY(Math.min(...lats), zoom)) / 2;
  const left = cx - width / 2;
  const top = cy - height / 2;
  const tiles: { x: number; y: number; px: number; py: number }[] = [];
  const max = 2 ** zoom;
  for (let tx = Math.floor(left / TILE); tx <= Math.floor((left + width) / TILE); tx += 1) {
    for (let ty = Math.floor(top / TILE); ty <= Math.floor((top + height) / TILE); ty += 1) {
      if (ty < 0 || ty >= max) continue;
      tiles.push({ x: ((tx % max) + max) % max, y: ty, px: tx * TILE - left, py: ty * TILE - top });
    }
  }
  const center = data.places[0];
  return (
    <section className="wg wg-map" aria-label={data.title || "Map"}>
      {data.title ? <header className="wg-head"><figcaption>{data.title}</figcaption></header> : null}
      <div ref={ref} className="wg-map-view" style={{ height }}>
        {tiles.map((t) => (
          <span key={`${t.x}-${t.y}-${t.px}`} className="wg-map-tile" style={{ left: t.px, top: t.py }}>
            <RemoteImage src={`https://tile.openstreetmap.org/${zoom}/${t.x}/${t.y}.png`} alt="" />
          </span>
        ))}
        {data.places.map((p, i) => (
          <span key={i} className="wg-map-pin" style={{ left: worldX(p.lon, zoom) - left, top: worldY(p.lat, zoom) - top }} title={p.name}>
            {i + 1}
          </span>
        ))}
        <span className="wg-map-credit">© OpenStreetMap</span>
      </div>
      <ol className="wg-map-list">
        {data.places.map((p, i) => (
          <li key={i}>
            <b>{i + 1}</b>
            <div>
              <strong>{p.name}</strong>
              {p.address ? <small>{p.address}</small> : null}
              {p.note ? <p>{p.note}</p> : null}
            </div>
            <ExternalLink href={`https://www.openstreetmap.org/?mlat=${p.lat}&mlon=${p.lon}#map=16/${p.lat}/${p.lon}`}>Open map</ExternalLink>
          </li>
        ))}
      </ol>
      {data.places.length > 1 ? (
        <footer className="wg-foot">
          <ExternalLink href={`https://www.google.com/maps/dir/${data.places.map((p) => `${p.lat},${p.lon}`).join("/")}`}>Directions between these places</ExternalLink>
        </footer>
      ) : (
        <footer className="wg-foot"><ExternalLink href={`https://www.google.com/maps/dir/?api=1&destination=${center.lat},${center.lon}`}>Directions</ExternalLink></footer>
      )}
    </section>
  );
}
