import React from "react";
import type { WeatherData } from "./types";

/** WMO weather codes (Open-Meteo) → short label + icon kind. */
export function describeCode(code: number): { label: string; icon: "sun" | "partly" | "cloud" | "fog" | "rain" | "snow" | "storm" } {
  if (code === 0) return { label: "Clear", icon: "sun" };
  if (code <= 2) return { label: code === 1 ? "Mostly clear" : "Partly cloudy", icon: "partly" };
  if (code === 3) return { label: "Overcast", icon: "cloud" };
  if (code === 45 || code === 48) return { label: "Fog", icon: "fog" };
  if (code >= 51 && code <= 57) return { label: "Drizzle", icon: "rain" };
  if (code >= 61 && code <= 67) return { label: code >= 65 ? "Heavy rain" : "Rain", icon: "rain" };
  if (code >= 71 && code <= 77) return { label: "Snow", icon: "snow" };
  if (code >= 80 && code <= 82) return { label: "Showers", icon: "rain" };
  if (code === 85 || code === 86) return { label: "Snow showers", icon: "snow" };
  if (code >= 95) return { label: "Thunderstorms", icon: "storm" };
  return { label: "—", icon: "cloud" };
}

export function WeatherIcon({ code, size = 22 }: { code: number; size?: number }) {
  const kind = describeCode(code).icon;
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  const cloud = <path d="M7 17.5h9.5a3.5 3.5 0 0 0 .4-7A5 5 0 0 0 7.3 9.6 4 4 0 0 0 7 17.5z" />;
  switch (kind) {
    case "sun":
      return (
        <svg {...common} className="wg-wx-sun">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4" />
        </svg>
      );
    case "partly":
      return (
        <svg {...common}>
          <path className="wg-wx-sun" d="M9 4.5v1.2M4.6 6.2l.9.9M3 10.5h1.2M13.4 6.2l-.9.9M6.8 11a2.6 2.6 0 0 1 4.5-2.5" />
          <path d="M8.5 19h8.5a3 3 0 0 0 .3-6A4.4 4.4 0 0 0 8.8 12a3.5 3.5 0 0 0-.3 7z" />
        </svg>
      );
    case "fog":
      return (
        <svg {...common}>
          {cloud}
          <path d="M5 20.5h14" />
        </svg>
      );
    case "rain":
      return (
        <svg {...common}>
          <path d="M7 15h9.5a3.5 3.5 0 0 0 .4-7A5 5 0 0 0 7.3 7.1 4 4 0 0 0 7 15z" />
          <path className="wg-wx-rain" d="M8.5 18l-1 2.5M12.5 18l-1 2.5M16.5 18l-1 2.5" />
        </svg>
      );
    case "snow":
      return (
        <svg {...common}>
          <path d="M7 15h9.5a3.5 3.5 0 0 0 .4-7A5 5 0 0 0 7.3 7.1 4 4 0 0 0 7 15z" />
          <path d="M9 18.5h.01M12 20h.01M15 18.5h.01" strokeWidth={2.4} />
        </svg>
      );
    case "storm":
      return (
        <svg {...common}>
          <path d="M7 14.5h9.5a3.5 3.5 0 0 0 .4-7A5 5 0 0 0 7.3 6.6 4 4 0 0 0 7 14.5z" />
          <path className="wg-wx-sun" d="m12.5 15-2 3.5h3l-2 3.5" />
        </svg>
      );
    default:
      return <svg {...common}>{cloud}</svg>;
  }
}

const round = (n: number | null | undefined) => (typeof n === "number" ? Math.round(n) : "–");

function dayName(date: string, index: number): string {
  if (index === 0) return "Today";
  const d = new Date(`${date}T12:00:00`);
  return Number.isNaN(d.getTime()) ? date : d.toLocaleDateString([], { weekday: "short" });
}

function hourLabel(time: string): string {
  const d = new Date(time);
  return Number.isNaN(d.getTime()) ? time.slice(11, 16) : d.toLocaleTimeString([], { hour: "numeric" });
}

export function WeatherCard({ data }: { data: WeatherData }) {
  const now = describeCode(data.current.code);
  const lows = data.daily.map((d) => d.min);
  const highs = data.daily.map((d) => d.max);
  const floor = Math.min(...lows);
  const ceil = Math.max(...highs);
  const span = Math.max(1, ceil - floor);
  return (
    <section className="wg wg-weather" aria-label={`Weather for ${data.location}`}>
      <div className="wg-weather-now">
        <div className="wg-weather-place">
          <span>{data.location}</span>
          <small>{now.label}</small>
        </div>
        <div className="wg-weather-temp">
          <WeatherIcon code={data.current.code} size={40} />
          <strong>{round(data.current.temp)}°<sup>{data.units}</sup></strong>
        </div>
        <dl className="wg-weather-facts">
          {data.current.feels != null ? <div><dt>Feels like</dt><dd>{round(data.current.feels)}°</dd></div> : null}
          {data.current.humidity != null ? <div><dt>Humidity</dt><dd>{round(data.current.humidity)}%</dd></div> : null}
          {data.current.wind != null ? <div><dt>Wind</dt><dd>{round(data.current.wind)} {data.current.wind_unit}</dd></div> : null}
        </dl>
      </div>
      {data.hourly.length ? (
        <div className="wg-weather-hours" role="list" aria-label="Next hours">
          {data.hourly.filter((_, i) => i % 3 === 0).slice(0, 8).map((h) => (
            <div key={h.time} role="listitem" className="wg-weather-hour">
              <small>{hourLabel(h.time)}</small>
              <WeatherIcon code={h.code} size={18} />
              <span>{round(h.temp)}°</span>
              {h.precip ? <em>{Math.round(h.precip)}%</em> : <em aria-hidden> </em>}
            </div>
          ))}
        </div>
      ) : null}
      {data.daily.length ? (
        <div className="wg-weather-days" role="list" aria-label="This week">
          {data.daily.map((d, i) => (
            <div key={d.date} role="listitem" className="wg-weather-day">
              <span className="wg-weather-dayname">{dayName(d.date, i)}</span>
              <WeatherIcon code={d.code} size={18} />
              <span className="wg-weather-rain">{d.precip ? `${Math.round(d.precip)}%` : ""}</span>
              <span className="wg-weather-lo">{round(d.min)}°</span>
              <span className="wg-weather-bar" aria-hidden>
                <i style={{ left: `${((d.min - floor) / span) * 100}%`, right: `${100 - ((d.max - floor) / span) * 100}%` }} />
              </span>
              <span className="wg-weather-hi">{round(d.max)}°</span>
            </div>
          ))}
        </div>
      ) : null}
      <footer className="wg-foot">{data.source || "Open-Meteo"}{data.as_of ? ` · as of ${data.as_of}` : ""}</footer>
    </section>
  );
}
