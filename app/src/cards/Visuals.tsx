import { core } from "../ws/core";
import type { Card } from "../types";
import { MarkdownCard } from "./Cards";

const TONE: Record<string, string> = { good: "#3dffb0", warn: "#ffb020", bad: "#ff4d5e" };

export function StatsCard({ card }: { card: Card }) {
  const items: any[] = card.data?.items ?? [];
  return (
    <div className="stats">
      {items.map((s, i) => (
        <div key={i} className="stat" style={{ animationDelay: `${i * 70}ms` }}>
          <div className={`stat-value ${String(s.value ?? "").length > 7 ? "long" : ""}`} style={{ color: TONE[s.tone] }}>{typeof s.value === "number" ? s.value.toLocaleString() : s.value ?? "—"}</div>
          <div className="stat-label">{s.label}</div>
          {s.delta && <div className="stat-delta">{s.delta}</div>}
        </div>
      ))}
    </div>
  );
}

export function TableCard({ card }: { card: Card }) {
  const cols: string[] = card.data?.columns ?? [];
  const rows: any[][] = card.data?.rows ?? [];
  return (
    <div className="table-wrap">
      <table className="htable">
        {cols.length > 0 && <thead><tr>{cols.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>}
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{String(v ?? "")}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

export function ListCard({ card }: { card: Card }) {
  const items: any[] = card.data?.items ?? [];
  return (
    <div className="list">
      {items.map((it, i) => (
        <div key={i} className={`row ${it.url ? "clickable" : ""}`} style={{ animationDelay: `${i * 40}ms` }}
          onClick={() => it.url && core.openExternal(it.url)}>
          <div className="row-main">
            <div className="row-top"><span className="subject">{it.title}</span>{it.meta && <span className="when">{it.meta}</span>}</div>
            {it.subtitle && <div className="snippet">{it.subtitle}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}

export function ImageCard({ card }: { card: Card }) {
  return (
    <figure className="img-card">
      <img src={card.data?.url} alt={card.data?.caption || ""} onClick={() => core.openExternal(card.data?.url)} />
      {card.data?.caption && <figcaption>{card.data.caption}</figcaption>}
    </figure>
  );
}

export function LinkCard({ card }: { card: Card }) {
  const d = card.data;
  let host = "";
  try { host = new URL(d.url).hostname.replace(/^www\./, ""); } catch { /* */ }
  return (
    <div className="link-card" onClick={() => core.openExternal(d.url)}>
      <div className="link-host">{host}</div>
      <div className="subject">{d.title || d.url}</div>
      {d.description && <div className="snippet">{d.description}</div>}
    </div>
  );
}

/** Pure-SVG charts: bar / line / area / pie / donut. Series: [{name, values[]}], labels[] */
export function ChartCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  const kind: string = d.chart || d.kind || "bar";
  const labels: string[] = d.labels ?? [];
  const rawSeries: any[] = d.series ?? (d.values || d.data ? [{ name: d.title, values: d.values ?? d.data }] : []);
  const series: { name?: string; values: number[] }[] = rawSeries
    .map((s) => ({ name: s.name, values: ((s.values ?? s.data ?? []) as any[]).map((v) => Number(v) || 0) }))
    .filter((s) => s.values.length);
  const colors = ["#39d0ff", "#ffb020", "#3dffb0", "#b388ff", "#ff6b8a", "#7af7ff"];
  if (!series.length) return <div className="muted">No data.</div>;

  if (kind === "pie" || kind === "donut") {
    const vals = series[0].values.map((v) => Math.max(0, Number(v) || 0));
    const total = vals.reduce((a, b) => a + b, 0) || 1;
    let acc = 0;
    const R = 70, r = kind === "donut" ? 42 : 0;
    const arc = (a0: number, a1: number) => {
      const p = (a: number, rad: number) => [100 + rad * Math.cos(a - Math.PI / 2), 90 + rad * Math.sin(a - Math.PI / 2)];
      const large = a1 - a0 > Math.PI ? 1 : 0;
      const [x0, y0] = p(a0, R), [x1, y1] = p(a1, R), [x2, y2] = p(a1, r), [x3, y3] = p(a0, r);
      return `M${x0},${y0} A${R},${R} 0 ${large} 1 ${x1},${y1} L${x2},${y2} ${r ? `A${r},${r} 0 ${large} 0 ${x3},${y3}` : ""} Z`;
    };
    return (
      <div className="chart pie-wrap">
        <svg viewBox="0 0 200 180" className="chart-svg pie">
          {vals.map((v, i) => {
            const a0 = (acc / total) * Math.PI * 2;
            acc += v;
            const a1 = (acc / total) * Math.PI * 2 - 0.0001;
            return <path key={i} d={arc(a0, a1)} fill={colors[i % colors.length]} fillOpacity={0.75} stroke="#01040a" strokeWidth={1.5} className="pie-slice" style={{ animationDelay: `${i * 80}ms` }} />;
          })}
          {kind === "donut" && <text x="100" y="96" textAnchor="middle" className="pie-total">{total.toLocaleString()}</text>}
        </svg>
        <div className="legend">
          {vals.map((v, i) => <div key={i}><span className="sw" style={{ background: colors[i % colors.length] }} />{labels[i] ?? `#${i + 1}`} <b>{v.toLocaleString()}</b> <span className="muted">{Math.round((v / total) * 100)}%</span></div>)}
        </div>
      </div>
    );
  }

  const W = 420, H = 190, P = { l: 38, r: 10, t: 12, b: 30 };
  const all = series.flatMap((s) => s.values.map(Number));
  const max = Math.max(...all, 0), min = Math.min(...all, 0);
  const span = max - min || 1;
  const n = Math.max(...series.map((s) => s.values.length));
  const x = (i: number) => P.l + ((W - P.l - P.r) * (kind === "bar" ? i + 0.5 : i)) / Math.max(1, kind === "bar" ? n : n - 1);
  const y = (v: number) => P.t + (H - P.t - P.b) * (1 - (v - min) / span);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + span * f);
  const bw = ((W - P.l - P.r) / n) * 0.7 / series.length;
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} className="chart-svg">
        <defs>
          {series.map((_, si) => (
            <linearGradient key={si} id={`g${card.id}${si}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colors[si % colors.length]} stopOpacity={0.55} />
              <stop offset="100%" stopColor={colors[si % colors.length]} stopOpacity={0.02} />
            </linearGradient>
          ))}
        </defs>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={P.l - 6} y={y(t) + 3} textAnchor="end" className="axis">{Math.abs(t) >= 1000 ? `${(t / 1000).toFixed(1)}k` : Math.round(t * 10) / 10}</text>
          </g>
        ))}
        {labels.map((l, i) => (n <= 14 || i % Math.ceil(n / 12) === 0) && <text key={i} x={x(i)} y={H - 10} textAnchor="middle" className="axis">{String(l).slice(0, 8)}</text>)}
        {series.map((s, si) => {
          const c = colors[si % colors.length];
          if (kind === "bar") {
            return s.values.map((v, i) => (
              <rect key={`${si}-${i}`} className="bar" style={{ animationDelay: `${i * 35}ms` }}
                x={x(i) - (bw * series.length) / 2 + si * bw} width={bw - 2} y={y(Math.max(0, v))} height={Math.abs(y(v) - y(0))}
                fill={c} fillOpacity={0.7} />
            ));
          }
          const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
          return (
            <g key={si}>
              {kind === "area" && <polygon points={`${x(0)},${y(Math.max(min, 0))} ${pts} ${x(s.values.length - 1)},${y(Math.max(min, 0))}`} fill={`url(#g${card.id}${si})`} />}
              <polyline points={pts} fill="none" stroke={c} strokeWidth={2} className="line" />
              {s.values.map((v, i) => <circle key={i} cx={x(i)} cy={y(v)} r={2.5} fill={c} />)}
            </g>
          );
        })}
      </svg>
      {series.length > 1 && <div className="legend row-legend">{series.map((s, i) => <div key={i}><span className="sw" style={{ background: colors[i % colors.length] }} />{s.name}</div>)}</div>}
    </div>
  );
}

export function VisualMarkdown({ card }: { card: Card }) {
  return <MarkdownCard text={card.data?.markdown ?? card.data?.text ?? ""} />;
}
