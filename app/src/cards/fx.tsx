import { useEffect, useRef, useState } from "react";
import {
  AreaSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, TickMarkType, createChart,
  type IChartApi, type ISeriesApi, type Time, type UTCTimestamp,
} from "lightweight-charts";

/* Shared finance primitives: TradingView lightweight-charts wrappers styled for the HUD, logos with fallbacks,
   number formatting, sparklines. Times arrive as exchange-local wall-clock seconds, so the chart shows them as-is. */

export const UP = "#3dffb0";
export const DOWN = "#ff4d5e";
export const CY = "#39d0ff";
export const SERIES_COLORS = ["#39d0ff", "#ffb020", "#c77dff", "#3dffb0", "#ff6b9d", "#f5f06b"];

export const tone = (v?: number | null) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "flat");
export const sign = (v?: number | null) => (v == null ? "" : v > 0 ? "+" : v < 0 ? "−" : "");

export function fmtPrice(v?: number | null, cur = "USD"): string {
  if (v == null) return "—";
  const a = Math.abs(v);
  const d = a >= 1000 ? 2 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6;
  const s = a.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  const sym = cur === "USD" ? "$" : cur === "EUR" ? "€" : cur === "GBP" ? "£" : cur === "JPY" ? "¥" : "";
  return `${v < 0 ? "−" : ""}${sym}${s}${sym ? "" : ` ${cur}`}`;
}
export const fmtEps = (v?: number | null) => (v == null ? "—" : `${v < 0 ? "−" : ""}$${Math.abs(v).toFixed(2)}`);
export const fmtPct = (v?: number | null, d = 2) => (v == null ? "—" : `${sign(v)}${Math.abs(v).toFixed(d)}%`);
export const fmtChg = (v?: number | null, price?: number | null) => {
  if (v == null) return "—";
  const d = price != null && Math.abs(price) < 1 ? 4 : 2; // sub-dollar assets need more precision
  return `${sign(v)}${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
};
export function fmtBig(v?: number | null, money = true): string {
  if (v == null) return "—";
  const a = Math.abs(v);
  const [n, u] = a >= 1e12 ? [a / 1e12, "T"] : a >= 1e9 ? [a / 1e9, "B"] : a >= 1e6 ? [a / 1e6, "M"] : a >= 1e3 ? [a / 1e3, "K"] : [a, ""];
  return `${v < 0 ? "−" : ""}${money ? "$" : ""}${n.toFixed(n >= 100 || !u ? 0 : n >= 10 ? 1 : 2)}${u}`;
}
export const fmtNum = (v?: number | null, d = 2) => (v == null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: d }));

/* ------------------------------------------------------------------ logo with fallbacks + monogram */
export function Logo({ urls, label, size = 40, round = false }: { urls?: (string | undefined)[]; label: string; size?: number; round?: boolean }) {
  const list = (urls || []).filter(Boolean) as string[];
  const [i, setI] = useState(0);
  useEffect(() => setI(0), [list.join("|")]);
  const letters = label.replace(/^\^/, "").replace(/[^A-Za-z0-9]/g, "").slice(0, label.startsWith("^") ? 3 : 2).toUpperCase();
  const style = { width: size, height: size, borderRadius: round ? "50%" : Math.max(6, size / 5) };
  if (i >= list.length)
    return <span className="fx-logo fx-mono" style={{ ...style, fontSize: size * (letters.length > 2 ? 0.3 : 0.38) }}>{letters}</span>;
  return (
    <span className="fx-logo" style={style}>
      <img src={list[i]} alt="" draggable={false} referrerPolicy="no-referrer"
        onError={() => setI((n) => n + 1)}
        onLoad={(e) => { const im = e.currentTarget; if (im.naturalWidth < 20) setI((n) => n + 1); }} />
    </span>
  );
}

/* ------------------------------------------------------------------ sparkline (SVG) */
export function Spark({ values, w = 90, h = 28, base, color }: { values: number[]; w?: number; h?: number; base?: number | null; color?: string }) {
  if (!values || values.length < 2) return <svg width={w} height={h} />;
  const lo = Math.min(...values, base ?? Infinity), hi = Math.max(...values, base ?? -Infinity);
  const span = hi - lo || 1;
  const x = (i: number) => (i / (values.length - 1)) * w;
  const y = (v: number) => h - 2 - ((v - lo) / span) * (h - 4);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const ref = base ?? values[0];
  const c = color || (values[values.length - 1] >= ref ? UP : DOWN);
  const id = `sp${Math.round(Math.random() * 1e9)}`;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="fx-spark">
      <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={c} stopOpacity="0.35" /><stop offset="1" stopColor={c} stopOpacity="0" /></linearGradient></defs>
      {base != null && <line x1="0" x2={w} y1={y(base)} y2={y(base)} stroke="rgba(180,225,240,0.25)" strokeDasharray="2 3" />}
      <path d={`${d}L${w},${h}L0,${h}Z`} fill={`url(#${id})`} />
      <path d={d} fill="none" stroke={c} strokeWidth="1.5" />
    </svg>
  );
}

