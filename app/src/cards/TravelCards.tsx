/** Travel & dining displays: flight offers, hotels, rental cars, restaurant tables, and the booked confirmation.
 *  "Book" buttons only ask Core to PREPARE a booking (re-priced confirm card); nothing is bought until he authorizes. */
import { useState } from "react";
import { motion } from "framer-motion";
import type { Card } from "../types";
import { core } from "../ws/core";
import { useStore } from "../state/store";
import { SearchBar } from "./SearchBar";

const t12 = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso.length <= 19 ? iso : iso);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
};
const dayLabel = (iso?: string) => (iso ? new Date(iso.length === 10 ? iso + "T12:00:00" : iso).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }) : "");
const book = (op: string, args: Record<string, unknown>) => core.direct(op, args);
const TestBadge = ({ on }: { on?: boolean }) => (on ? <span className="tv-test" title="Duffel test inventory: not a real ticket">TEST MODE</span> : null);

function Stop({ n }: { n: number }) {
  return <span className={`tv-stops ${n ? "" : "ns"}`}>{n ? `${n} stop${n > 1 ? "s" : ""}` : "Nonstop"}</span>;
}

function FlightSearch({ card }: { card: Card }) {
  const d = card.data ?? {};
  const [f, setF] = useState({ origin: (d.origins || []).join(",") || "", destination: d.destination || "", depart: d.depart_date || "", ret: d.return_date || "", adults: d.adults || 1 });
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!f.destination || !f.depart) { useStore.getState().toast({ text: "Add a destination and a departure date.", error: true }); return; }
    setBusy(true);
    const r = await core.rpc("flights_find", { destination: f.destination, depart_date: f.depart, return_date: f.ret, origin: f.origin, adults: f.adults });
    setBusy(false);
    if (r.ok) core.patchCard(card.id, r.result, `Flights · ${f.destination} · ${f.depart}`);
    else useStore.getState().toast({ text: r.error || "Flight search failed", error: true });
  };
  return (
    <form className="cs-bar cs-wrap" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); run(); }}>
      <input className="cs-in grow" placeholder="From (blank = NYC airports)" value={f.origin} onChange={(e) => setF({ ...f, origin: e.target.value })} />
      <input className="cs-in grow" placeholder="To (city or airport)" value={f.destination} onChange={(e) => setF({ ...f, destination: e.target.value })} />
      <input className="cs-in" type="date" title="Depart" value={f.depart} onChange={(e) => setF({ ...f, depart: e.target.value })} />
      <input className="cs-in" type="date" title="Return (optional)" value={f.ret} onChange={(e) => setF({ ...f, ret: e.target.value })} />
      <select className="cs-in" value={f.adults} onChange={(e) => setF({ ...f, adults: Number(e.target.value) })}>
        {[...Array(9)].map((_, i) => <option key={i} value={i + 1}>{i + 1} adult{i ? "s" : ""}</option>)}
      </select>
      <button type="submit" className="cs-go" disabled={busy}>{busy ? <span className="cx-spin" /> : "Search flights"}</button>
    </form>
  );
}

