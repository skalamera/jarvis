import { useContext, useEffect, useMemo, useRef, useState } from "react";
import type { Card } from "../types";
import { core } from "../ws/core";
import { useStore } from "../state/store";
import { MaxCtx } from "./HoloCard";

/* Trip planner: describe a trip (typed or spoken) -> researched options + three hour-by-hour itineraries.
   Sidebar = summary, the 3 options, quick revise. Expanded = full planner (itineraries, overview, hotels, events,
   sights, dining) with choose / PDF / Drive / undo. Every change is also just "tell JARVIS" (trip_revise). */

const stop = (e: React.SyntheticEvent) => e.stopPropagation();
const toast = (text: string, error = false) => useStore.getState().toast({ text, error });
const TYPE_ICON: Record<string, string> = { breakfast: "☕", lunch: "🥪", dinner: "🍽", activity: "◆", event: "🎟", transit: "➜",
  hotel: "🛏", free: "☀", nightlife: "🍸" };
const EXAMPLES = ["Long weekend in Charleston next month, foodie, by the water, no hiking",
  "5 days in Lisbon in May for 2, mid-range, lots of walking and seafood",
  "Ski weekend in Vermont in January, cozy lodge, driving from home"];

const newId = () => Math.random().toString(16).slice(2, 12).padEnd(10, "0");
const fmtDay = (s: string) => { const d = new Date(s + "T12:00:00"); return isNaN(+d) ? s : d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }); };
const fmtRange = (b: any) => b ? `${fmtDay(b.start_date)} – ${fmtDay(b.end_date)}` : "";

function useTripRun(card: Card) {
  const d = card.data || {};
  const [steps, setSteps] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const busy = d.status === "planning" || d.status === "revising";
  const started = useRef(0);
  useEffect(() => {
    if (!busy || !d.id) return;
    started.current = Date.now();
    let alive = true;
    const tick = async () => {
      const r = await core.rpc("trip_progress", { trip_id: d.id });
      if (!alive || !r.ok) return;
      setSteps(r.result.steps || []);
      if (r.result.error) { setErr(r.result.error); core.patchCard(card.id, { ...d, status: d.itineraries ? "ready" : "error", error: r.result.error }); return; }
      if (r.result.done && d.local) {   // started from this card: fetch the finished plan ourselves
        const t = await core.rpc("trip_get", { trip_id: d.id });
        if (t.ok) core.patchCard(card.id, t.result, t.result.title);
      }
    };
    tick();
    const t = setInterval(tick, 1500);
    return () => { alive = false; clearInterval(t); };
  }, [busy, d.id, d.status]);
  const plan = async (request: string) => {
    const id = newId();
    core.patchCard(card.id, { kind: "trip", key: `trip:${id}`, id, status: "planning", request, local: true }, "Trip planner");
    const r = await core.rpc("trip_plan_async", { request, trip_id: id });
    if (!r.ok) toast(r.error || "Couldn't start planning", true);
  };
  const revise = async (instruction: string) => {
    if (!d.id) return;
    setSteps([]); setErr("");
    core.patchCard(card.id, { ...d, status: "revising", pending_change: instruction, local: true });
    const r = await core.rpc("trip_revise_async", { instruction, trip_id: d.id });
    if (!r.ok) toast(r.error || "Couldn't start the change", true);
  };
  return { steps, err, busy, plan, revise };
}

/* ---------------------------------------------------------------- shared pieces */

function Composer({ onSubmit, placeholder, big, disabled }: { onSubmit: (t: string) => void; placeholder: string; big?: boolean; disabled?: boolean }) {
  const [t, setT] = useState("");
  const go = () => { const v = t.trim(); if (v.length < 4) return; onSubmit(v); setT(""); };
  return (
    <div className={`tp-comp${big ? " big" : ""}`} onClick={stop}>
      <textarea value={t} disabled={disabled} rows={big ? 3 : 1} placeholder={placeholder}
        onChange={(e) => setT(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); go(); } }} />
      <button className="tp-mic" title="Talk to JARVIS" onClick={() => core.listenNow()}>🎙</button>
      <button className="tp-go" disabled={disabled || t.trim().length < 4} onClick={go}>{big ? "Plan trip" : "Change"}</button>
    </div>
  );
}

