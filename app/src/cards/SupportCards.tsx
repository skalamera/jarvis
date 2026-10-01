/** Hadrius Support Model: an interactive blueprint of the 3-tier support operation (architecture diagram, swimlane
 *  workflows, tier cards, routing matrix, lifecycle state machine, budgets, payloads, guardrails, FAQ). Data comes
 *  from the precomputed support_blueprint.json; JARVIS's narrated tour drives the tabs via `jarvis:support_tab`. */
import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useStore } from "../state/store";
import type { Card } from "../types";

type Tab = "overview" | "architecture" | "layers" | "tiers" | "flows" | "routing" | "lifecycle" | "budgets" | "payloads" | "guardrails" | "faq" | "glossary";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" }, { id: "architecture", label: "Blueprint" }, { id: "layers", label: "Layers" },
  { id: "tiers", label: "Tiers" }, { id: "flows", label: "Workflows" }, { id: "routing", label: "Routing" },
  { id: "lifecycle", label: "Lifecycle" }, { id: "budgets", label: "Budgets" }, { id: "payloads", label: "Payloads" },
  { id: "guardrails", label: "Guardrails" }, { id: "faq", label: "FAQ" }, { id: "glossary", label: "Glossary" },
];
const PALETTE: Record<string, string> = {
  cyan: "#39d0ff", violet: "#b388ff", amber: "#ffb020", green: "#3dffb0", rose: "#ff6b8a", blue: "#5b8cff",
  teal: "#2ee6c9", gold: "#f5c451", red: "#ff4d5e", pink: "#ff6bd5", orange: "#ff8a3d",
};
const AUTO = ["#39d0ff", "#3dffb0", "#b388ff", "#ffb020", "#ff6b8a", "#5b8cff", "#2ee6c9"];
const col = (c?: string, i = 0) => (c && (PALETTE[c] || (c.startsWith("#") ? c : ""))) || AUTO[i % AUTO.length];
const arr = <T,>(x: T[] | undefined | null): T[] => (Array.isArray(x) ? x : []);
const stop = (e: React.SyntheticEvent) => e.stopPropagation();