/* ------------------------------------------------------------------ interactive price chart */
export type Pt = { t: number; c: number; o?: number | null; h?: number | null; l?: number | null; v?: number };

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const axisPrice = (v: number) => {
  const a = Math.abs(v);
  return v.toLocaleString("en-US", { minimumFractionDigits: a >= 1000 ? 0 : a >= 1 ? 2 : 4, maximumFractionDigits: a >= 1000 ? 0 : a >= 1 ? 2 : 6 });
};
// times are exchange-local wall clock stored as if UTC, so always read them back in UTC
const tick = (t: Time, type: TickMarkType) => {
  const d = new Date((t as number) * 1000);
  if (type === TickMarkType.Year) return String(d.getUTCFullYear());
  if (type === TickMarkType.Month) return MON[d.getUTCMonth()];
  if (type === TickMarkType.DayOfMonth) return `${MON[d.getUTCMonth()]} ${d.getUTCDate()}`;
  const h = d.getUTCHours(), m = d.getUTCMinutes();
  return `${h % 12 || 12}:${String(m).padStart(2, "0")}`;
};

const baseOpts = (intraday: boolean) => ({
  localization: { priceFormatter: axisPrice, timeFormatter: (t: Time) => fmtWhen(t as number, intraday) },
  layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "rgba(180,225,240,0.6)", fontFamily: "JetBrains Mono, monospace", fontSize: 10, attributionLogo: false },
  grid: { vertLines: { color: "rgba(57,208,255,0.05)" }, horzLines: { color: "rgba(57,208,255,0.07)" } },
  rightPriceScale: { borderColor: "rgba(57,208,255,0.18)", scaleMargins: { top: 0.12, bottom: 0.22 } },
  timeScale: { borderColor: "rgba(57,208,255,0.18)", timeVisible: intraday, secondsVisible: false, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true, tickMarkFormatter: tick },
  crosshair: {
    mode: CrosshairMode.Magnet,
    vertLine: { color: "rgba(159,240,255,0.45)", width: 1 as const, style: LineStyle.Dashed, labelBackgroundColor: "#0b6fa8" },
    horzLine: { color: "rgba(159,240,255,0.35)", width: 1 as const, style: LineStyle.Dashed, labelBackgroundColor: "#0b6fa8" },
  },
  handleScroll: false, handleScale: false,
});

