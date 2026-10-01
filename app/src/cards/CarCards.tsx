/** Used-car listings near Stephen: summary tiles, a price vs. mileage scatter (hover links to a row) and photo rows. */
import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import type { Card } from "../types";

const money = (v?: number | null) => (v == null ? "—" : `$${v.toLocaleString()}`);
const kmi = (v?: number | null) => (v == null ? "—" : v >= 1000 ? `${Math.round(v / 1000)}k mi` : `${v} mi`);
const open = (u?: string) => u && window.jarvis?.open(u);

export function CarListingsCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  const cars: any[] = d.cars ?? [];
  const [hover, setHover] = useState<number | null>(null);
  const plot = useMemo(() => cars.filter((c) => c.price && c.miles), [cars]);
  if (!cars.length) return <div className="muted">No listings found near {d.near}.</div>;
  const W = 520, H = 170, P = { l: 46, r: 12, t: 12, b: 26 };
  const maxM = Math.max(...plot.map((c) => c.miles), 1), maxP = Math.max(...plot.map((c) => c.price), 1);
  const minP = Math.min(...plot.map((c) => c.price), maxP);
  const lo = Math.floor(minP * 0.85 / 1000) * 1000, hi = Math.ceil(maxP * 1.05 / 1000) * 1000;
  const x = (m: number) => P.l + ((W - P.l - P.r) * m) / (maxM * 1.05);
  const y = (p: number) => H - P.b - ((H - P.t - P.b) * (p - lo)) / Math.max(1, hi - lo);
  const yTicks = [lo, (lo + hi) / 2, hi];
  return (
    <div className="car">
      <div className="car-tiles">
        <div><b>{d.count}</b><span>for sale nearby</span></div>
        <div><b>{money(d.price_median)}</b><span>median price</span></div>
        <div><b>{money(d.price_min)}</b><span>lowest</span></div>
        <div><b>{cars[0]?.distance_mi != null ? `${cars[0].distance_mi} mi` : "—"}</b><span>nearest</span></div>
      </div>
      {plot.length > 2 && (
        <div className="car-plot">
          <div className="car-plot-h">PRICE VS MILEAGE <span>· nearer = brighter</span></div>
          <svg viewBox={`0 0 ${W} ${H}`} className="car-svg">
            {yTicks.map((t) => (
              <g key={t}>
                <line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} className="car-grid" />
                <text x={P.l - 6} y={y(t) + 3} textAnchor="end" className="car-ax">${Math.round(t / 1000)}k</text>
              </g>
            ))}
            {[0, 0.5, 1].map((f) => <text key={f} x={x(maxM * f)} y={H - 8} textAnchor="middle" className="car-ax">{kmi(maxM * f)}</text>)}
            {plot.map((c, i) => {
              const idx = cars.indexOf(c);
              const near = c.distance_mi != null ? Math.max(0.25, 1 - c.distance_mi / 80) : 0.4;
              return (
                <motion.circle key={c.vin || i} cx={x(c.miles)} cy={y(c.price)} r={hover === idx ? 8 : 5.5}
                  initial={{ opacity: 0, r: 0 }} animate={{ opacity: hover == null || hover === idx ? near : 0.15, r: hover === idx ? 8 : 5.5 }}
                  transition={{ delay: 0.03 * i }} className={`car-dot ${c.deal ? "deal" : ""}`}
                  onMouseEnter={() => setHover(idx)} onMouseLeave={() => setHover(null)} onClick={(e) => { e.stopPropagation(); open(c.url); }} />
              );
            })}
          </svg>
        </div>
      )}
      <div className="car-list">
        {cars.slice(0, 10).map((c, i) => (
          <motion.button key={c.vin || i} className={`car-row ${hover === i ? "on" : ""}`} initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.04 * i }} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
            onClick={(e) => { e.stopPropagation(); open(c.url); }} title="Open the listing">
            <div className="car-ph">{c.photo ? <img src={c.photo} alt="" loading="lazy" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} /> : null}<span>🚘</span></div>
            <div className="car-main">
              <div className="car-t">{c.year} {String(c.title).replace(/^\d{4}\s+Mercedes-Benz\s+(CLS-Class\s+)?/, "")}</div>
              <div className="car-m">{kmi(c.miles)}{c.color ? ` · ${c.color}` : ""}</div>
              <div className="car-l">{c.location}{c.distance_mi != null ? ` · ${c.distance_mi} mi away` : ""}</div>
            </div>
            <div className="car-p">
              <b>{money(c.price)}</b>
              {c.deal && <em>{c.deal}</em>}
            </div>
          </motion.button>
        ))}
      </div>
      <div className="car-foot muted small">Listings via {d.source} · tap one to open it</div>
    </div>
  );
}
