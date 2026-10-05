/** GARAGE: Stephen's 2003 Mercedes-Benz CL600. A tabbed vehicle display (overview, specs, service timeline, mods,
 *  maintenance gauges, known issues vs his car, documents, build plans) plus diagnosis, document-search and build-plan
 *  cards. Data comes from Core's garage profile (his Drive receipts + manual + expert knowledge). */
import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import type { Card } from "../types";
import { core } from "../ws/core";

const money = (v?: number | null) => (v == null ? "—" : `$${Math.round(v).toLocaleString()}`);
const range = (lo?: number | null, hi?: number | null) => (hi && lo && hi !== lo ? `${money(lo)}–${money(hi).slice(1)}` : money(lo));
const miles = (v?: number | null) => (v == null ? "—" : `${Math.round(v).toLocaleString()} mi`);
const day = (iso?: string | null) => (iso ? new Date(iso + "T12:00:00").toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" }) : "—");
const open = (u?: string | null) => u && window.jarvis?.open(u);
const fileUrl = (id?: string | null) => (id ? core.garageFileUrl(id) : "");

const TABS: [string, string][] = [["overview", "Overview"], ["specs", "Specs"], ["service", "Service"], ["mods", "Mods"],
  ["maintenance", "Maintenance"], ["issues", "Known issues"], ["docs", "Documents"], ["plans", "Plans"]];
const SYS_ICON: Record<string, string> = { engine: "⚙", transmission: "⛭", suspension: "⌇", brakes: "◎", electrical: "ϟ", cooling: "❄",
  fuel: "⛽", exhaust: "≋", steering: "◐", interior: "▣", exterior: "◇", wheels_tires: "◉", hvac: "✻", other: "•" };

export function GarageCard({ card, expanded = false }: { card: Card; expanded?: boolean }) {
  const d = card.data ?? {};
  const p = d.profile ?? {};
  const [tab, setTab] = useState<string>(d.tab || "overview");
  useEffect(() => setTab(d.tab || "overview"), [d.tab, d.updated]);
  return (
    <div className={`gr ${expanded ? "xl" : ""}`}>
      <Hero p={p} heroId={d.hero_id} />
      <div className="gr-tabs" onClick={(e) => e.stopPropagation()}>
        {TABS.map(([k, label]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      <motion.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="gr-body">
        {tab === "overview" && <Overview p={p} go={setTab} />}
        {tab === "specs" && <Specs specs={p.specs ?? []} />}
        {tab === "service" && <Service hist={p.service_history ?? []} />}
        {tab === "mods" && <Mods mods={p.mods ?? []} notes={p.configuration_notes ?? []} />}
        {tab === "maintenance" && <Maintenance items={p.maintenance ?? []} est={p.estimated_mileage} />}
        {tab === "issues" && <Issues items={p.known_issues ?? []} />}
        {tab === "docs" && <Docs docs={p.documents ?? []} />}
        {tab === "plans" && <Plans plans={d.plans ?? []} />}
      </motion.div>
    </div>
  );
}

function Hero({ p, heroId }: { p: any; heroId?: string }) {
  const v = p.vehicle ?? {};
  const spent = p.totals?.documented_spend;
  return (
    <div className="gr-hero">
      <div className="gr-photo">{heroId ? <img src={fileUrl(heroId)} alt="" /> : <span>🚘</span>}<i /></div>
      <div className="gr-id">
        <div className="gr-badge">{v.chassis || "C215"} · {v.engine?.split(",")[0] || "M275 V12 Biturbo"}</div>
        <div className="gr-name">{v.year || 2003} Mercedes-Benz {v.model || "CL600"}</div>
        <div className="gr-meta">{[v.color, v.interior && `${v.interior} interior`, v.vin && `VIN …${String(v.vin).slice(-6)}`].filter(Boolean).join(" · ")}</div>
        <div className="gr-stats">
          <div><b>{miles(p.estimated_mileage ?? v.current_mileage)}</b><span>{v.mileage_as_of ? `last recorded ${day(v.mileage_as_of)}` : "odometer"}</span></div>
          <div><b>{(p.service_history ?? []).length}</b><span>service records</span></div>
          <div><b>{(p.mods ?? []).length}</b><span>modifications</span></div>
          <div><b>{money(spent)}</b><span>documented spend</span></div>
        </div>
      </div>
    </div>
  );
}

function Overview({ p, go }: { p: any; go: (t: string) => void }) {
  const due = (p.maintenance ?? []).filter((m: any) => m.status === "overdue" || m.status === "due_soon");
  const last = (p.service_history ?? [])[0];
  const watch = (p.known_issues ?? []).filter((i: any) => i.applies === "watch" || (i.applies === "yes" && i.severity !== "low"));
  return (
    <div className="gr-ov">
      {(p.configuration_notes ?? []).length > 0 && (
        <div className="gr-config">{p.configuration_notes.map((n: string, i: number) => <span key={i}>⚑ {n}</span>)}</div>
      )}
      <div className="gr-ov-grid">
        <button className="gr-tile" onClick={(e) => { e.stopPropagation(); go("maintenance"); }}>
          <h4>NEEDS ATTENTION</h4>
          {due.length ? due.slice(0, 4).map((m: any, i: number) => <div key={i} className={`gr-due ${m.status}`}>{m.item}<em>{m.status === "overdue" ? "OVERDUE" : "SOON"}</em></div>)
            : <div className="muted small">Nothing due by your records.</div>}
        </button>
        <button className="gr-tile" onClick={(e) => { e.stopPropagation(); go("service"); }}>
          <h4>LAST SERVICE</h4>
          {last ? <><div className="gr-tt">{last.title}</div><div className="muted small">{day(last.date)}{last.vendor ? ` · ${last.vendor}` : ""}{last.cost ? ` · ${money(last.cost)}` : ""}</div></> : <div className="muted small">—</div>}
        </button>
        <button className="gr-tile" onClick={(e) => { e.stopPropagation(); go("mods"); }}>
          <h4>MODIFICATIONS</h4>
          {(p.mods ?? []).slice(0, 4).map((m: any, i: number) => <div key={i} className="gr-chip">{m.name}</div>)}
        </button>
        <button className="gr-tile" onClick={(e) => { e.stopPropagation(); go("issues"); }}>
          <h4>WATCH LIST</h4>
          {watch.slice(0, 4).map((x: any, i: number) => <div key={i} className={`gr-sev ${x.severity}`}>{x.title}</div>)}
          {!watch.length && <div className="muted small">No open concerns.</div>}
        </button>
      </div>
      <SpendChart hist={p.service_history ?? []} />
    </div>
  );
}

function SpendChart({ hist }: { hist: any[] }) {
  const byYear = useMemo(() => {
    const m: Record<string, { m: number; u: number }> = {};
    for (const e of hist) {
      if (!e.date || !e.cost) continue;
      const y = e.date.slice(0, 4);
      m[y] ||= { m: 0, u: 0 };
      if (e.category === "upgrade") m[y].u += e.cost; else m[y].m += e.cost;
    }
    return Object.entries(m).sort();
  }, [hist]);
  if (!byYear.length) return null;
  const max = Math.max(...byYear.map(([, v]) => v.m + v.u), 1);
  return (
    <div className="gr-spend">
      <h4>SPEND BY YEAR <span><i className="m" /> maintenance & repair <i className="u" /> upgrades</span></h4>
      <div className="gr-bars">
        {byYear.map(([y, v], i) => (
          <div key={y} className="gr-bar">
            <motion.div className="u" initial={{ height: 0 }} animate={{ height: `${(v.u / max) * 100}%` }} transition={{ delay: 0.05 * i }} />
            <motion.div className="m" initial={{ height: 0 }} animate={{ height: `${(v.m / max) * 100}%` }} transition={{ delay: 0.05 * i }} />
            <b>{money(v.m + v.u)}</b><span>{y}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Specs({ specs }: { specs: any[] }) {
  const groups = useMemo(() => {
    const g: Record<string, any[]> = {};
    for (const s of specs) (g[s.group || "Other"] ||= []).push(s);
    return Object.entries(g);
  }, [specs]);
  return (
    <div className="gr-specs">
      {groups.map(([g, rows]) => (
        <div key={g} className="gr-spec-g">
          <h4>{g.toUpperCase()}</h4>
          {rows.map((s, i) => <div key={i} className="gr-kv"><span>{s.label}</span><b>{s.value}{s.source === "doc" && <i title="From your documents">●</i>}</b></div>)}
        </div>
      ))}
      <div className="muted small">● = confirmed in your documents · others are factory specs</div>
    </div>
  );
}

function Service({ hist }: { hist: any[] }) {
  const [q, setQ] = useState("");
  const rows = hist.filter((e) => !q || JSON.stringify(e).toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="gr-svc">
      <input className="gr-filter" placeholder="Filter service history…" value={q} onClick={(e) => e.stopPropagation()} onChange={(e) => setQ(e.target.value)} />
      <div className="gr-timeline">
        {rows.map((e, i) => (
          <motion.div key={i} className={`gr-ev ${e.category}`} initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(0.02 * i, 0.4) }}>
            <div className="gr-dot">{SYS_ICON[e.system] ?? "•"}</div>
            <div className="gr-ev-main">
              <div className="gr-ev-h"><b>{e.title}</b><span>{money(e.cost)}</span></div>
              <div className="muted small">{day(e.date)}{e.mileage ? ` · ${miles(e.mileage)}` : ""}{e.vendor ? ` · ${e.vendor}` : ""}{e.source === "logged" ? " · logged by voice" : ""}</div>
              {(e.items ?? []).length > 0 && <div className="gr-items">{e.items.map((x: string, k: number) => <span key={k}>{x}</span>)}</div>}
              {(e.parts ?? []).filter((x: any) => x.part_number).length > 0 && (
                <div className="gr-parts">{e.parts.filter((x: any) => x.part_number).map((x: any, k: number) => <code key={k}>{x.part_number}</code>)}</div>
              )}
              {(e.docs ?? []).map((x: any) => <button key={x.id} className="gr-doc-link" onClick={(ev) => { ev.stopPropagation(); open(x.link); }}>📄 {x.title}</button>)}
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

function Mods({ mods, notes }: { mods: any[]; notes: string[] }) {
  return (
    <div className="gr-mods">
      {notes.length > 0 && <div className="gr-config">{notes.map((n, i) => <span key={i}>⚑ {n}</span>)}</div>}
      <div className="gr-mod-grid">
        {mods.map((m, i) => (
          <motion.div key={i} className={`gr-mod ${m.category}`} initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} transition={{ delay: 0.04 * i }}>
            <div className="gr-mod-cat">{String(m.category || "").replace("_", " ").toUpperCase()}</div>
            <div className="gr-tt">{m.name}</div>
            <div className="muted small">{m.details}</div>
            <div className="gr-mod-f"><span>{day(m.date)}</span><span>{m.vendor}</span><b>{money(m.cost)}</b></div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

function Maintenance({ items, est }: { items: any[]; est?: number }) {
  const order = { overdue: 0, due_soon: 1, ok: 2, unknown: 3 } as Record<string, number>;
  const rows = [...items].sort((a, b) => (order[a.status] ?? 4) - (order[b.status] ?? 4));
  return (
    <div className="gr-mx">
      <div className="muted small">Last recorded odometer {miles(est)} · status by date and recorded mileage</div>
      {rows.map((m, i) => (
        <div key={i} className={`gr-mx-row ${m.status}`}>
          <div className="gr-mx-h"><b>{m.item}</b><em>{m.status === "overdue" ? "OVERDUE" : m.status === "due_soon" ? "DUE SOON" : m.status === "ok" ? "OK" : "NO RECORD"}</em></div>
          <div className="gr-gauge"><motion.i initial={{ width: 0 }} animate={{ width: `${(m.remaining_pct ?? 0) * 100}%` }} /></div>
          <div className="muted small">
            {m.last_date ? `Last ${day(m.last_date)}${m.last_mileage ? ` @ ${miles(m.last_mileage)}` : ""}` : "No record in your files"}
            {m.interval_miles || m.interval_months ? ` · every ${[m.interval_miles && miles(m.interval_miles), m.interval_months && `${m.interval_months} mo`].filter(Boolean).join(" / ")}` : ""}
            {m.miles_left != null ? ` · ${m.miles_left >= 0 ? `${m.miles_left.toLocaleString()} mi left` : `${(-m.miles_left).toLocaleString()} mi over`}` : ""}
          </div>
          {m.spec && <div className="gr-spec-line">{m.spec}</div>}
        </div>
      ))}
    </div>
  );
}

function Issues({ items }: { items: any[] }) {
  const lab: Record<string, string> = { yes: "APPLIES", no: "N/A", resolved: "RESOLVED", watch: "WATCH" };
  return (
    <div className="gr-iss">
      {items.map((x, i) => (
        <div key={i} className={`gr-iss-row ${x.applies} sev-${x.severity}`}>
          <div className="gr-iss-h"><span className="gr-sys">{SYS_ICON[x.system] ?? "•"}</span><b>{x.title}</b><em>{lab[x.applies] ?? x.applies}</em></div>
          <div className="small">{x.why}</div>
          {x.symptoms && <div className="muted small">Symptoms: {x.symptoms}</div>}
          {x.fix && <div className="muted small">Fix: {x.fix}</div>}
        </div>
      ))}
    </div>
  );
}

function Docs({ docs }: { docs: any[] }) {
  const cats = useMemo(() => {
    const g: Record<string, any[]> = {};
    for (const x of docs) (g[x.category || "Other"] ||= []).push(x);
    return Object.entries(g);
  }, [docs]);
  return (
    <div className="gr-docs">
      {cats.map(([c, xs]) => (
        <div key={c}>
          <h4>{c.toUpperCase()} <span>{xs.length}</span></h4>
          <div className="gr-doc-grid">
            {xs.map((x) => (
              <button key={x.id} className="gr-docx" onClick={(e) => { e.stopPropagation(); open(x.link); }} title={x.summary}>
                <div className="gr-thumb">{String(x.mime || "").startsWith("image/") ? <img src={fileUrl(x.id)} alt="" loading="lazy" /> : <span>{String(x.mime || "").includes("pdf") ? "PDF" : "DOC"}</span>}</div>
                <div className="gr-doc-t">{x.title}</div>
                <div className="muted small">{x.date ? day(x.date) : x.folder}</div>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function Plans({ plans }: { plans: any[] }) {
  if (!plans.length) return <div className="muted">No build plans yet. Ask JARVIS to plan an upgrade.</div>;
  return <div className="gr-plans">{plans.map((p) => <PlanView key={p.id} plan={p} />)}</div>;
}

function PlanView({ plan }: { plan: any }) {
  return (
    <div className="gr-plan">
      <div className="gr-plan-h"><div><div className="gr-tt">{plan.title}</div><div className="muted small">{plan.goal}</div></div><b>{range(plan.total, plan.total_high)}</b></div>
      <div className="gr-stages">
        {(plan.stages ?? []).map((s: any, i: number) => (
          <motion.div key={i} className="gr-stage" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.08 * i }}>
            <div className="gr-stage-n">STAGE {i + 1}</div>
            <div className="gr-stage-h"><b>{s.name}</b><span>{range(s.est_total, s.est_total_high)}</span></div>
            {(s.items ?? []).map((it: any, k: number) => (
              <div key={k} className="gr-pi"><span>{it.part}{it.brand ? <em> · {it.brand}</em> : null}</span>{it.part_number && <code>{it.part_number}</code>}<b>{it.est_cost ? range(it.est_cost, it.est_cost_high) : ""}</b></div>
            ))}
            {s.notes && <div className="muted small">{s.notes}</div>}
          </motion.div>
        ))}
      </div>
      {(plan.considerations ?? []).length > 0 && <div className="gr-consider">{plan.considerations.map((c: string, i: number) => <div key={i}>⚑ {c}</div>)}</div>}
    </div>
  );
}

export function GaragePlanCard({ card }: { card: Card }) {
  return <div className="gr"><PlanView plan={card.data ?? {}} /></div>;
}

export function GarageSearchCard({ card }: { card: Card }) {
  const hits: any[] = card.data?.hits ?? [];
  return (
    <div className="gr gr-search">
      {hits.map((h, i) => (
        <motion.button key={i} className="gr-hit" initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.04 * i }} onClick={(e) => { e.stopPropagation(); open(h.link); }}>
          <div className="gr-hit-h"><b>📄 {h.doc}</b><span>{h.page ? `p. ${h.page}` : ""}{h.date ? ` · ${day(h.date)}` : ""}</span></div>
          <div className="gr-hit-x">{h.excerpt}</div>
        </motion.button>
      ))}
    </div>
  );
}

export function GarageDiagnosisCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  const urg: Record<string, [string, string]> = { safe_to_drive: ["SAFE TO DRIVE", "ok"], drive_carefully: ["DRIVE CAREFULLY", "warn"], stop_driving: ["STOP DRIVING", "bad"] };
  const [label, tone] = urg[d.urgency] ?? ["ASSESSING", "warn"];
  return (
    <div className="gr gr-dx">
      <div className="gr-dx-top">
        {(d.media ?? []).length > 0 && (
          <div className="gr-dx-media">
            {d.media.slice(0, 3).map((m: any) => m.kind === "video"
              ? <video key={m.id} src={core.artifactUrl(m.id, m.version)} muted loop autoPlay playsInline />
              : <img key={m.id} src={core.artifactUrl(m.id, m.version)} alt="" />)}
          </div>
        )}
        <div className="gr-dx-sum">
          <span className={`gr-urg ${tone}`}>{label}</span>
          <div className="gr-tt">{d.summary}</div>
          {d.symptoms && <div className="muted small">“{d.symptoms}”</div>}
        </div>
      </div>
      <h4>LIKELY CAUSES</h4>
      {(d.causes ?? []).map((c: any, i: number) => (
        <div key={i} className="gr-cause">
          <div className="gr-cause-h"><b>{c.cause}</b><span>{c.likelihood}%</span></div>
          <div className="gr-gauge"><motion.i initial={{ width: 0 }} animate={{ width: `${c.likelihood}%` }} transition={{ delay: 0.1 * i }} /></div>
          <div className="small">{c.why}</div>
          <div className="muted small">Fix: {c.fix} · {String(c.diy || "").toUpperCase()} · {c.est_cost}</div>
          {(c.parts ?? []).filter((p: any) => p.part_number).map((p: any, k: number) => <code key={k} className="gr-pn" title="Verify against your VIN before ordering">{p.name}: {p.part_number} · verify</code>)}
        </div>
      ))}
      {(d.checks ?? []).length > 0 && <><h4>NEXT CHECKS</h4><ol className="gr-checks">{d.checks.map((c: string, i: number) => <li key={i}>{c}</li>)}</ol></>}
      {(d.related_history ?? []).length > 0 && <><h4>RELATED HISTORY</h4>{d.related_history.map((r: string, i: number) => <div key={i} className="muted small">↺ {r}</div>)}</>}
      {d.follow_up_question && <div className="gr-q">? {d.follow_up_question}</div>}
    </div>
  );
}