export function PriceChart({ points, base, intraday, height = 230, onHover }: {
  points: Pt[]; base?: number | null; intraday?: boolean; height?: number;
  onHover?: (p: { t: number; c: number; pct: number | null } | null) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const hoverRef = useRef(onHover);
  hoverRef.current = onHover;
  useEffect(() => {
    if (!el.current || !points?.length) return;
    const c = createChart(el.current, { ...baseOpts(!!intraday), width: el.current.clientWidth, height });
    chart.current = c;
    const ref = base ?? points[0].c;
    const up = points[points.length - 1].c >= ref;
    const col = up ? UP : DOWN;
    const area = c.addSeries(AreaSeries, {
      lineColor: col, lineWidth: 2, topColor: up ? "rgba(61,255,176,0.32)" : "rgba(255,77,94,0.30)",
      bottomColor: "rgba(1,4,10,0)", priceLineVisible: false, lastValueVisible: true,
      crosshairMarkerRadius: 4, crosshairMarkerBorderColor: "#fff", crosshairMarkerBackgroundColor: col,
    });
    area.setData(points.map((p) => ({ time: p.t as UTCTimestamp, value: p.c })));
    area.createPriceLine({ price: ref, color: "rgba(180,225,240,0.35)", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
    if (points.some((p) => p.v)) {
      const vol = c.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
      c.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
      vol.setData(points.map((p, i) => ({
        time: p.t as UTCTimestamp, value: p.v || 0,
        color: (i ? p.c >= points[i - 1].c : true) ? "rgba(61,255,176,0.28)" : "rgba(255,77,94,0.28)",
      })));
    }
    c.timeScale().fitContent();
    c.subscribeCrosshairMove((param) => {
      const cb = hoverRef.current;
      if (!cb) return;
      const d = param.time != null ? (param.seriesData.get(area as ISeriesApi<"Area">) as any) : null;
      cb(d ? { t: param.time as number, c: d.value, pct: ref ? (d.value / ref - 1) * 100 : null } : null);
    });
    const ro = new ResizeObserver(() => el.current && c.applyOptions({ width: el.current.clientWidth }));
    ro.observe(el.current);
    return () => { ro.disconnect(); c.remove(); chart.current = null; };
  }, [points, base, intraday, height]);
  return <div className="fx-chart" ref={el} style={{ height }} onMouseLeave={() => hoverRef.current?.(null)} />;
}

export function CompareChart({ series, intraday, height = 250, onHover }: {
  series: { symbol: string; points: { t: number; v: number }[] }[]; intraday?: boolean; height?: number;
  onHover?: (vals: Record<string, number> | null, t?: number) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const hoverRef = useRef(onHover);
  hoverRef.current = onHover;
  useEffect(() => {
    if (!el.current || !series?.length) return;
    const c = createChart(el.current, { ...baseOpts(!!intraday), width: el.current.clientWidth, height });
    c.applyOptions({ rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.08 } }, localization: { priceFormatter: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`, timeFormatter: (t: Time) => fmtWhen(t as number, !!intraday) } });
    const lines = series.map((s, i) => {
      const ln = c.addSeries(LineSeries, { color: SERIES_COLORS[i % SERIES_COLORS.length], lineWidth: 2, priceLineVisible: false, lastValueVisible: true, crosshairMarkerRadius: 3 });
      ln.setData(s.points.map((p) => ({ time: p.t as UTCTimestamp, value: p.v })));
      return ln;
    });
    lines[0]?.createPriceLine({ price: 0, color: "rgba(180,225,240,0.35)", lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
    c.timeScale().fitContent();
    c.subscribeCrosshairMove((param) => {
      const cb = hoverRef.current;
      if (!cb) return;
      if (param.time == null) return cb(null);
      const vals: Record<string, number> = {};
      lines.forEach((ln, i) => { const d = param.seriesData.get(ln) as any; if (d) vals[series[i].symbol] = d.value; });
      cb(vals, param.time as number);
    });
    const ro = new ResizeObserver(() => el.current && c.applyOptions({ width: el.current.clientWidth }));
    ro.observe(el.current);
    return () => { ro.disconnect(); c.remove(); };
  }, [series, intraday, height]);
  return <div className="fx-chart" ref={el} style={{ height }} onMouseLeave={() => hoverRef.current?.(null)} />;
}

/* wall-clock seconds -> label */
export function fmtWhen(t: number, intraday: boolean): string {
  const d = new Date(t * 1000);
  const opts: Intl.DateTimeFormatOptions = intraday
    ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "UTC" }
    : { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" };
  return d.toLocaleString("en-US", opts);
}

export function ago(ts?: number | null): string {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/* horizontal range bar: low —●— high */
export function RangeBar({ lo, hi, v, left, right }: { lo?: number | null; hi?: number | null; v?: number | null; left?: string; right?: string }) {
  if (lo == null || hi == null || v == null || hi <= lo) return null;
  const p = Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  return (
    <div className="fx-range">
      <span>{left ?? fmtNum(lo)}</span>
      <div className="fx-range-track"><div className="fx-range-fill" style={{ width: `${p}%` }} /><i style={{ left: `${p}%` }} /></div>
      <span>{right ?? fmtNum(hi)}</span>
    </div>
  );
}