export function FlightsCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  const offers: any[] = d.offers ?? [];
  if (!offers.length) return <div className="tv"><FlightSearch card={card} />{!d.form && <div className="muted">No flights found for those dates.</div>}</div>;
  return (
    <div className="tv">
      <FlightSearch card={card} />
      <div className="tv-head">
        <span>{dayLabel(d.depart_date)}{d.return_date ? ` → ${dayLabel(d.return_date)}` : " · one way"}</span>
        <span>{d.adults} adult{d.adults > 1 ? "s" : ""} · {String(d.cabin).replace("_", " ")}</span>
        <span className="tv-spacer" />
        <TestBadge on={d.test_mode} />
      </div>
      {offers.map((o, i) => (
        <motion.div key={o.id} className={`tv-row fl ${i === 0 ? "best" : ""}`} initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.04 * i }}>
          <div className="tv-logo">{o.logo ? <img src={o.logo} alt="" /> : <span>✈</span>}</div>
          <div className="tv-legs">
            {o.slices.map((s: any, k: number) => (
              <div className="tv-leg" key={k}>
                <b>{t12(s.depart)}</b>
                <span className="tv-ap">{s.origin}</span>
                <span className="tv-line"><i /><em>{s.duration}</em></span>
                <span className="tv-ap">{s.destination}</span>
                <b>{t12(s.arrive)}</b>
                <Stop n={s.stops} />
              </div>
            ))}
            <div className="tv-sub">{o.airline} · {o.slices.map((s: any) => s.flights.join(" ")).join(" / ")}{o.bags ? ` · ${o.bags}` : ""}{o.refundable ? " · refundable" : ""}</div>
          </div>
          <div className="tv-price">
            <b>{o.price}</b>
            {i === 0 && <em>BEST</em>}
            <button className="tv-book" onClick={(e) => { e.stopPropagation(); book("flight_book", { offer_id: o.id }); }}>Book</button>
          </div>
        </motion.div>
      ))}
      <div className="muted small">{d.count} offers from {(d.origins || []).join(" · ")} · LaGuardia preferred · Book shows a final price to confirm</div>
    </div>
  );
}

export function HotelsCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  const hs: any[] = d.hotels ?? [];
  if (!hs.length) return <div className="muted">No hotels available there for those dates.</div>;
  return (
    <div className="tv">
      <div className="tv-head"><span>{dayLabel(d.check_in)} → {dayLabel(d.check_out)} · {d.nights} night{d.nights > 1 ? "s" : ""}</span><span>{d.guests} guest{d.guests > 1 ? "s" : ""}</span><span className="tv-spacer" /><TestBadge on={d.test_mode} /></div>
      <div className="tv-grid">
        {hs.map((h, i) => (
          <motion.div key={h.id} className="tv-tile" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 * i }}>
            <div className="tv-ph">{h.photo ? <img src={h.photo} alt="" loading="lazy" /> : <span>🏨</span>}{h.review_score && <i>{h.review_score}</i>}</div>
            <div className="tv-tt">{h.name}</div>
            <div className="tv-sub">{"★".repeat(Math.round(h.stars || 0))} {h.distance_km != null ? `· ${h.distance_km} km` : ""}</div>
            <div className="tv-foot"><span><b>{h.nightly}</b>/night · {h.price} total</span>
              <button className="tv-book" onClick={(e) => { e.stopPropagation(); book("hotel_book", { search_result_id: h.id }); }}>Book</button></div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

export function CarRentalsCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  const cs: any[] = d.cars ?? [];
  if (!cs.length) return <div className="muted">No rental cars available.</div>;
  return (
    <div className="tv">
      <div className="tv-head"><span>{dayLabel(d.pickup_date)} → {dayLabel(d.dropoff_date)} · {d.days} day{d.days > 1 ? "s" : ""}</span><span className="tv-spacer" /><TestBadge on={d.test_mode} /></div>
      {cs.map((c, i) => (
        <motion.div key={c.id} className="tv-row car" initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.04 * i }}>
          <div className="tv-carph">{c.photo ? <img src={c.photo} alt="" /> : <span>🚗</span>}</div>
          <div className="tv-legs">
            <div className="tv-tt">{c.name} <span className="muted small">or similar · {c.category}</span></div>
            <div className="tv-sub">{c.supplier} · {c.seats} seats · {c.transmission}{c.pickup ? ` · ${c.pickup}` : ""}</div>
          </div>
          <div className="tv-price"><b>{c.price}</b>
            <button className="tv-book" onClick={(e) => { e.stopPropagation(); book("car_rental_book", { rate_id: c.id }); }}>Book</button></div>
        </motion.div>
      ))}
    </div>
  );
}

