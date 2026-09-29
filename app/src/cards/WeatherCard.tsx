import { useMemo } from "react";
import type { Card } from "../types";

/* Weather card: hero (animated condition glyph + temp), stat strip, 24h temperature curve with rain chance,
   and a multi-day range bar forecast. Data comes straight from Open-Meteo via the jarvis_google weather tool. */

type Icon = "clear" | "partly" | "cloudy" | "fog" | "drizzle" | "rain" | "sleet" | "snow" | "storm";

export function Glyph({ icon, day = true, size = 28 }: { icon: string; day?: boolean; size?: number }) {
  const i = icon as Icon;
  const sun = (
    <g className="wx-sun">
      <circle cx="24" cy="24" r="8" />
      {Array.from({ length: 8 }, (_, k) => {
        const a = (k * Math.PI) / 4;
        return <line key={k} x1={24 + Math.cos(a) * 12} y1={24 + Math.sin(a) * 12} x2={24 + Math.cos(a) * 16} y2={24 + Math.sin(a) * 16} />;
      })}
    </g>
  );
  const moon = <path className="wx-moon" d="M29 12a12 12 0 1 0 7 21a10 10 0 0 1-7-21z" />;
  const cloud = (dx = 0, dy = 0, cls = "wx-cloud") => (
    <path className={cls} transform={`translate(${dx} ${dy})`}
      d="M14 34h21a7 7 0 0 0 0-14a10 10 0 0 0-19-2a7 7 0 0 0-2 16z" />
  );
  const drops = (n: number, cls: string) => (
    <g className={cls}>
      {Array.from({ length: n }, (_, k) => <line key={k} x1={16 + k * 7} y1="38" x2={14 + k * 7} y2="44" style={{ animationDelay: `${k * 0.18}s` }} />)}
    </g>
  );
  const flakes = (
    <g className="wx-snow">
      {[16, 24, 32].map((x, k) => <circle key={k} cx={x} cy="41" r="1.6" style={{ animationDelay: `${k * 0.3}s` }} />)}
    </g>
  );
  let body: React.ReactNode;
  switch (i) {
    case "clear": body = day ? sun : moon; break;
    case "partly": body = <><g transform="translate(-6 -6) scale(0.85)">{day ? sun : moon}</g>{cloud(2, 2)}</>; break;
    case "cloudy": body = <>{cloud(-4, -5, "wx-cloud back")}{cloud(2, 2)}</>; break;
    case "fog": body = <g className="wx-fog">{[18, 25, 32].map((y, k) => <line key={k} x1={8 + (k % 2) * 4} y1={y} x2={40 - (k % 2) * 4} y2={y} />)}</g>; break;
    case "drizzle": body = <>{cloud(0, -4)}{drops(3, "wx-rain light")}</>; break;
    case "rain": body = <>{cloud(0, -4)}{drops(4, "wx-rain")}</>; break;
    case "sleet": body = <>{cloud(0, -4)}{drops(2, "wx-rain")}{flakes}</>; break;
    case "snow": body = <>{cloud(0, -4)}{flakes}</>; break;
    case "storm": body = <>{cloud(0, -4, "wx-cloud dark")}<path className="wx-bolt" d="M25 30l-5 8h5l-3 8l9-11h-5l3-5z" /></>; break;
    default: body = cloud(0, 0);
  }
  return <svg className={`wx-glyph wx-${i}`} viewBox="0 0 48 48" width={size} height={size}>{body}</svg>;
}

const hourLabel = (iso: string) => {
  const h = Number(iso.slice(11, 13));
  return h === 0 ? "12a" : h < 12 ? `${h}a` : h === 12 ? "12p" : `${h - 12}p`;
};
const dayLabel = (iso: string, i: number) =>
  i === 0 ? "Today" : new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "short" });
const clock = (iso?: string) => {
  if (!iso) return "";
  const [h, m] = iso.slice(11, 16).split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")}${h < 12 ? "a" : "p"}`;
};

