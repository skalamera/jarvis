import { useContext, useMemo, useState } from "react";
import { core } from "../ws/core";
import { useStore } from "../state/store";
import { MaxCtx } from "./HoloCard";
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
  // Lines/areas live in a stretchable SVG; dots and labels are HTML positioned in %, so they never
  // distort when the card is wide (e.g. maximized).
  const L = (x: number) => `${(x / W) * 100}%`, T = (y: number) => `${(y / H) * 100}%`;
  return (
    <div className="wx-curve">
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
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
      <path d={line} className="wx-line" vectorEffect="non-scaling-stroke" />
      <title>{`Next 24 hours (${unit})`}</title>
    </svg>
      {pts.map((p, i) => i % every === 0 && (
        <span key={i}>
          <i className={i === 0 ? "wx-dot now" : "wx-dot"} style={{ left: L(p.x), top: T(p.y) }} />
          <b className="wx-t" style={{ left: L(p.x), top: T(p.y - 8) }}>{p.temp}°</b>
          <b className="wx-h" style={{ left: L(p.x), top: T(H - 16) }}>{i === 0 ? "Now" : hourLabel(p.time)}</b>
          {p.pop >= 20 && <b className="wx-p" style={{ left: L(p.x), top: T(H - 4) }}>{p.pop}%</b>}
        </span>
      ))}
    </div>
  );
}

export function WeatherCard({ card }: { card: Card }) {
  const w = card.data || {};
  const c = w.current || {};
  const today = w.today || {};
  const daily: any[] = w.daily || [];
  const lo = Math.min(...daily.map((d) => d.lo)), hi = Math.max(...daily.map((d) => d.hi));
  const span = Math.max(hi - lo, 1);
  const max = !!useContext(MaxCtx)?.max;
  const [edit, setEdit] = useState(false);
  const [place, setPlace] = useState("");
  const [busy, setBusy] = useState(false);
  const relocate = async (loc: string) => {
    setBusy(true);
    const r = await core.rpc("weather_at", { location: loc });
    setBusy(false);
    if (r.ok) { core.patchCard(card.id, r.result, r.result.location?.name ? `${r.result.location.name}${r.result.location.region ? ", " + r.result.location.region : ""}` : undefined); setEdit(false); setPlace(""); }
    else useStore.getState().toast({ text: r.error || `Couldn't find “${loc}”`, error: true });
  };
  const rainSoon = (w.hourly || []).slice(0, 12).find((h: any) => h.pop >= 50);
  return (
    <div className={`weather ${c.is_day ? "day" : "night"} wx-bg-${c.icon}`}>
      <div className="wx-loc" onClick={(e) => e.stopPropagation()}>
        {edit ? (
          <form className="cs-bar" onSubmit={(e) => { e.preventDefault(); if (place.trim()) relocate(place.trim()); }}>
            <div className="cs-q"><span>⌕</span><input autoFocus value={place} onChange={(e) => setPlace(e.target.value)} placeholder="City, ZIP or place" /></div>
            <button type="submit" className="cs-go" disabled={busy}>{busy ? <span className="cx-spin" /> : "Go"}</button>
            <button type="button" className="cs-go" title="My location" onClick={() => relocate("")}>⌖</button>
            <button type="button" className="cs-x" onClick={() => setEdit(false)}>×</button>
          </form>
        ) : (
          <button className="wx-loc-btn" onClick={() => setEdit(true)} title="Change location">📍 {w.location?.name || "Location"}{w.location?.region ? `, ${w.location.region}` : ""} <span>change</span></button>
        )}
      </div>
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
      {max && <Radar lat={w.location?.lat} lon={w.location?.lon} />}
      <div className="wx-foot">
        {w.location?.source === "ip" ? "Approximate location · " : ""}Open-Meteo · updated {clock(w.current?.time)}
      </div>
    </div>
  );
}

/** Live animated radar (Windy embed: precipitation radar, auto-playing loop) centred on the forecast location. */
export function radarUrl(lat: number, lon: number, zoom = 8): string {
  const q = new URLSearchParams({
    lat: String(lat), lon: String(lon), detailLat: String(lat), detailLon: String(lon), zoom: String(zoom), level: "surface",
    overlay: "radar", product: "radar", menu: "", message: "true", marker: "true", calendar: "now", pressure: "",
    type: "map", location: "coordinates", detail: "", metricWind: "mph", metricTemp: "°F", radarRange: "-1",
  });
  return `https://embed.windy.com/embed.html?${q}`;
}

function Radar({ lat, lon }: { lat?: number; lon?: number }) {
  if (lat == null || lon == null) return null;
  return (
    <>
      <div className="wx-sub">LIVE RADAR</div>
      <div className="wx-radar" onClick={(e) => e.stopPropagation()}>
        <iframe key={`${lat},${lon}`} src={radarUrl(lat, lon)} title="Weather radar" loading="lazy" allowFullScreen />
      </div>
    </>
  );
}