function Forge({ steps, request, change }: { steps: string[]; request?: string; change?: string }) {
  const all = steps.length ? steps : ["Reading your request"];
  return (
    <div className="tp-forge">
      <div className="tp-globe"><i /><i /><i /><b>✈</b></div>
      <div className="tp-fq">{change ? <>Applying: <em>“{change}”</em></> : <em>“{request}”</em>}</div>
      <ol className="tp-steps">
        {all.map((s, i) => <li key={i} className={i === all.length - 1 ? "now" : "done"}>{i === all.length - 1 ? <span className="cx-spin" /> : "✓"} {s}</li>)}
      </ol>
      <div className="tp-fnote">Researching live sources: weather, hotels, events, transport. This takes a couple of minutes.</div>
    </div>
  );
}

function Empty({ onPlan }: { onPlan: (t: string) => void }) {
  return (
    <div className="tp-empty" onClick={stop}>
      <div className="tp-eh">Where to, sir?</div>
      <div className="tp-es">Describe the trip in your own words: where, when, who's going, budget, and the vibe. Or just tell me out loud.</div>
      <Composer big onSubmit={onPlan} placeholder="e.g. A long weekend in Charleston late October for two, great food, things by the water, no hiking" />
      <div className="tp-ex">{EXAMPLES.map((x) => <button key={x} onClick={() => onPlan(x)}>{x}</button>)}</div>
    </div>
  );
}

function Meta({ d }: { d: any }) {
  const b = d.brief || {};
  return (
    <div className="tp-meta">
      <span>📅 {fmtRange(b)} · {b.nights} night{b.nights === 1 ? "" : "s"}</span>
      <span>👥 {b.adults || 2}{b.children ? ` + ${b.children}` : ""}</span>
      <span>💳 {b.budget}</span>
      {(b.themes || []).map((t: string) => <span key={t} className="tp-theme">{t}</span>)}
      {(b.avoid || []).map((t: string) => <span key={t} className="tp-theme no">no {t.replace(/^no /i, "")}</span>)}
    </div>
  );
}

function WeatherStrip({ w }: { w: any }) {
  if (!w) return null;
  return (
    <div className="tp-wx">
      {(w.days || []).length ? (w.days.map((x: any) => (
        <div key={x.date} className="tp-wd"><b>{fmtDay(x.date)}</b><span className="ic">{x.icon || "·"}</span>
          <span>{x.hi}° / {x.lo}°</span><small>{x.condition}{x.pop != null ? ` · ${x.pop}%` : ""}</small></div>
      ))) : (
        <div className="tp-wd typ"><b>Typical</b><span>{w.avg_high ?? "–"} / {w.avg_low ?? "–"}</span><small>{w.rain || ""}</small></div>
      )}
      <div className="tp-wsum">{w.summary}{w.source === "typical" ? " (seasonal averages; a live forecast appears within two weeks of the trip)" : ""}</div>
    </div>
  );
}

/* ---------------------------------------------------------------- itinerary */

