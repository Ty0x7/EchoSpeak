/**
 * Check and normalise widget data before rendering. Tool cards are already
 * validated by the backend; this is the second line, and the only line for
 * blocks the model writes itself (```chart, ```timeline, ...). Anything that
 * fails returns null and the caller shows plain text instead.
 */
import type { ChartData, ComparisonData, MapData, StatData, TimelineData, Widget } from "./types";

const str = (v: unknown, max = 200): string => (typeof v === "string" || typeof v === "number" ? String(v).replace(/\s+/g, " ").trim().slice(0, max) : "");
const num = (v: unknown): number | null => {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim()) {
    const n = Number(v.replace(/[$,%\s]/g, ""));
    return Number.isFinite(n) ? n : null;
  }
  return null;
};
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

/** Only absolute http(s) URLs; javascript:, data:, relative and malformed URLs become "". */
export function safeUrl(value: unknown): string {
  const text = str(value, 2048);
  if (!text) return "";
  try {
    const url = new URL(text);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : "";
  } catch {
    return "";
  }
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

export function validateChart(raw: unknown): ChartData | null {
  const d = obj(raw);
  let labels = arr(d.labels).map((l) => str(l, 40));
  let series = arr(d.series)
    .map((s) => {
      const o = obj(s);
      return { name: str(o.name, 60), values: arr(o.values).map(num) };
    })
    .filter((s) => s.values.some((v) => v !== null));
  // Shorthand the model may write: {"data": [{"label": "A", "value": 3}, ...]}
  if (!series.length && arr(d.data).length) {
    const rows = arr(d.data).map(obj);
    labels = rows.map((r) => str(r.label ?? r.name ?? r.x, 40));
    series = [{ name: str(d.name ?? d.title, 60), values: rows.map((r) => num(r.value ?? r.y)) }];
  }
  if (!labels.length || !series.length || labels.length > 500 || series.length > 8) return null;
  series = series.map((s) => ({ ...s, values: labels.map((_, i) => s.values[i] ?? null) }));
  const kind = ["line", "bar", "area", "pie"].includes(String(d.kind ?? d.type)) ? (String(d.kind ?? d.type) as ChartData["kind"]) : "bar";
  return {
    kind,
    title: str(d.title, 120),
    labels,
    series,
    unit: str(d.unit, 16),
    y_label: str(d.y_label, 40),
    as_of: str(d.as_of, 40),
    source: str(d.source, 80),
    source_url: safeUrl(d.source_url),
  };
}

export function validateTimeline(raw: unknown, ordered = false): TimelineData | null {
  const d = obj(raw);
  const items = arr(d.items ?? d.events ?? d.steps)
    .map((i) => {
      const o = obj(i);
      return { when: str(o.when ?? o.date ?? o.time, 40), title: str(o.title ?? o.name ?? o.step, 160), detail: str(o.detail ?? o.description ?? o.text, 600) };
    })
    .filter((i) => i.title)
    .slice(0, 30);
  return items.length ? { title: str(d.title, 120), ordered: ordered || d.ordered === true, items } : null;
}

export function validateComparison(raw: unknown): ComparisonData | null {
  const d = obj(raw);
  const items = arr(d.items ?? d.options)
    .map((i) => {
      const o = obj(i);
      const specs: Record<string, string> = {};
      for (const [k, v] of Object.entries(obj(o.specs ?? o.features)).slice(0, 20)) specs[str(k, 60)] = str(v, 160);
      return { name: str(o.name ?? o.title, 80), url: safeUrl(o.url), image: safeUrl(o.image), summary: str(o.summary, 300), specs };
    })
    .filter((i) => i.name)
    .slice(0, 4);
  return items.length >= 2 ? { title: str(d.title, 120), items } : null;
}

export function validateStat(raw: unknown): StatData | null {
  const d = obj(raw);
  const items = arr(d.items ?? d.stats)
    .map((i) => {
      const o = obj(i);
      return { label: str(o.label ?? o.name, 60), value: str(o.value, 40), unit: str(o.unit, 16), change: str(o.change, 24), note: str(o.note, 120) };
    })
    .filter((i) => i.label && i.value)
    .slice(0, 8);
  return items.length ? { title: str(d.title, 120), items, source: str(d.source, 80) } : null;
}

export function validateMap(raw: unknown): MapData | null {
  const d = obj(raw);
  const places = arr(d.places ?? d.locations)
    .map((p) => {
      const o = obj(p);
      return { name: str(o.name, 100), lat: num(o.lat ?? o.latitude) ?? NaN, lon: num(o.lon ?? o.lng ?? o.longitude) ?? NaN, address: str(o.address, 160), note: str(o.note, 200) };
    })
    .filter((p) => p.name && Math.abs(p.lat) <= 85 && Math.abs(p.lon) <= 180)
    .slice(0, 12);
  return places.length ? { title: str(d.title, 120), places } : null;
}

/** Tool cards: shapes are fixed by the backend; check the essentials and URLs. */
export function validateWidget(raw: unknown): Widget | null {
  const w = obj(raw);
  const type = String(w.type || "");
  const data = obj(w.data);
  switch (type) {
    case "weather": {
      const current = obj(data.current);
      if (num(current.temp) === null) return null;
      return {
        type,
        data: {
          location: str(data.location, 120),
          units: data.units === "F" ? "F" : "C",
          current: { temp: num(current.temp)!, feels: num(current.feels), code: num(current.code) ?? 0, humidity: num(current.humidity), wind: num(current.wind), wind_unit: str(current.wind_unit, 8) },
          hourly: arr(data.hourly).map(obj).filter((h) => num(h.temp) !== null).map((h) => ({ time: str(h.time, 32), temp: num(h.temp)!, code: num(h.code) ?? 0, precip: num(h.precip) ?? 0 })).slice(0, 24),
          daily: arr(data.daily).map(obj).filter((h) => num(h.max) !== null && num(h.min) !== null).map((h) => ({ date: str(h.date, 16), max: num(h.max)!, min: num(h.min)!, code: num(h.code) ?? 0, precip: num(h.precip) ?? 0 })).slice(0, 10),
          as_of: str(data.as_of, 40),
          source: str(data.source, 60),
        },
      };
    }
    case "chart": {
      const chart = validateChart(data);
      return chart ? { type, data: chart } : null;
    }
    case "product_carousel": {
      const items = arr(data.items)
        .map(obj)
        .map((i) => ({ title: str(i.title, 160), url: safeUrl(i.url), image: safeUrl(i.image), price: num(i.price) ?? NaN, currency: str(i.currency || "USD", 8), merchant: str(i.merchant, 60), rating: num(i.rating), reviews: num(i.reviews) }))
        .filter((i) => i.title && i.url && Number.isFinite(i.price))
        .slice(0, 12);
      return items.length ? { type, data: { query: str(data.query, 120), items, as_of: str(data.as_of, 40) } } : null;
    }
    case "media": {
      const items = arr(data.items)
        .map(obj)
        .map((i) => ({ title: str(i.title, 160), url: safeUrl(i.url), thumbnail: safeUrl(i.thumbnail), image: safeUrl(i.image), duration: str(i.duration, 12), publisher: str(i.publisher, 60), published: str(i.published, 32), width: num(i.width), height: num(i.height) }))
        .filter((i) => i.url && i.thumbnail)
        .slice(0, 12);
      return items.length ? { type, data: { kind: data.kind === "video" ? "video" : "image", query: str(data.query, 120), items } } : null;
    }
    case "citations": {
      const items = arr(data.items)
        .map(obj)
        .map((i) => ({ title: str(i.title, 140), url: safeUrl(i.url), site: str(i.site, 80), snippet: str(i.snippet, 220), image: safeUrl(i.image) }))
        .filter((i) => i.url);
      return items.length ? { type, data: { items } } : null;
    }
    case "score_card": {
      const games = arr(data.games).map(obj).map((g) => ({ home: str(g.home, 60), away: str(g.away, 60), home_score: num(g.home_score), away_score: num(g.away_score), status: str(g.status, 40), start: str(g.start, 40), league: str(g.league, 60) })).filter((g) => g.home && g.away);
      return games.length ? { type, data: { title: str(data.title, 120), games, as_of: str(data.as_of, 40) } } : null;
    }
    case "timeline": {
      const t = validateTimeline(data);
      return t ? { type, data: t } : null;
    }
    case "comparison": {
      const c = validateComparison(data);
      return c ? { type, data: c } : null;
    }
    case "stat": {
      const s = validateStat(data);
      return s ? { type, data: s } : null;
    }
    case "map": {
      const m = validateMap(data);
      return m ? { type, data: m } : null;
    }
    case "artifact": {
      const id = str(data.id, 64);
      return id ? { type, data: { id, title: str(data.title, 120) || "Artifact", kind: str(data.kind, 16), version: num(data.version) ?? 1, language: str(data.language, 24) } } : null;
    }
    default:
      return null;
  }
}

/** Fenced blocks the model may write: ```chart, ```timeline, ```steps, ```comparison, ```stat, ```map. */
export const FENCED_WIDGETS = new Set(["chart", "timeline", "steps", "comparison", "stat", "map"]);

export function widgetFromFence(lang: string, source: string): Widget | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch {
    return null;
  }
  switch (lang) {
    case "chart": {
      const data = validateChart(parsed);
      return data ? { type: "chart", data } : null;
    }
    case "timeline":
    case "steps": {
      const data = validateTimeline(parsed, lang === "steps");
      return data ? { type: "timeline", data } : null;
    }
    case "comparison": {
      const data = validateComparison(parsed);
      return data ? { type: "comparison", data } : null;
    }
    case "stat": {
      const data = validateStat(parsed);
      return data ? { type: "stat", data } : null;
    }
    case "map": {
      const data = validateMap(parsed);
      return data ? { type: "map", data } : null;
    }
    default:
      return null;
  }
}
