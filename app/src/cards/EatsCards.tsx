/** UBER EATS: stores near him, a store's FULL menu (every section + item, photos, prices, Add), and the cart.
 *  Taps go straight to Core (`core.direct("eats_*")`): browsing and editing the cart only. Placing an order is
 *  voice/model-driven and always ends in a confirm card with the full total. */
import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import type { Card } from "../types";
import { core } from "../ws/core";

const usd = (v?: number | null) => (v == null ? "" : `$${v.toFixed(2)}`);
const go = (op: string, args: Record<string, unknown>) => core.direct(`eats_${op}`, args);

export function EatsStoresCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  const stores: any[] = d.stores ?? [];
  return (
    <div className="ue">
      {d.deliver_to && <div className="ue-to">Delivering to <b>{d.deliver_to}</b></div>}
      <div className="ue-grid">
        {stores.map((s, i) => (
          <motion.button key={s.id} className={`ue-store ${s.accepting ? "" : "closed"}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(i * 0.03, 0.4) }} onClick={(e) => { e.stopPropagation(); go("menu", { store: s.id }); }}>
            <div className="ue-img">{s.image ? <img src={s.image} alt="" loading="lazy" /> : null}
              {!s.accepting && <span className="ue-shut">NOT ACCEPTING ORDERS</span>}
              {s.promos?.[0] && <span className="ue-promo">{s.promos[0]}</span>}
            </div>
            <div className="ue-row"><b>{s.name}</b>{s.rating ? <span className="ue-rate">★ {s.rating.toFixed(1)}</span> : null}</div>
            <div className="ue-sub">{s.eta_min ? `${s.eta_min}–${s.eta_max} min` : s.meta?.join(" · ")}{s.reviews ? ` · ${s.reviews} ratings` : ""}</div>
          </motion.button>
        ))}
      </div>
    </div>
  );
}

export function EatsMenuCard({ card, expanded = false }: { card: Card; expanded?: boolean }) {
  const d = card.data ?? {};
  const sections: any[] = d.sections ?? [];
  const [q, setQ] = useState("");
  const [sec, setSec] = useState(0);
  const body = useRef<HTMLDivElement>(null);
  const refs = useRef<(HTMLDivElement | null)[]>([]);
  const shown = useMemo(() => {
    const w = q.trim().toLowerCase();
    return sections.map((s) => ({ ...s, items: w ? s.items.filter((i: any) => `${i.name} ${i.description ?? ""}`.toLowerCase().includes(w)) : s.items }))
      .filter((s) => s.items.length);
  }, [sections, q]);
  useEffect(() => setSec(0), [d.id]);
  const jump = (k: number) => { setSec(k); refs.current[k]?.scrollIntoView({ behavior: "smooth", block: "start" }); };
  return (
    <div className={`ue ue-menu ${expanded ? "xl" : ""}`}>
      <div className="ue-hero">
        {d.image && <img src={d.image} alt="" />}
        <div className="ue-hero-txt">
          <div className="ue-name">{d.name}</div>
          <div className="ue-sub">
            {[d.rating && `★ ${d.rating} (${d.reviews})`, d.eta, d.distance, d.price_bucket, (d.cuisines ?? []).slice(0, 3).join(", ")].filter(Boolean).join(" · ")}
          </div>
          <div className="ue-sub">{d.open ? <span className="ue-open">OPEN</span> : <span className="ue-closed">{d.closed_message || "CLOSED"}</span>} {(d.menus ?? []).map((m: any) => `${m.title} ${m.hours ?? ""}`).join(" · ")}</div>
        </div>
        <div className="ue-count"><b>{d.item_count}</b><span>items</span></div>
      </div>
      <div className="ue-tools" onClick={(e) => e.stopPropagation()}>
        <input placeholder={`Search ${d.name ?? "menu"}…`} value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="ue-tabs">
          {shown.map((s, k) => <button key={s.title + k} className={k === sec ? "on" : ""} onClick={() => jump(k)}>{s.title}<em>{s.items.length}</em></button>)}
        </div>
      </div>
      <div className="ue-split">
        <div className="ue-sections" ref={body}>
          {shown.map((s, k) => (
            <div key={s.title + k} ref={(el) => { refs.current[k] = el; }} className="ue-sec">
              <h4>{s.title}</h4>
              <div className="ue-items">
                {s.items.map((it: any) => (
                  <div key={it.id} className={`ue-item ${it.sold_out ? "out" : ""} ${it.image ? "" : "noimg"}`}>
                    <div className="ue-item-txt">
                      <b>{it.name}</b>
                      {it.description && <p>{it.description}</p>}
                      <span className="ue-price">{usd(it.price)}{it.options ? <em> · options</em> : null}{it.sold_out ? <em> · sold out</em> : null}</span>
                    </div>
                    <div className="ue-item-img">
                      {it.image ? <img src={it.image} alt="" loading="lazy" /> : null}
                      {!it.sold_out && <button className="ue-add" title={it.options ? "Add (defaults for required choices; or tell JARVIS your options)" : "Add to cart"}
                        onClick={(e) => { e.stopPropagation(); go("cart_add", { store: d.id, item: it.id }); }}>+</button>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {!shown.length && <div className="muted">Nothing on the menu matches “{q}”.</div>}
        </div>
        {d.cart && <CartRail cart={d.cart} />}
      </div>
    </div>
  );
}

function CartRail({ cart }: { cart: any }) {
  const items: any[] = cart.items ?? [];
  const tip = cart.subtotal * (cart.tip_pct ?? 15) / 100;
  return (
    <div className="ue-cart">
      <h4>CART {cart.store_name ? <span>· {cart.store_name}</span> : null}</h4>
      {!items.length && <div className="muted small">Empty. Tap + or tell JARVIS what you want.</div>}
      {items.map((i) => (
        <motion.div key={i.line_id} className="ue-line" initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }}>
          <div><b>{i.qty}×</b> {i.name}{i.options?.length ? <div className="muted small">{i.options.map((o: any) => /^(none|no)$/i.test(o.name) ? `${String(o.group || "").replace(/^choose (your )?/i, "")}: ${o.name}` : o.name).join(", ")}</div> : null}</div>
          <span>{usd(i.unit_price * i.qty)}</span>
          <button title="Remove" onClick={(e) => { e.stopPropagation(); go("cart_remove", { item: i.line_id }); }}>×</button>
        </motion.div>
      ))}
      {items.length > 0 && (
        <div className="ue-tot">
          <div><span>Subtotal</span><b>{usd(cart.subtotal)}</b></div>
          <div className="muted"><span>Tip ({cart.tip_pct}%) est.</span><span>{usd(tip)}</span></div>
          <div className="muted small">Delivery fee, service fee and tax are shown on the confirm card before anything is ordered.</div>
          <div className="ue-say">Say “order it” to check out</div>
        </div>
      )}
    </div>
  );
}

export function EatsCartCard({ card }: { card: Card }) {
  return <div className="ue"><CartRail cart={card.data ?? {}} /></div>;
}