function Itinerary({ d, i, cardId }: { d: any; i: number; cardId: string }) {
  const it = d.itineraries[i];
  const h = it.hotel_info || {};
  const [saving, setSaving] = useState("");
  const [dayIx, setDayIx] = useState(-1);
  const exp = async (to: "local" | "drive", account = "personal") => {
    setSaving(to + account);
    const r = await core.workspace("trip_export", { trip_id: d.id, index: i, to, account });
    setSaving("");
    if (!r.ok) return toast(r.error || "Export failed", true);
    toast(to === "drive" ? `Saved to ${account} Drive: ${r.result.name}` : `Saved to Downloads: ${r.result.name}`);
    if (to === "drive" && r.result.url) core.openExternal(r.result.url);
  };
  const choose = async () => {
    const r = await core.workspace("trip_choose", { trip_id: d.id, index: d.chosen === i ? -1 : i });
    if (r.ok) core.patchCard(cardId, r.result);
  };
  const days = it.days || [];
  return (
    <div className="tp-it">
      <div className="tp-ith">
        {h.photo && <img src={h.photo} alt="" />}
        <div className="tp-ithx">
          <div className="tp-itn"><span className="tp-badge">{it.id}</span>{it.name}{d.chosen === i && <span className="tp-chosen">✓ Chosen</span>}</div>
          <div className="tp-its">{it.summary}</div>
          <div className="tp-ithl">🛏 <b>{it.hotel}</b>{h.rating ? ` · ★${h.rating}` : ""}{h.live ? <> · <span className="tp-livebadge">● LIVE</span> {h.live.nightly}/night</> : h.est_nightly ? ` · ${h.est_nightly}/night` : ""}
            <span> — {it.hotel_why}</span></div>
          <div className="tp-itt">Est. total <b>{it.est_total}</b></div>
        </div>
        <div className="tp-acts" onClick={stop}>
          <button className={d.chosen === i ? "on" : ""} onClick={choose}>{d.chosen === i ? "✓ Chosen" : "Choose this"}</button>
          <button disabled={!!saving} onClick={() => exp("local")}>{saving === "localpersonal" ? "Saving…" : "⤓ Save PDF"}</button>
          <button disabled={!!saving} onClick={() => exp("drive", "personal")}>{saving === "drivepersonal" ? "Uploading…" : "☁ Drive · Personal"}</button>
          <button disabled={!!saving} onClick={() => exp("drive", "work")}>{saving === "drivework" ? "Uploading…" : "☁ Drive · Work"}</button>
        </div>
      </div>
      <div className="tp-dtabs" onClick={stop}>
        <button className={dayIx < 0 ? "on" : ""} onClick={() => setDayIx(-1)}>All days</button>
        {days.map((x: any, k: number) => <button key={k} className={dayIx === k ? "on" : ""} onClick={() => setDayIx(k)}>Day {k + 1} · {fmtDay(x.date)}</button>)}
      </div>
      {days.map((day: any, k: number) => (dayIx < 0 || dayIx === k) && (
        <section key={k} className="tp-day">
          <h4><span>Day {k + 1}</span> {fmtDay(day.date)} <em>{day.title}</em></h4>
          <div className="tp-tl">
            {(day.items || []).map((x: any, n: number) => (
              <div key={n} className={`tp-row t-${x.type}`}>
                <div className="tp-time">{x.time}{x.end ? <small>{x.end}</small> : null}</div>
                <div className="tp-dot">{TYPE_ICON[x.type] || "•"}</div>
                <div className="tp-body">
                  <div className="tp-rt">{x.title}{x.place ? <span className="tp-pl"> · {x.place}</span> : null}{x.rating ? <span className="tp-rat"> ★{x.rating}</span> : null}</div>
                  <div className="tp-rd">{x.detail}</div>
                  {x.address && <div className="tp-ra">{x.address}</div>}
                </div>
                {x.photo ? <img className="tp-ph" src={x.photo} alt="" /> : <div />}
                <div className="tp-cost">{x.cost}</div>
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- overview tabs */

const hm = (s?: string) => { const x = new Date(s || ""); return isNaN(+x) ? "" : x.toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" }); };
const legTxt = (l: any) => l ? `${l.origin}→${l.destination} · ${hm(l.depart)} → ${hm(l.arrive)} · ${l.duration || ""} · ${l.stops ? `${l.stops} stop${l.stops > 1 ? "s" : ""}${l.via?.length ? " via " + l.via.join(", ") : ""}` : "nonstop"}` : "";

function LiveFares({ d, cardId }: { d: any; cardId: string }) {
  const lf = d.transport?.live_flights;
  const [busy, setBusy] = useState(false);
  if (!lf?.offers?.length) return null;
  const age = Math.round((Date.now() / 1000 - (lf.searched_at || 0)) / 60);
  const stale = age > 25;
  const refresh = async () => {
    setBusy(true);
    const r = await core.rpc("trip_refresh_flights", { trip_id: d.id }, 120_000);
    setBusy(false);
    if (r.ok) { core.patchCard(cardId, r.result, r.result.title); toast("Live prices refreshed (flights, hotels, cars)."); } else toast(r.error || "Couldn't refresh prices", true);
  };
  return (
    <div className="tp-live" onClick={stop}>
      <div className="tp-liveh"><span className="tp-livebadge">● LIVE FARES</span> Duffel · {lf.passengers} traveller{lf.passengers > 1 ? "s" : ""} · round trip ·
        <em className={stale ? "old" : ""}> searched {age < 1 ? "just now" : `${age} min ago`}{stale ? " (offers expire, refresh before booking)" : ""}</em>
        <button disabled={busy} onClick={refresh}>{busy ? "Searching…" : "↻ Refresh"}</button></div>
      {lf.offers.slice(0, 6).map((o: any) => (
        <div key={o.offer_id} className="tp-fare">
          {o.logo ? <img src={o.logo} alt="" /> : <span className="tp-noimg sm">✈</span>}
          <div className="tp-fl"><b>{o.airline}</b><span>Out {legTxt(o.out)}</span>{o.back && <span>Back {legTxt(o.back)}</span>}
            <small>{[o.cabin, o.bags, o.refundable ? "refundable" : "non-refundable", o.changeable ? "changeable" : ""].filter(Boolean).join(" · ")}</small></div>
          <div className="tp-fp"><b>{o.price}</b><small>{o.currency === "USD" ? "$" : ""}{o.per_person}/person</small>
            <button disabled={stale} title={stale ? "Refresh fares first" : "JARVIS shows a confirm card before anything is booked"}
              onClick={() => core.sendText(`Book flight offer ${o.offer_id} (${o.airline}, ${o.price} round trip for the ${d.title} trip)`)}>Book…</button></div>
        </div>
      ))}
    </div>
  );
}

function Overview({ d, cardId }: { d: any; cardId: string }) {
  const t = d.transport || {};
  const mx = useContext(MaxCtx);
  const openSite = (url: string, title: string) => {   // step out of the planner's expanded view so the site is on top
    mx?.setMax(false);
    window.setTimeout(() => core.launchCard({ kind: "webapp", title, account: null, data: { url, partition: "persist:cars" } }), 80);
  };
  return (
    <div className="tp-ov">
      <div className="tp-sec"><h5>The trip</h5><p>{d.summary}</p><ul className="tp-hl">{(d.highlights || []).map((x: string) => <li key={x}>{x}</li>)}</ul></div>
      <div className="tp-sec"><h5>Weather</h5><WeatherStrip w={d.weather} /></div>
      <div className="tp-sec"><h5>What to wear</h5><p>{d.attire?.summary}</p>
        <div className="tp-pack">{(d.attire?.pack || []).map((x: string) => <span key={x}>{x}</span>)}</div></div>
      <div className="tp-sec"><h5>Getting there</h5><p className="tp-rec">{t.recommended}</p><LiveFares d={d} cardId={cardId} />
        <div className="tp-grid2">{(t.getting_there || []).map((x: any, k: number) => (
          <div key={k} className="tp-opt"><b>{(x.mode || "").toUpperCase()}</b><span>{x.summary}</span>
            <small>{[x.duration, x.est_cost].filter(Boolean).join(" · ")}</small>{x.notes && <small>{x.notes}</small>}</div>))}</div>
        {!t.live_flights && (t.flights || []).length > 0 && <table className="tp-tab"><thead><tr><th>Airline</th><th>Route</th><th>Time</th><th>Est. round trip</th></tr></thead>
          <tbody>{t.flights.map((f: any, k: number) => <tr key={k}><td>{f.airline}</td><td>{f.route}{f.nonstop ? " · nonstop" : ""}</td><td>{f.typical_duration}</td><td>{f.est_round_trip}</td></tr>)}</tbody></table>}
      </div>
      {t.live_cars?.cars?.length > 0 && <div className="tp-sec"><h5>Car rentals</h5>
        <div className="tp-liveh"><span className="tp-livebadge">● LIVE PRICES</span> {t.live_cars.location} · whole rental · book on the provider's site</div>
        {t.live_cars.cars.map((c: any) => (
          <div key={c.booking_url} className="tp-fare" onClick={stop}>
            {c.photo ? <img src={c.photo} alt="" /> : <span className="tp-noimg sm">🚗</span>}
            <div className="tp-fl"><b>{c.name} <span style={{ fontWeight: 400, color: "var(--muted)" }}>or similar · {c.category}</span></b>
              <span>{c.supplier} · {c.seats} seats · {c.bags} bags · {c.transmission}</span>
              <small>{[c.free_cancellation ? "free cancellation" : "", c.mileage ? `${c.mileage} miles` : "", c.deposit ? `$${c.deposit} deposit` : ""].filter(Boolean).join(" · ")}</small></div>
            <div className="tp-fp"><b>{c.price}</b><small>${c.per_day}/day</small>
              <button onClick={() => openSite(c.booking_url, `Rental · ${c.supplier} ${c.name}`)}>Book ↗</button></div>
          </div>))}
      </div>}
      {!t.live_cars && (t.car_rentals || []).length > 0 && <div className="tp-sec"><h5>Car rentals</h5>
        <table className="tp-tab"><thead><tr><th>Company</th><th>Class</th><th>Est. per day</th><th>Pickup</th></tr></thead>
          <tbody>{t.car_rentals.map((c: any, k: number) => <tr key={k}><td>{c.company}</td><td>{c.category}</td><td>{c.est_daily}</td><td>{c.pickup}</td></tr>)}</tbody></table></div>}
      {(t.getting_around || []).length > 0 && <div className="tp-sec"><h5>Getting around</h5><ul>{t.getting_around.map((x: string) => <li key={x}>{x}</li>)}</ul></div>}
      {(d.tips || []).length > 0 && <div className="tp-sec"><h5>Good to know</h5><ul>{d.tips.map((x: string) => <li key={x}>{x}</li>)}</ul></div>}
      {(d.brief?.assumptions || []).length > 0 && <div className="tp-sec dim"><h5>Assumptions</h5><ul>{d.brief.assumptions.map((x: string) => <li key={x}>{x}</li>)}</ul></div>}
    </div>
  );
}

function Hotels({ d, onRevise }: { d: any; onRevise: (t: string) => void }) {
  const liveAge = d.hotels_live_at ? Math.round((Date.now() / 1000 - d.hotels_live_at) / 60) : null;
  const users = (name: string) => (d.itineraries || []).filter((it: any) => it.hotel === name).map((it: any) => it.id);
  return (
    <>{liveAge != null && <div className="tp-liveh" style={{ marginTop: 8 }}><span className="tp-livebadge">● LIVE RATES</span> bookable via LiteAPI · checked {liveAge < 1 ? "just now" : `${liveAge} min ago`}{liveAge > 25 ? " (refresh in Overview before booking)" : ""}</div>}
    <div className="tp-tiles">
      {(d.hotels || []).map((h: any) => (
        <div key={h.id || h.name} className="tp-tile">
          {h.photo ? <img src={h.photo} alt="" /> : <div className="tp-noimg">🛏</div>}
          <div className="tp-tb">
            <b>{h.name}</b>
            <span>{h.rating ? `★${h.rating}` : ""}{h.reviews_count ? ` (${h.reviews_count.toLocaleString()})` : ""}{h.price ? ` · ${h.price}` : ""}</span>
            {h.live ? <span className="tp-price live"><span className="tp-livebadge">● LIVE</span> {h.live.nightly}/night · {h.live.price} total</span>
              : <span className="tp-price">{h.est_nightly ? `${h.est_nightly} / night (est.)` : "price on request"}</span>}
            {h.live && <small className={h.live.refundable ? "tp-ok" : "tp-warn"}>{h.live.room ? `${h.live.room} · ` : ""}{h.live.policy}{h.live.due_at_hotel ? ` · ${h.live.due_at_hotel} due at hotel` : ""}</small>}
            <small>{h.area || h.short_address}</small>
            <div className="tp-use" onClick={stop}>
              {users(h.name).length ? <em>In {users(h.name).join(", ")}</em> : null}
              {h.live?.offer_id && <button className="tp-bookb" onClick={() => core.sendText(`Book hotel offer ${h.live.offer_id} (${h.name}, ${h.live.price} for the ${d.title} trip)`)}>Book…</button>}
              {(d.itineraries || []).filter((it: any) => it.hotel !== h.name).map((it: any) => (
                <button key={it.id} onClick={() => onRevise(`Switch itinerary ${it.id}'s hotel to ${h.name}`)}>Use for {it.id}</button>))}
            </div>
          </div>
        </div>
      ))}
    </div></>
  );
}

function Events({ d }: { d: any }) {
  if (!(d.events || []).length) return <div className="tp-none">No events found for those dates.</div>;
  return (
    <div className="tp-evs">
      {d.events.map((e: any, k: number) => (
        <div key={k} className="tp-ev"><div className="tp-evd">{String(e.date || "").split(" ")[0].slice(5) || "—"}</div>
          <div><b>{e.name}</b><span>{[e.time, e.venue, e.category, e.price].filter(Boolean).join(" · ")}</span><small>{e.why}</small></div></div>
      ))}
    </div>
  );
}

function Places({ list, icon }: { list: any[]; icon: string }) {
  return (
    <div className="tp-tiles sm">
      {(list || []).map((p: any) => (
        <div key={p.id || p.name} className="tp-tile">
          {p.photo ? <img src={p.photo} alt="" /> : <div className="tp-noimg">{icon}</div>}
          <div className="tp-tb"><b>{p.name}</b><span>{[p.type, p.rating ? `★${p.rating}` : "", p.price].filter(Boolean).join(" · ")}</span><small>{p.short_address}</small></div>
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- card */

export function TripCard({ card }: { card: Card }) {
  const mx = useContext(MaxCtx);
  const d = card.data || {};
  const { steps, err, busy, plan, revise } = useTripRun(card);
  const [tab, setTab] = useState<string>("it0");
  const ready = Array.isArray(d.itineraries) && d.itineraries.length > 0;
  useEffect(() => { if (ready && d.chosen != null && tab === "it0") setTab(`it${d.chosen}`); }, [ready]);
  const undo = async () => {
    const r = await core.workspace("trip_undo", { trip_id: d.id });
    if (r.ok) { core.patchCard(card.id, r.result, r.result.title); toast("Restored the previous version."); } else toast(r.error || "Nothing to undo", true);
  };
  const lastChange = useMemo(() => (d.history || []).slice(-1)[0]?.change, [d.history]);

  if (!d.id && !busy) return <Empty onPlan={plan} />;
  if (busy && !ready) return <Forge steps={steps} request={d.request} />;
  if (d.status === "error" || (!ready && err)) return (
    <div className="tp-empty" onClick={stop}><div className="ks-warn">Planning failed: {d.error || err}</div><Empty onPlan={plan} /></div>
  );
  if (!ready) return <Forge steps={steps} request={d.request} />;

  const header = (
    <div className="tp-head">
      <div className="tp-title">{d.title}</div>
      <div className="tp-dest">{d.brief?.destination} · from {d.brief?.origin}</div>
      <Meta d={d} />
    </div>
  );
  const reviseBar = (
    <div className="tp-rev">
      {busy ? <div className="tp-revbusy"><span className="cx-spin" /> {steps.slice(-1)[0] || "Working"}… <em>“{d.pending_change}”</em></div>
        : <Composer onSubmit={revise} placeholder="Tell JARVIS what to change: “use the other hotel”, “more by the water”, “no hiking”…" />}
      {d.revise_error && <div className="ks-warn">Last change failed: {d.revise_error}</div>}
      <div className="tp-hist">
        {lastChange && <span>Last change: {lastChange}</span>}
        {(d.version_count || 0) > 1 && !busy && <button onClick={(e) => { stop(e); undo(); }}>↶ Undo</button>}
      </div>
    </div>
  );

  if (!mx?.max) {
    return (
      <div className={`tp tp-side${busy ? " busy" : ""}`}>
        {header}
        <WeatherStrip w={d.weather} />
        <div className="tp-opts">
          {d.itineraries.map((it: any, i: number) => (
            <button key={i} className={`tp-optc${d.chosen === i ? " on" : ""}`} onClick={(e) => { stop(e); setTab(`it${i}`); mx?.setMax(true); }}>
              <span className="tp-badge">{it.id}</span>
              <div><b>{it.name}{d.chosen === i ? " ✓" : ""}</b><span>🛏 {it.hotel}</span><small>{it.est_total}</small></div>
            </button>
          ))}
        </div>
        {d.transport?.recommended && <div className="tp-line">✈ {d.transport.recommended}</div>}
        {d.transport?.live_flights?.offers?.length > 0 && <div className="tp-line"><span className="tp-livebadge">● LIVE</span> flights from <b>${Math.round(d.transport.live_flights.cheapest || d.transport.live_flights.offers[0].total).toLocaleString()}</b> round trip for {d.transport.live_flights.passengers}</div>}
        {(d.events || []).slice(0, 3).map((e: any, k: number) => <div key={k} className="tp-line">🎟 {e.name} <small>{String(e.date || "")}</small></div>)}
        {reviseBar}
        <button className="tp-open" onClick={(e) => { stop(e); mx?.setMax(true); }}>Open full planner ⤢</button>
      </div>
    );
  }

  const tabs: [string, string][] = [
    ...d.itineraries.map((it: any, i: number) => [`it${i}`, `${it.id} · ${it.name}`] as [string, string]),
    ["ov", "Overview"], ["hotels", `Hotels (${(d.hotels || []).length})`], ["events", `Events (${(d.events || []).length})`],
    ["sights", "Sights"], ["dining", "Dining"],
  ];
  return (
    <div className={`tp tp-xl${busy ? " busy" : ""}`}>
      {header}
      <div className="tp-tabs" onClick={stop}>
        {tabs.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}{k === `it${d.chosen}` ? " ✓" : ""}</button>)}
      </div>
      <div className="tp-main">
        {busy && <div className="tp-veil"><Forge steps={steps} change={d.pending_change} /></div>}
        {tab.startsWith("it") && d.itineraries[+tab.slice(2)] && <Itinerary d={d} i={+tab.slice(2)} cardId={card.id} />}
        {tab === "ov" && <Overview d={d} cardId={card.id} />}
        {tab === "hotels" && <Hotels d={d} onRevise={revise} />}
        {tab === "events" && <Events d={d} />}
        {tab === "sights" && <Places list={d.sights} icon="◆" />}
        {tab === "dining" && <Places list={[...(d.dining || []), ...(d.nightlife || [])]} icon="🍽" />}
      </div>
      {reviseBar}
    </div>
  );
}