export function RestaurantsCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  const rs: any[] = d.restaurants ?? [];
  const [date, setDate] = useState(d.date || "");
  const [from, setFrom] = useState(d.time_from || "");
  const [to, setTo] = useState(d.time_to || "");
  const [party, setParty] = useState(Number(d.party_size) || 2);
  const [busy, setBusy] = useState(false);
  const search = async (query: string) => {
    setBusy(true);
    const r = await core.rpc("resy_find", { query, date, time_from: from, time_to: to, party_size: party });
    setBusy(false);
    if (r.ok) core.patchCard(card.id, r.result, `Resy · ${query || "near you"}`);
    else useStore.getState().toast({ text: r.error || "Resy search failed", error: true });
  };
  const anytime = (!d.time_from || d.time_from === "00:00") && (!d.time_to || d.time_to === "23:59");
  const when = d.time_from || d.time_to ? (anytime ? "any time" : `${d.time_from || "…"}–${d.time_to || "…"}`) : `around ${d.time}`;
  return (
    <div className="tv">
      <SearchBar value={d.query} placeholder="Search restaurants or cuisines" busy={busy} onSearch={search}>
        <input className="cs-in" type="date" value={date} onChange={(e) => setDate(e.target.value)} title="Date" />
        <input className="cs-in" type="time" value={from === "00:00" ? "" : from} onChange={(e) => setFrom(e.target.value)} title="From" />
        <span className="muted small">to</span>
        <input className="cs-in" type="time" value={to === "23:59" ? "" : to} onChange={(e) => setTo(e.target.value)} title="To" />
        <select className="cs-in" value={party} onChange={(e) => setParty(Number(e.target.value))} title="People">
          {[...Array(12)].map((_, i) => <option key={i} value={i + 1}>{i + 1} {i ? "people" : "person"}</option>)}
        </select>
      </SearchBar>
      {!rs.length && <div className="muted">No open tables for that date, time and party size.</div>}
      <div className="tv-head"><span>{dayLabel(d.date)} · {when}</span><span>party of {d.party_size}</span><span className="tv-spacer" /><span className="tv-resy">resy</span></div>
      {rs.map((r, i) => (
        <motion.div key={r.venue_id} className="tv-row rs" initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.04 * i }}>
          <div className="tv-rsph" onClick={(e) => { e.stopPropagation(); window.jarvis?.open(r.url); }}>{r.photo ? <img src={r.photo} alt="" loading="lazy" /> : <span>🍽</span>}</div>
          <div className="tv-legs">
            <div className="tv-tt">{r.name}</div>
            <div className="tv-sub">{r.cuisine} · {r.price} · {r.neighborhood}{r.rating ? ` · ★ ${r.rating}` : ""}{r.distance_km != null ? ` · ${r.distance_km} km` : ""}</div>
            <div className="tv-slots">
              {r.slots.slice(0, 6).map((s: any) => (
                <button key={s.time + s.type} className="tv-slot" title={`${s.type} · reserve`}
                  onClick={(e) => { e.stopPropagation(); book("restaurant_book", { venue_id: r.venue_id, date: d.date, time: s.time, party_size: d.party_size, seating: s.type }); }}>
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        </motion.div>
      ))}
      <div className="muted small">Tap a time to reserve · books instantly when there are no fees, otherwise you confirm first</div>
    </div>
  );
}