export function SupportBlueprintCard({ card, expanded = false }: { card: Card; expanded?: boolean }) {
  const d = card.data ?? {};
  const avail = useMemo(() => TABS.filter((t) => t.id === "overview" || hasData(d, t.id)), [d]);
  const [tab, setTab] = useState<Tab>((d.tab as Tab) || "overview");
  const [tier, setTier] = useState<string | null>(null);
  const [flow, setFlow] = useState<string | null>(null);
  const tour = useStore((s) => s.showcase);
  useEffect(() => { if (d.tab) setTab(d.tab); }, [d.tab]);
  useEffect(() => {
    const h = (e: Event) => {
      const det = (e as CustomEvent).detail || {};
      if (det.tab && TABS.some((t) => t.id === det.tab)) setTab(det.tab);
      setTier(det.tier ? String(det.tier) : null);
      setFlow(det.flow ? String(det.flow) : null);
    };
    window.addEventListener("jarvis:support_tab", h);
    return () => window.removeEventListener("jarvis:support_tab", h);
  }, []);
  const touring = tour?.active && tour.mode === "tour";
  return (
    <div className={`sb ${expanded ? "sb-xl" : ""}`} onClick={stop}>
      <div className="sb-head">
        <div className="sb-titles">
          <div className="sb-title">{d.title || "Hadrius Support Model"}</div>
          {d.subtitle && <div className="sb-sub">{d.subtitle}</div>}
        </div>
        {touring && <div className="sb-live"><i />NARRATING</div>}
        {!expanded && <button className="sb-open" title="Open full size" onClick={() => useStore.getState().set({ expandedCardId: card.id })}>⤢</button>}
      </div>
      <nav className="sb-tabs">
        {avail.map((t) => (
          <button key={t.id} className={`sb-tab ${tab === t.id ? "on" : ""}`} onClick={() => { setTab(t.id); setTier(null); }}>
            {t.label}{tab === t.id && <motion.span layoutId={`sb-ul-${card.id}`} className="sb-ul" />}
          </button>
        ))}
      </nav>
      <AnimatePresence mode="wait">
        <motion.div key={tab} className="sb-body" initial={{ opacity: 0, y: 10, filter: "blur(6px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={{ opacity: 0, y: -8, filter: "blur(4px)" }} transition={{ duration: 0.28 }}>
          {tab === "overview" && <Overview d={d} expanded={expanded} go={setTab} />}
          {tab === "architecture" && <Architecture a={d.architecture} expanded={expanded} />}
          {tab === "layers" && <Layers layers={arr(d.layers)} />}
          {tab === "tiers" && <Tiers tiers={arr(d.tiers)} focus={tier} expanded={expanded} />}
          {tab === "flows" && <Flows flows={arr(d.flows)} expanded={expanded} focus={flow} />}
          {tab === "routing" && <Routing rows={arr(d.routing)} expanded={expanded} />}
          {tab === "lifecycle" && <Lifecycle l={d.lifecycle} expanded={expanded} />}
          {tab === "budgets" && <Budgets b={arr(d.budgets)} />}
          {tab === "payloads" && <Payloads p={arr(d.payloads)} />}
          {tab === "guardrails" && <Guardrails g={arr(d.guardrails)} />}
          {tab === "faq" && <Faq f={arr(d.faq)} />}
          {tab === "glossary" && <Glossary g={arr(d.glossary)} />}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

function hasData(d: any, t: Tab): boolean {
  const v = d?.[t];
  if (t === "architecture") return !!v?.nodes?.length;
  if (t === "lifecycle") return !!v?.states?.length;
  return Array.isArray(v) ? v.length > 0 : !!v;
}

// ------------------------------------------------------------------ overview
function Overview({ d, expanded, go }: { d: any; expanded: boolean; go: (t: Tab) => void }) {
  const tiers = arr<any>(d.tiers);
  return (
    <div className="sb-ov">
      {d.summary && <p className="sb-summary">{d.summary}</p>}
      <div className="sb-stats">
        {arr<any>(d.stats).slice(0, 8).map((s, i) => (
          <motion.div key={i} className="sb-stat" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 * i }}>
            <b style={{ color: AUTO[i % AUTO.length] }}>{s.value}</b><span>{s.label}</span>{s.sub && <em>{s.sub}</em>}
          </motion.div>
        ))}
      </div>
      {tiers.length > 0 && (
        <div className="sb-pipe">
          {tiers.map((t, i) => (
            <div key={t.id || i} className="sb-pipe-seg">
              <button className="sb-pipe-node" style={{ ["--c" as any]: col(t.color, i) }} onClick={() => go("tiers")}>
                <span className="sb-pipe-tier">{t.tier === "0" || /intake/i.test(t.name || "") ? "INTAKE" : t.tier ? `TIER ${t.tier}` : t.name}</span>
                <span className="sb-pipe-agent">{t.agent || t.name}</span>
                <span className="sb-pipe-plat">{t.platform}</span>
              </button>
              {i < tiers.length - 1 && <div className="sb-pipe-wire"><i /><i /><i /></div>}
            </div>
          ))}
        </div>
      )}
      {expanded && d.narrative && (
        <div className="sb-narr">{String(d.narrative).split(/\n\n+/).map((p: string, i: number) => <p key={i}>{p}</p>)}</div>
      )}
      {arr<any>(d.sources).length > 0 && (
        <div className="sb-sources"><span>BUILT FROM</span>{arr<any>(d.sources).map((s, i) => <em key={i} title={s.kind}>{s.name}</em>)}</div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ architecture blueprint
function Architecture({ a, expanded }: { a: any; expanded: boolean }) {
  const groups = arr<any>(a?.groups), nodes = arr<any>(a?.nodes), edges = arr<any>(a?.edges);
  const [hover, setHover] = useState<string | null>(null);
  const L = useMemo(() => {
    const gi = new Map(groups.map((g, i) => [g.id, i]));
    const cols = Math.max(1, groups.length);
    const byG: Record<string, any[]> = {};
    for (const n of nodes) (byG[n.group] ||= []).push(n);
    const NW = 168, NH = expanded ? 58 : 52, GX = 64, PADX = 26, PADY = 62, GAPY = expanded ? 40 : 20;
    const maxRows = Math.max(1, ...Object.values(byG).map((x) => x.length));
    const W = PADX * 2 + cols * NW + (cols - 1) * GX;
    const H = PADY + maxRows * (NH + GAPY) + 24;
    const pos = new Map<string, { x: number; y: number; c: string; g: number }>();
    groups.forEach((g, i) => {
      const list = byG[g.id] || [];
      const x = PADX + i * (NW + GX);
      const off = ((maxRows - list.length) * (NH + GAPY)) / 2;
      list.forEach((n, j) => pos.set(n.id, { x, y: PADY + off + j * (NH + GAPY), c: col(g.color, i), g: i }));
    });
    for (const n of nodes) if (!pos.has(n.id)) pos.set(n.id, { x: PADX, y: PADY, c: AUTO[0], g: gi.get(n.group) ?? 0 });
    return { W, H, NW, NH, pos, PADX, GX };
  }, [a, expanded]);
  const lit = (e: any) => !hover || e.from === hover || e.to === hover;
  return (
    <div className="sb-arch">
      <div className="sb-scroll">
        <svg viewBox={`0 0 ${L.W} ${L.H}`} className="sb-svg" style={expanded ? { height: "min(64vh, 640px)" } : { minWidth: Math.min(L.W, 900) }}>
          <defs>
            <marker id="sb-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#9ff0ff" /></marker>
            <filter id="sb-glow"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
            <pattern id="sb-grid" width="24" height="24" patternUnits="userSpaceOnUse"><path d="M24 0H0V24" fill="none" stroke="rgba(57,208,255,0.06)" /></pattern>
          </defs>
          <rect x="0" y="0" width={L.W} height={L.H} fill="url(#sb-grid)" />
          {groups.map((g, i) => {
            const x = L.PADX + i * (L.NW + L.GX) - 10;
            const c = col(g.color, i);
            return (
              <g key={g.id}>
                <rect x={x} y={14} width={L.NW + 20} height={L.H - 24} rx={14} fill={c} fillOpacity={0.035} stroke={c} strokeOpacity={0.22} strokeDasharray="4 5" />
                <text x={x + (L.NW + 20) / 2} y={36} textAnchor="middle" className="sb-glabel" fill={c}>{String(g.label).toUpperCase()}</text>
              </g>
            );
          })}
          {edges.map((e, i) => {
            const s = L.pos.get(e.from), t = L.pos.get(e.to);
            if (!s || !t) return null;
            let d: string, lx: number, ly: number;
            if (s.g === t.g) {
              const x = s.x + L.NW, y1 = s.y + L.NH / 2, y2 = t.y + L.NH / 2, bend = 34 + Math.abs(y2 - y1) * 0.15;
              d = `M${x},${y1} C${x + bend},${y1} ${x + bend},${y2} ${x + 2},${y2}`;
              lx = x + bend * 0.8; ly = (y1 + y2) / 2;
            } else {
              const fwd = t.x > s.x;
              const x1 = fwd ? s.x + L.NW : s.x, x2 = fwd ? t.x : t.x + L.NW;
              const y1 = s.y + L.NH / 2, y2 = t.y + L.NH / 2, dx = Math.max(40, Math.abs(x2 - x1) * 0.45);
              d = `M${x1},${y1} C${x1 + (fwd ? dx : -dx)},${y1} ${x2 - (fwd ? dx : -dx)},${y2} ${x2},${y2}`;
              lx = (x1 + x2) / 2; ly = (y1 + y2) / 2 - 4;
            }
            const on = lit(e);
            return (
              <g key={i} className={`sb-edge k-${e.kind || "api"} ${on ? "" : "dim"}`}>
                <path d={d} className="sb-edge-base" markerEnd="url(#sb-arrow)" />
                <path d={d} className="sb-edge-flow" style={{ animationDelay: `${(i % 7) * 0.3}s` }} />
                {e.label && on && hover && <text x={lx} y={ly} className="sb-elabel" textAnchor="middle">{e.label}</text>}
              </g>
            );
          })}
          {nodes.map((n, i) => {
            const p = L.pos.get(n.id)!;
            const on = !hover || hover === n.id || edges.some((e) => (e.from === hover && e.to === n.id) || (e.to === hover && e.from === n.id));
            return (
              <motion.g key={n.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: on ? 1 : 0.28, y: 0 }} transition={{ delay: 0.025 * i }}
                onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} className="sb-node">
                <rect x={p.x} y={p.y} width={L.NW} height={L.NH} rx={10} fill="rgba(6,24,40,0.92)" stroke={p.c} strokeOpacity={hover === n.id ? 0.95 : 0.5} filter={hover === n.id ? "url(#sb-glow)" : undefined} />
                <rect x={p.x} y={p.y} width={4} height={L.NH} rx={2} fill={p.c} />
                <text x={p.x + 14} y={p.y + 21} className="sb-nlabel">{clip(n.label, 22)}</text>
                {n.sub && <text x={p.x + 14} y={p.y + 38} className="sb-nsub">{clip(n.sub, 28)}</text>}
                <title>{`${n.label}${n.sub ? `: ${n.sub}` : ""}`}</title>
              </motion.g>
            );
          })}
        </svg>
      </div>
      <div className="sb-legend">
        {[["event", "Webhook / trigger"], ["api", "API call"], ["data", "Data / persistence"], ["human", "Human action"]].map(([k, l]) => (
          <span key={k} className={`sb-lg k-${k}`}><i />{l}</span>
        ))}
        <span className="muted small">Hover a component to trace its connections.</span>
      </div>
    </div>
  );
}
const clip = (s: string, n: number) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");

// ------------------------------------------------------------------ layers (three layers, no overlap)
function Layers({ layers }: { layers: any[] }) {
  return (
    <div className="sb-layers">
      {layers.map((l, i) => (
        <motion.div key={l.id || i} className="sb-layer" style={{ ["--c" as any]: col(l.color, i) }} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 * i }}>
          <div className="sb-layer-h"><b>{l.name}</b><span>{l.role}</span></div>
          <div className="sb-layer-cols">
            <div><div className="sb-mini ok">OWNS</div><ul>{arr<string>(l.owns).map((x, j) => <li key={j}>{x}</li>)}</ul></div>
            {arr(l.never).length > 0 && <div><div className="sb-mini no">NEVER</div><ul className="no">{arr<string>(l.never).map((x, j) => <li key={j}>{x}</li>)}</ul></div>}
          </div>
        </motion.div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ tiers
function Tiers({ tiers, focus, expanded }: { tiers: any[]; focus: string | null; expanded: boolean }) {
  const [sel, setSel] = useState(0);
  useEffect(() => {
    if (focus == null) return;
    const i = tiers.findIndex((t) => String(t.tier) === focus);
    if (i >= 0) setSel(i);
  }, [focus, tiers]);
  const t = tiers[sel];
  if (!t) return null;
  const c = col(t.color, sel);
  return (
    <div className="sb-tiers">
      <div className="sb-tier-rail">
        {tiers.map((x, i) => (
          <button key={x.id || i} className={`sb-tier-chip ${i === sel ? "on" : ""}`} style={{ ["--c" as any]: col(x.color, i) }} onClick={() => setSel(i)}>
            <span>{x.tier === "0" ? "INTAKE" : x.tier ? `TIER ${x.tier}` : "HANDOFF"}</span><b>{x.agent || x.name}</b>
          </button>
        ))}
      </div>
      <AnimatePresence mode="wait">
        <motion.div key={sel} className="sb-tier" style={{ ["--c" as any]: c }} initial={{ opacity: 0, x: 18 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -12 }} transition={{ duration: 0.22 }}>
          <div className="sb-tier-h">
            <div className="sb-tier-badge">{t.tier === "0" ? "IN" : t.tier ? `T${t.tier}` : "AM"}</div>
            <div><div className="sb-tier-name">{t.name}{t.agent ? ` · ${t.agent}` : ""}</div><div className="sb-tier-plat">{t.platform}</div></div>
          </div>
          {t.mission && <p className="sb-tier-mission">{t.mission}</p>}
          {arr(t.invoked_by).length > 0 && <div className="sb-trig"><span>INVOKED BY</span>{arr<string>(t.invoked_by).map((x, i) => <em key={i}>{x}</em>)}</div>}
          <div className={`sb-tier-grid ${expanded ? "xl" : ""}`}>
            <div><div className="sb-mini ok">DOES</div><ul>{arr<string>(t.does).map((x, i) => <li key={i}>{x}</li>)}</ul></div>
            {arr(t.never).length > 0 && <div><div className="sb-mini no">NEVER</div><ul className="no">{arr<string>(t.never).map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
            {arr(t.outputs).length > 0 && <div><div className="sb-mini">OUTPUTS</div><ul>{arr<string>(t.outputs).map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
          </div>
          {arr(t.exit_states).length > 0 && (
            <div className="sb-exits">
              {arr<any>(t.exit_states).map((e, i) => <div key={i} className="sb-exit"><code>{e.label}</code><span>{e.when}</span></div>)}
            </div>
          )}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

// ------------------------------------------------------------------ workflows (swimlanes)
function Flows({ flows, expanded, focus }: { flows: any[]; expanded: boolean; focus?: string | null }) {
  const [sel, setSel] = useState(() => Math.max(0, flows.findIndex((f) => f.id === focus)));
  useEffect(() => {
    const i = flows.findIndex((f) => f.id === focus);
    if (i >= 0) setSel(i);
  }, [focus, flows]);
  const f = flows[sel];
  return (
    <div className="sb-flows">
      <div className="sb-flow-pick">
        {flows.map((x, i) => <button key={x.id || i} className={`sb-chip ${i === sel ? "on" : ""}`} onClick={() => setSel(i)}>{x.title}</button>)}
      </div>
      {f && (
        <AnimatePresence mode="wait">
          <motion.div key={sel} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
            {f.summary && <div className="sb-flow-sum">{f.summary}</div>}
            <Swimlane f={f} expanded={expanded} />
          </motion.div>
        </AnimatePresence>
      )}
    </div>
  );
}

function Swimlane({ f, expanded }: { f: any; expanded: boolean }) {
  const lanes = arr<any>(f.lanes), steps = arr<any>(f.steps);
  const [hover, setHover] = useState<string | null>(null);
  const LW = expanded ? 230 : 190, NW = LW - 34, NH = 46, RH = 70, TOP = 48;
  const li = new Map(lanes.map((l, i) => [l.id, i]));
  const pos = new Map<string, { x: number; y: number; r: number }>();
  steps.forEach((s, r) => pos.set(s.id, { x: (li.get(s.lane) ?? 0) * LW + 17, y: TOP + r * RH, r }));
  const W = Math.max(1, lanes.length) * LW, H = TOP + steps.length * RH + 10;
  const detail = steps.find((s) => s.id === hover);
  return (
    <div className="sb-swim-wrap">
      <div className="sb-scroll">
        <svg viewBox={`0 0 ${W} ${H}`} className="sb-svg" style={expanded ? { height: `min(60vh, ${Math.round(H * 1.15)}px)` } : { minWidth: Math.min(W, 760) }}>
          <defs><marker id="sb-arrow2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#9ff0ff" /></marker></defs>
          {lanes.map((l, i) => (
            <g key={l.id}>
              <rect x={i * LW + 3} y={4} width={LW - 6} height={H - 8} rx={12} fill={AUTO[i % AUTO.length]} fillOpacity={0.035} stroke={AUTO[i % AUTO.length]} strokeOpacity={0.18} />
              <text x={i * LW + LW / 2} y={28} textAnchor="middle" className="sb-glabel" fill={AUTO[i % AUTO.length]}>{String(l.label).toUpperCase()}</text>
            </g>
          ))}
          {steps.flatMap((s) => arr<any>(s.next).map((n, k) => {
            const a = pos.get(s.id), b = pos.get(n.to);
            if (!a || !b) return null;
            const ax = a.x + NW / 2, bx = b.x + NW / 2;
            let d: string;
            if (b.r > a.r) {
              const y1 = a.y + NH, y2 = b.y, my = (y1 + y2) / 2;
              d = `M${ax},${y1} C${ax},${my} ${bx},${my} ${bx},${y2}`;
            } else { // loop back up: route around the right side
              const x = Math.max(a.x, b.x) + NW, y1 = a.y + NH / 2, y2 = b.y + NH / 2;
              d = `M${a.x + NW},${y1} C${x + 40},${y1} ${x + 40},${y2} ${b.x + NW},${y2}`;
            }
            const on = !hover || hover === s.id || hover === n.to;
            return (
              <g key={`${s.id}-${k}`} className={`sb-edge k-api ${on ? "" : "dim"}`}>
                <path d={d} className="sb-edge-base" markerEnd="url(#sb-arrow2)" />
                <path d={d} className="sb-edge-flow" />
                {n.label && <text x={(ax + bx) / 2 + (ax === bx ? 8 : 0)} y={b.r > a.r ? (a.y + NH + b.y) / 2 + 3 : (a.y + b.y) / 2 + NH / 2} className="sb-elabel" textAnchor={ax === bx ? "start" : "middle"}>{n.label}</text>}
              </g>
            );
          }))}
          {steps.map((s, i) => {
            const p = pos.get(s.id)!;
            const c = AUTO[(li.get(s.lane) ?? 0) % AUTO.length];
            const kind = s.kind || "step";
            return (
              <motion.g key={s.id} className="sb-node" initial={{ opacity: 0, scale: 0.94 }} animate={{ opacity: !hover || hover === s.id ? 1 : 0.45, scale: 1 }}
                transition={{ delay: 0.04 * i }} onMouseEnter={() => setHover(s.id)} onMouseLeave={() => setHover(null)} style={{ transformOrigin: `${p.x + NW / 2}px ${p.y + NH / 2}px` }}>
                {kind === "decision" ? (
                  <polygon points={`${p.x + NW / 2},${p.y - 4} ${p.x + NW + 4},${p.y + NH / 2} ${p.x + NW / 2},${p.y + NH + 4} ${p.x - 4},${p.y + NH / 2}`} fill="rgba(40,26,4,0.92)" stroke="#ffb020" strokeOpacity={0.75} />
                ) : (
                  <rect x={p.x} y={p.y} width={NW} height={NH} rx={kind === "start" || kind === "end" ? NH / 2 : 9}
                    fill={kind === "end" ? "rgba(6,40,30,0.92)" : "rgba(6,24,40,0.92)"} stroke={kind === "end" ? "#3dffb0" : kind === "io" ? "#b388ff" : c} strokeOpacity={0.6}
                    strokeDasharray={kind === "io" ? "5 3" : undefined} />
                )}
                <text x={p.x + NW / 2} y={p.y + NH / 2 + 4} textAnchor="middle" className="sb-slabel">{clip(s.label, kind === "decision" ? 20 : 26)}</text>
                <title>{s.detail || s.label}</title>
              </motion.g>
            );
          })}
        </svg>
      </div>
      <div className="sb-step-detail">{detail ? <><b>{detail.label}</b> {detail.detail}</> : <span className="muted">Hover a step for the detail. ◇ decisions · ◯ start / end · dashed = data in or out.</span>}</div>
    </div>
  );
}

// ------------------------------------------------------------------ routing matrix
function Routing({ rows, expanded }: { rows: any[]; expanded: boolean }) {
  const teams = [...new Set(rows.map((r) => r.team).filter(Boolean))];
  const tc = (t: string) => AUTO[Math.max(0, teams.indexOf(t)) % AUTO.length];
  if (!expanded) return (
    <div className="sb-route-c">
      {rows.map((r, i) => (
        <motion.div key={i} className="sb-rc" style={{ ["--c" as any]: tc(r.team) }} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.025 * i }}>
          <div className="sb-rc-w">{r.when}</div>
          <div className="sb-rc-r">{r.route}</div>
          <div className="sb-rc-m">
            {r.team && <em className="sb-team">{r.team}</em>}
            {r.status && <em className={`sb-status st-${slug(r.status)}`}>{r.status}</em>}
            {r.assignee && <span>{r.assignee}</span>}
            {r.by && <span className="by">by {r.by}</span>}
          </div>
        </motion.div>
      ))}
    </div>
  );
  return (
    <div className="sb-route">
      <div className="sb-route-h"><span>WHEN</span><span>WHAT HAPPENS</span><span>TEAM</span><span>STATUS</span><span>ASSIGNEE</span><span>BY</span></div>
      {rows.map((r, i) => (
        <motion.div key={i} className="sb-route-r" initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.03 * i }}>
          <span className="w">{r.when}</span><span>{r.route}</span>
          <span><em className="sb-team" style={{ ["--c" as any]: tc(r.team) }}>{r.team}</em></span>
          <span><em className={`sb-status st-${slug(r.status)}`}>{r.status}</em></span>
          <span className="muted">{r.assignee}</span><span className="muted">{r.by}</span>
        </motion.div>
      ))}
    </div>
  );
}
const slug = (s: string) => String(s || "").toLowerCase().replace(/[^a-z]+/g, "-");

// ------------------------------------------------------------------ lifecycle state machine
function Lifecycle({ l, expanded }: { l: any; expanded: boolean }) {
  const states = arr<any>(l?.states), trans = arr<any>(l?.transitions);
  const [hover, setHover] = useState<string | null>(null);
  const W = expanded ? 900 : 620, H = expanded ? 470 : 380, cx = W / 2, cy = H / 2 + 6, rx = W / 2 - 110, ry = H / 2 - 62;
  const pos = new Map(states.map((s, i) => {
    const a = (i / Math.max(1, states.length)) * Math.PI * 2 - Math.PI / 2;
    return [s.id, { x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a), c: STATUS_C[slug(s.label)] || AUTO[i % AUTO.length] }];
  }));
  const pairCount = new Map<string, number>();
  const sel = states.find((s) => s.id === hover);
  return (
    <div className="sb-life">
      <div className="sb-scroll">
        <svg viewBox={`0 0 ${W} ${H}`} className="sb-svg" style={{ minWidth: Math.min(W, 640) }}>
          <defs><marker id="sb-arrow3" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#9ff0ff" /></marker></defs>
          <ellipse cx={cx} cy={cy} rx={rx} ry={ry} fill="none" stroke="rgba(57,208,255,0.08)" strokeDasharray="3 6" />
          {trans.map((t, i) => {
            const a = pos.get(t.from), b = pos.get(t.to);
            if (!a || !b || a === b) return null;
            const key = [t.from, t.to].sort().join("|");
            const k = pairCount.get(key) ?? 0;
            pairCount.set(key, k + 1);
            const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
            const bend = 0.18 + k * 0.14;
            const qx = mx + (cx - mx) * bend * (t.from < t.to ? 1 : -1.4), qy = my + (cy - my) * bend * (t.from < t.to ? 1 : -1.4);
            const shrink = (px: number, py: number, tx: number, ty: number) => { const dx = tx - px, dy = ty - py, len = Math.hypot(dx, dy) || 1; return [px + (dx / len) * 46, py + (dy / len) * 22]; };
            const [sx, sy] = shrink(a.x, a.y, qx, qy), [ex, ey] = shrink(b.x, b.y, qx, qy);
            const on = !hover || hover === t.from || hover === t.to;
            return (
              <g key={i} className={`sb-edge k-event ${on ? "" : "dim"}`}>
                <path d={`M${sx},${sy} Q${qx},${qy} ${ex},${ey}`} className="sb-edge-base" markerEnd="url(#sb-arrow3)" />
                <path d={`M${sx},${sy} Q${qx},${qy} ${ex},${ey}`} className="sb-edge-flow" />
                {on && hover && <text x={(sx + 2 * qx + ex) / 4} y={(sy + 2 * qy + ey) / 4} className="sb-elabel" textAnchor="middle">{clip(t.trigger, 30)}</text>}
              </g>
            );
          })}
          {states.map((s) => {
            const p = pos.get(s.id)!;
            return (
              <g key={s.id} className="sb-node" onMouseEnter={() => setHover(s.id)} onMouseLeave={() => setHover(null)}>
                <rect x={p.x - 58} y={p.y - 19} width={116} height={38} rx={19} fill="rgba(6,24,40,0.94)" stroke={p.c} strokeOpacity={hover === s.id ? 1 : 0.6} />
                <circle cx={p.x - 42} cy={p.y} r={4.5} fill={p.c}><animate attributeName="opacity" values="1;0.35;1" dur="2.4s" repeatCount="indefinite" /></circle>
                <text x={p.x + 6} y={p.y + 4} textAnchor="middle" className="sb-nlabel">{clip(s.label, 14)}</text>
              </g>
            );
          })}
        </svg>
      </div>
      <div className="sb-step-detail">{sel ? <><b>{sel.label}</b> {sel.meaning}</> : <span className="muted">Hover a status to see what it means and what moves a ticket in or out of it.</span>}</div>
      <div className="sb-trans">
        {trans.filter((t) => !hover || t.from === hover || t.to === hover).map((t, i) => (
          <div key={i}><b>{lbl(states, t.from)}</b><i>→</i><b>{lbl(states, t.to)}</b><span>{t.trigger}</span><em>{t.by}</em></div>
        ))}
      </div>
    </div>
  );
}
const STATUS_C: Record<string, string> = { new: "#39d0ff", "on-you": "#ffb020", "on-customer": "#3dffb0", "on-hold": "#b388ff", closed: "#7f97a8" };
const lbl = (states: any[], id: string) => states.find((s) => s.id === id)?.label ?? id;

// ------------------------------------------------------------------ budgets / timers
function Budgets({ b }: { b: any[] }) {
  return (
    <div className="sb-budgets">
      {b.map((g, gi) => {
        const max = Math.max(1, ...arr<any>(g.items).map((x) => Number(x.value) || 0));
        return (
          <div key={gi} className="sb-budget">
            <div className="sb-budget-h">{g.label}{g.unit && <span>{g.unit}</span>}</div>
            {arr<any>(g.items).map((x, i) => (
              <div key={i} className="sb-bar">
                <span className="l">{x.label}</span>
                <span className="t"><motion.i initial={{ width: 0 }} animate={{ width: `${(100 * (Number(x.value) || 0)) / max}%` }} transition={{ delay: 0.06 * i + 0.1 * gi, duration: 0.7, ease: "easeOut" }}
                  style={{ background: `linear-gradient(90deg, ${AUTO[(gi + i) % AUTO.length]}55, ${AUTO[(gi + i) % AUTO.length]})` }} /></span>
                <b>{x.value}</b>
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ payloads / guardrails / faq / glossary
function Payloads({ p }: { p: any[] }) {
  return (
    <div className="sb-payloads">
      {p.map((x, i) => (
        <div key={i} className="sb-payload">
          <div className="sb-payload-h"><code>{x.endpoint}</code><b>{x.name}</b>{x.by && <em>{x.by}</em>}</div>
          <div className="sb-fields">{arr<any>(x.fields).map((f, j) => <div key={j}><code>{f.name}</code><span>{f.desc}</span></div>)}</div>
        </div>
      ))}
    </div>
  );
}

function Guardrails({ g }: { g: any[] }) {
  return (
    <div className="sb-guards">
      {g.map((x, i) => (
        <motion.div key={i} className="sb-guard" style={{ ["--c" as any]: AUTO[i % AUTO.length] }} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 * i }}>
          <div className="sb-guard-h">⛨ {x.group}</div>
          <ul>{arr<string>(x.rules).map((r, j) => <li key={j}>{r}</li>)}</ul>
        </motion.div>
      ))}
    </div>
  );
}

function Faq({ f }: { f: any[] }) {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <div className="sb-faq">
      {f.map((x, i) => (
        <div key={i} className={`sb-q ${open === i ? "on" : ""}`}>
          <button onClick={() => setOpen(open === i ? null : i)}><span>{open === i ? "−" : "+"}</span>{x.q}</button>
          <AnimatePresence initial={false}>
            {open === i && <motion.div className="sb-a" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}>{x.a}</motion.div>}
          </AnimatePresence>
        </div>
      ))}
    </div>
  );
}

function Glossary({ g }: { g: any[] }) {
  const [q, setQ] = useState("");
  const ref = useRef<HTMLInputElement>(null);
  const list = g.filter((x) => !q || `${x.term} ${x.def}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="sb-gloss">
      <input ref={ref} className="sb-search" placeholder="Filter terms…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={stop} />
      <div className="sb-gloss-grid">{list.map((x, i) => <div key={i}><b>{x.term}</b><span>{x.def}</span></div>)}</div>
    </div>
  );
}