function HourlyCurve({ hours, unit }: { hours: any[]; unit: string }) {
  const W = 360, H = 118, top = 26, bottom = 30, padX = 12;
  const pts = useMemo(() => {
    const temps = hours.map((h) => h.temp);
    const lo = Math.min(...temps), hi = Math.max(...temps);
    const span = Math.max(hi - lo, 4);
    return hours.map((h, i) => ({
      x: padX + (i * (W - padX * 2)) / Math.max(hours.length - 1, 1),
      y: top + (1 - (h.temp - lo) / span) * (H - top - bottom),
      ...h,
    }));
  }, [hours]);
  if (pts.length < 2) return null;
  const line = pts.map((p, i) => {
    if (i === 0) return `M${p.x},${p.y}`;
    const q = pts[i - 1];
    const cx = (q.x + p.x) / 2;
    return `C${cx},${q.y} ${cx},${p.y} ${p.x},${p.y}`;
  }).join(" ");
  const area = `${line} L${pts[pts.length - 1].x},${H - bottom} L${pts[0].x},${H - bottom} Z`;
  const every = 3;
  return (
    <svg className="wx-curve" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id="wxfill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--cy)" stopOpacity="0.35" />
          <stop offset="100%" stopColor="var(--cy)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {pts.map((p, i) => p.pop > 0 && (
        <rect key={`r${i}`} className="wx-pop" x={p.x - 5} width="10" y={H - bottom - (p.pop / 100) * 34} height={(p.pop / 100) * 34} />
      ))}
      <path d={area} fill="url(#wxfill)" />
      <path d={line} className="wx-line" />
      {pts.map((p, i) => i % every === 0 && (
        <g key={i}>
          <circle cx={p.x} cy={p.y} r={i === 0 ? 3.5 : 2.2} className={i === 0 ? "wx-dot now" : "wx-dot"} />
          <text x={p.x} y={p.y - 8} className="wx-t">{p.temp}°</text>
          <text x={p.x} y={H - 16} className="wx-h">{i === 0 ? "Now" : hourLabel(p.time)}</text>
          {p.pop >= 20 && <text x={p.x} y={H - 4} className="wx-p">{p.pop}%</text>}
        </g>
      ))}
      <title>{`Next 24 hours (${unit})`}</title>
    </svg>
  );
}

export function WeatherCard({ card }: { card: Card }) {
  const w = card.data || {};
  const c = w.current || {};
  const today = w.today || {};
  const daily: any[] = w.daily || [];
  const lo = Math.min(...daily.map((d) => d.lo)), hi = Math.max(...daily.map((d) => d.hi));
  const span = Math.max(hi - lo, 1);
  const rainSoon = (w.hourly || []).slice(0, 12).find((h: any) => h.pop >= 50);
  return (
    <div className={`weather ${c.is_day ? "day" : "night"} wx-bg-${c.icon}`}>
      <div className="wx-hero">
        <Glyph icon={c.icon} day={c.is_day} size={76} />
        <div className="wx-now">
          <div className="wx-temp">{c.temp}<span>{w.unit_temp}</span></div>
          <div className="wx-cond">{c.condition}</div>
          <div className="wx-hilo">H {today.hi}° · L {today.lo}° · Feels {c.feels_like}°</div>
        </div>
      </div>
      {rainSoon && <div className="wx-alert">☂ {rainSoon.pop}% chance of rain around {hourLabel(rainSoon.time).replace("a", " AM").replace("p", " PM")}</div>}
      <div className="wx-stats">
        <div><b>{c.humidity}%</b><span>Humidity</span></div>
        <div><b>{c.wind} <small>{w.unit_wind}</small></b><span>Wind {c.wind_dir}</span></div>
        <div><b>{Math.round(today.uv ?? 0)}</b><span>UV index</span></div>
        <div><b>{clock(today.sunrise)}</b><span>Sunrise</span></div>
        <div><b>{clock(today.sunset)}</b><span>Sunset</span></div>
      </div>
      <div className="wx-sub">NEXT 24 HOURS</div>
      <HourlyCurve hours={w.hourly || []} unit={w.unit_temp} />
      <div className="wx-sub">{daily.length}-DAY FORECAST</div>
      <div className="wx-days">
        {daily.map((d, i) => (
          <div key={d.date} className="wx-day">
            <span className="wx-dname">{dayLabel(d.date, i)}</span>
            <Glyph icon={d.icon} size={22} />
            <span className="wx-dpop">{d.pop >= 20 ? `${d.pop}%` : ""}</span>
            <span className="wx-dlo">{d.lo}°</span>
            <span className="wx-range">
              <i style={{ left: `${((d.lo - lo) / span) * 100}%`, right: `${100 - ((d.hi - lo) / span) * 100}%` }} />
              {i === 0 && <b style={{ left: `${Math.min(100, Math.max(0, ((c.temp - lo) / span) * 100))}%` }} />}
            </span>
            <span className="wx-dhi">{d.hi}°</span>
          </div>
        ))}
      </div>
      <div className="wx-foot">
        {w.location?.source === "ip" ? "Approximate location · " : ""}Open-Meteo · updated {clock(w.current?.time)}
      </div>
    </div>
  );
}