export function ReservationsCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  const items: any[] = d.items ?? [];
  const icon = (p: string) => (p === "resy" ? "🍽" : p === "duffel_flight" ? "✈" : p === "duffel_hotel" ? "🏨" : "🚗");
  if (!items.length) return <div className="muted">No upcoming reservations.{d.errors?.length ? ` (${d.errors[0]})` : ""}</div>;
  return (
    <div className="tv">
      {items.map((r, i) => (
        <motion.div key={r.provider + r.id} className="tv-row rs" initial={{ opacity: 0, x: 14 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.04 * i }}>
          <div className="tv-rsph">{r.photo ? <img src={r.photo} alt="" loading="lazy" /> : <span>{icon(r.provider)}</span>}</div>
          <div className="tv-legs">
            <div className="tv-tt">{r.name} {r.test_mode ? <TestBadge on /> : null}</div>
            <div className="tv-sub">{dayLabel(r.day)} · {r.time_label}{r.party_size ? ` · party of ${r.party_size}` : ""}{r.neighborhood ? ` · ${r.neighborhood}` : ""}</div>
            <div className="tv-sub">{r.reference ? `Ref ${r.reference}` : ""}{r.cancel_fee ? ` · late-cancel fee ${r.cancel_fee}` : ""}</div>
            {r.cancel_allowed ? (
              <div className="tv-slots"><button className="tv-cancel" onClick={(e) => { e.stopPropagation(); book("reservation_cancel", { reservation_id: r.id, provider: r.provider }); }}>Cancel…</button></div>
            ) : <div className="tv-sub">Can't be cancelled online</div>}
          </div>
        </motion.div>
      ))}
      <div className="muted small">Free Resy cancellations happen right away · anything with a fee (and all travel) asks you first</div>
    </div>
  );
}

export function BookingConfirmedCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  if (d.category === "cancel") {
    return (
      <div className="tv-done">
        <motion.div className="tv-check cancel" initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}>✕</motion.div>
        <div className="tv-done-main">
          <div className="tv-done-h cancel">CANCELLED</div>
          <div className="tv-tt">{d.name}</div>
          {d.refund && <div className="tv-sub">Refund {d.refund}</div>}
          <div className="tv-ref">Ref <b>{d.reference}</b></div>
        </div>
      </div>
    );
  }
  const icon = { flight: "✈", hotel: "🏨", car: "🚗", restaurant: "🍽" }[d.category as string] ?? "✓";
  return (
    <div className="tv-done">
      <motion.div className="tv-check" initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ type: "spring", stiffness: 260, damping: 16 }}>{icon}</motion.div>
      <div className="tv-done-main">
        <div className="tv-done-h">CONFIRMED <TestBadge on={d.test_mode} /></div>
        <div className="tv-tt">{d.name || d.car || (d.slices?.[0] ? `${d.slices[0].origin} → ${d.slices[0].destination}` : "Booking")}</div>
        {d.slices?.map((s: any, k: number) => <div key={k} className="tv-sub">{dayLabel(s.depart)} · {t12(s.depart)} → {t12(s.arrive)} · {s.flights.join(" ")}</div>)}
        {d.check_in && <div className="tv-sub">{dayLabel(d.check_in)} → {dayLabel(d.check_out)}</div>}
        {d.when && <div className="tv-sub">{d.when} · party of {d.party_size}</div>}
        {d.pickup && <div className="tv-sub">Pick up {d.pickup}</div>}
        <div className="tv-ref">Ref <b>{d.reference}</b>{d.total ? ` · ${d.total}` : ""}</div>
      </div>
    </div>
  );
}

/** The booking section inside the AUTHORIZATION REQUIRED card. */
export function BookingPreview({ p }: { p: any }) {
  return (
    <div className="tv-pre">
      <div className="tv-pre-top">
        {p.photo ? <img className="tv-pre-ph" src={p.photo} alt="" /> : p.logo ? <img className="tv-pre-logo" src={p.logo} alt="" /> : null}
        <div><div className="tv-tt">{p.title}</div><TestBadge on={p.test_mode} /></div>
      </div>
      {(p.lines || []).map(([k, v]: [string, string], i: number) => <div className="tv-kv" key={i}><span>{k}</span><b>{v}</b></div>)}
      <div className="tv-total"><span>{p.total_label || "Total"}</span><b>{p.total}</b></div>
      {p.danger && <div className="tv-policy" style={{ color: "#ff9aa5" }}>{p.danger}</div>}
      <div className="tv-policy">{p.policy}</div>
    </div>
  );
}
