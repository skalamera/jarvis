/** TRADING DESK (Kraken) + INVESTMENT INTELLIGENCE.
 *  Desk: equity header, allocation, positions blotter, live chart, order book, order ticket, open orders, fills, alerts.
 *  The ticket only PROPOSES (core.direct("trade_order")) -> a confirm card he authorizes by voice or click. */
import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import type { Card } from "../types";
import { core } from "../ws/core";
import { DOWN, PriceChart, UP, ago, fmtPct, fmtPrice, sign, tone, type Pt } from "./fx";

const usd = (v?: number | null, d = 2) => (v == null ? "—" : `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`);
const qtyFmt = (v?: number | null) => (v == null ? "—" : v >= 100 ? v.toFixed(2) : v >= 1 ? v.toFixed(4) : v.toPrecision(4));
const RANGES = ["1D", "5D", "1M", "6M", "1Y", "ALL"];
const RATING_TONE: Record<string, string> = { BUY: "buy", ADD: "buy", HOLD: "hold", TRIM: "trim", SELL: "sell" };

export function TradeDeskCard({ card, expanded = false }: { card: Card; expanded?: boolean }) {
  const d = card.data ?? {};
  const pos: any[] = d.positions ?? [];
  const [sel, setSel] = useState<string>(pos[0]?.symbol ?? "BTC");
  const [tab, setTab] = useState<"positions" | "orders" | "fills" | "alerts">("positions");
  const insights = useMemo(() => Object.fromEntries((d.insights?.holdings ?? []).map((h: any) => [h.symbol, h])), [d.insights]);
  const cur = pos.find((p) => p.symbol === sel) ?? { symbol: sel, kind: "crypto" };
  return (
    <div className={`td ${expanded ? "xl" : ""}`}>
      <Header d={d} />
      <div className="td-grid">
        <div className="td-main">
          <ChartPanel sym={cur.symbol} kind={cur.kind} pos={cur} ins={insights[cur.symbol]} />
          <div className="td-tabs" onClick={(e) => e.stopPropagation()}>
            {(["positions", "orders", "fills", "alerts"] as const).map((t) => (
              <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
                {t}{t === "orders" && d.orders?.length ? <em>{d.orders.length}</em> : null}{t === "alerts" && d.alerts?.filter((a: any) => a.active).length ? <em>{d.alerts.filter((a: any) => a.active).length}</em> : null}
              </button>
            ))}
            <button className="td-refresh" title="Refresh" onClick={() => core.direct("trade_portfolio", {})}>⟳ {ago(d.updated)}</button>
          </div>
          {tab === "positions" && <Positions pos={pos} sel={sel} onSel={setSel} insights={insights} />}
          {tab === "orders" && <Orders orders={d.orders ?? []} />}
          {tab === "fills" && <Fills />}
          {tab === "alerts" && <Alerts alerts={d.alerts ?? []} sym={sel} />}
        </div>
        <div className="td-side">
          <Ticket pos={cur} cash={d.cash} holdings={pos} onSym={setSel} />
          {cur.kind === "crypto" && <Book sym={cur.symbol} />}
        </div>
      </div>
    </div>
  );
}

function Header({ d }: { d: any }) {
  const a = d.allocation ?? {};
  const tot = (a.crypto || 0) + (a.stocks || 0) + (a.cash || 0) || 1;
  return (
    <div className="td-head">
      <div className="td-eq">
        <span className="td-lbl">ACCOUNT VALUE</span>
        <b>{usd(d.total)}</b>
        <span className={`td-chg ${tone(d.day_change)}`}>{sign(d.day_change)}{usd(Math.abs(d.day_change ?? 0))} ({fmtPct(d.day_change_pct)}) today</span>
      </div>
      <div className="td-kpis">
        <div><span>Unrealized P&L</span><b className={tone(d.unrealized)}>{usd(d.unrealized)} <em>{fmtPct(d.unrealized_pct)}</em></b></div>
        <div><span>Cash</span><b>{usd(d.cash)}</b></div>
        <div title={d.realized_note || ""}><span>Realized (all-time){d.realized_note ? " *" : ""}</span><b className={tone(d.realized_all_time)}>{usd(d.realized_all_time, 0)}</b></div>
        <div><span>Dividends</span><b>{usd(d.dividends, 0)}</b></div>
        <div><span>Crypto exposure</span><b className={(a.crypto_exposure_pct ?? 0) > 40 ? "warn" : ""}>{fmtPct(a.crypto_exposure_pct, 1).replace("+", "")}</b></div>
      </div>
      <div className="td-alloc" title="Crypto · Stocks · Cash">
        <i className="c" style={{ width: `${(a.crypto || 0) / tot * 100}%` }} />
        <i className="s" style={{ width: `${(a.stocks || 0) / tot * 100}%` }} />
        <i className="k" style={{ width: `${(a.cash || 0) / tot * 100}%` }} />
      </div>
      <div className="td-legend"><span><i className="c" />Crypto {usd(a.crypto, 0)}</span><span><i className="s" />Stocks {usd(a.stocks, 0)}</span><span><i className="k" />Cash {usd(a.cash, 0)}</span></div>
    </div>
  );
}

function Positions({ pos, sel, onSel, insights }: { pos: any[]; sel: string; onSel: (s: string) => void; insights: Record<string, any> }) {
  return (
    <div className="td-table">
      <div className="td-tr td-th"><span>Asset</span><span>Qty</span><span>Price</span><span>Day</span><span>Value</span><span>Avg cost</span><span>P&L</span><span>Weight</span><span>Read</span></div>
      {pos.map((p, i) => {
        const r = insights[p.symbol]?.rating;
        return (
          <motion.button key={p.symbol} className={`td-tr ${sel === p.symbol ? "on" : ""}`} initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.03 }}
            onClick={(e) => { e.stopPropagation(); onSel(p.symbol); }}>
            <span className="td-asset"><b>{p.symbol}</b><em>{p.kind === "stock" ? (p.crypto_proxy ? "stock · crypto-linked" : "stock") : p.name}</em></span>
            <span>{qtyFmt(p.qty)}</span>
            <span>{fmtPrice(p.price)}</span>
            <span className={tone(p.day_change_pct)}>{fmtPct(p.day_change_pct)}</span>
            <span><b>{usd(p.value)}</b></span>
            <span title={p.basis_note || ""}>{p.avg_cost ? fmtPrice(p.avg_cost) : <em className="muted">{p.basis_note ? "n/a" : "—"}</em>}</span>
            <span className={tone(p.unrealized)}>{p.unrealized != null ? <>{usd(p.unrealized)}<em> {fmtPct(p.unrealized_pct)}</em></> : "—"}</span>
            <span><i className="td-wbar"><i style={{ width: `${Math.min(p.weight ?? 0, 100)}%` }} /></i>{(p.weight ?? 0).toFixed(1)}%</span>
            <span>{r ? <em className={`td-rate ${RATING_TONE[r] ?? ""}`}>{r}</em> : <em className="muted">—</em>}</span>
          </motion.button>
        );
      })}
    </div>
  );
}

function ChartPanel({ sym, kind, pos, ins }: { sym: string; kind: string; pos: any; ins?: any }) {
  const [rng, setRng] = useState("1M");
  const [data, setData] = useState<{ points: Pt[]; base?: number; intraday?: boolean; period_change_pct?: number } | null>(null);
  const [hover, setHover] = useState<{ c: number; pct: number | null } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let live = true;
    setErr("");
    core.rpc("trade_chart", { symbol: sym, kind, range: rng }).then((r) => {
      if (!live) return;
      if (r.ok) setData(r.result); else setErr(r.error || "No chart");
    });
    return () => { live = false; };
  }, [sym, kind, rng]);
  const px = hover?.c ?? pos?.price;
  return (
    <div className="td-chart">
      <div className="td-chart-h">
        <div><b>{sym}</b><span className="muted"> {kind === "stock" ? "Stock" : "Crypto"} · USD</span></div>
        <div className="td-px"><b>{fmtPrice(px)}</b><span className={tone(hover?.pct ?? data?.period_change_pct)}>{fmtPct(hover?.pct ?? data?.period_change_pct)} {hover ? "" : rng}</span></div>
        <div className="td-rng" onClick={(e) => e.stopPropagation()}>{RANGES.map((r) => <button key={r} className={rng === r ? "on" : ""} onClick={() => setRng(r)}>{r}</button>)}</div>
      </div>
      {data?.points?.length ? <PriceChart points={data.points} base={data.base} intraday={data.intraday} height={210} onHover={(h) => setHover(h)} />
        : <div className="td-empty">{err || "Loading chart…"}</div>}
      {ins && (
        <div className="td-ins-strip">
          <em className={`td-rate ${RATING_TONE[ins.rating] ?? ""}`}>{ins.rating} · {ins.confidence}%</em>
          <span>{ins.thesis}</span>
          {ins.levels && <span className="td-lv">{ins.levels.support ? `S ${fmtPrice(ins.levels.support)}` : ""}{ins.levels.resistance ? ` · R ${fmtPrice(ins.levels.resistance)}` : ""}{ins.levels.stop_idea ? ` · stop ${fmtPrice(ins.levels.stop_idea)}` : ""}</span>}
        </div>
      )}
    </div>
  );
}

function Ticket({ pos, cash, holdings, onSym }: { pos: any; cash?: number; holdings: any[]; onSym: (s: string) => void }) {
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [type, setType] = useState("market");
  const [mode, setMode] = useState<"usd" | "qty">("usd");
  const [amt, setAmt] = useState("");
  const [limit, setLimit] = useState("");
  const [stop, setStop] = useState("");
  const [sym, setSym] = useState(pos.symbol);
  const [pv, setPv] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { setSym(pos.symbol); setPv(null); setMsg(""); }, [pos.symbol]);
  const args = () => ({
    symbol: sym, side, order_type: type,
    amount_usd: mode === "usd" ? Number(amt) || 0 : 0, quantity: mode === "qty" ? Number(amt) || 0 : 0,
    limit_price: Number(limit) || 0, stop_price: Number(stop) || 0,
  });
  useEffect(() => {
    if (!(Number(amt) > 0)) { setPv(null); return; }
    const t = window.setTimeout(async () => {
      const r = await core.rpc("trade_preview", { ...args(), check: false });
      setPv(r.ok ? r.result : { error: r.error });
    }, 350);
    return () => window.clearTimeout(t);
  }, [sym, side, type, mode, amt, limit, stop]);
  useEffect(() => {
    const h = (e: Event) => {
      const m = (e as CustomEvent).detail;
      if (m?.op !== "trade_order") return;
      setBusy(false);
      setMsg(m.ok ? "Review the authorization card: say “confirm” or click Authorize." : (m.error || "").replace(/^\w+Error: /, ""));
    };
    window.addEventListener("jarvis:direct_result", h);
    return () => window.removeEventListener("jarvis:direct_result", h);
  }, []);
  const held = holdings.find((h) => h.symbol === sym);
  const needLimit = type === "limit" || type === "stop-limit";
  const needStop = type === "stop-loss" || type === "take-profit" || type === "stop-limit";
  return (
    <div className="td-ticket" onClick={(e) => e.stopPropagation()}>
      <div className="td-side-tabs">
        <button className={`b ${side === "buy" ? "on" : ""}`} onClick={() => setSide("buy")}>Buy</button>
        <button className={`s ${side === "sell" ? "on" : ""}`} onClick={() => setSide("sell")}>Sell</button>
      </div>
      <label>Asset<input value={sym} onChange={(e) => setSym(e.target.value.toUpperCase())} onBlur={() => holdings.some((h) => h.symbol === sym) && onSym(sym)} /></label>
      <label>Order type
        <select value={type} onChange={(e) => setType(e.target.value)}>
          <option value="market">Market</option><option value="limit">Limit</option><option value="stop-loss">Stop loss</option>
          <option value="take-profit">Take profit</option><option value="stop-limit">Stop limit</option>
        </select>
      </label>
      <div className="td-amt">
        <label>{mode === "usd" ? "Amount (USD)" : `Quantity (${sym})`}<input inputMode="decimal" value={amt} onChange={(e) => setAmt(e.target.value.replace(/[^0-9.]/g, ""))} placeholder="0.00" /></label>
        <button onClick={() => setMode(mode === "usd" ? "qty" : "usd")} title="Switch dollars / quantity">⇄</button>
      </div>
      <div className="td-quick">
        {side === "buy" ? [25, 50, 100, 250].map((v) => <button key={v} onClick={() => { setMode("usd"); setAmt(String(v)); }}>${v}</button>)
          : [25, 50, 100].map((p) => <button key={p} disabled={!held} onClick={() => { setMode("qty"); setAmt(String(((held?.qty ?? 0) * p) / 100)); }}>{p}%</button>)}
      </div>
      {needStop && <label>{type === "take-profit" ? "Trigger price" : "Stop price"}<input inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value.replace(/[^0-9.]/g, ""))} /></label>}
      {needLimit && <label>Limit price<input inputMode="decimal" value={limit} onChange={(e) => setLimit(e.target.value.replace(/[^0-9.]/g, ""))} placeholder={pos.price ? String(pos.price) : ""} /></label>}
      <div className="td-pv">
        <div><span>Available</span><b>{side === "buy" ? usd(cash) : held ? `${qtyFmt(held.qty)} ${sym}` : `0 ${sym}`}</b></div>
        {pv && !pv.error && <>
          <div><span>Quantity</span><b>{qtyFmt(pv.quantity)} {pv.symbol}</b></div>
          <div><span>Price ({pv.side === "buy" ? "ask" : "bid"})</span><b>{fmtPrice(pv.ref_price)}</b></div>
          <div><span>Fee ({pv.fee_rate_pct}%)</span><b>{usd(pv.est_fee)}</b></div>
          <div className="tot"><span>{pv.side === "buy" ? "Total cost" : "You receive"}</span><b>{usd(pv.est_total)}</b></div>
          {pv.warnings?.map((w: string, i: number) => <div key={i} className="td-warn">{w}</div>)}
          {pv.kind === "stock" && <div className="td-warn">Stock orders: Kraken's API currently rejects them for your account; JARVIS will check with Kraken and tell you.</div>}
        </>}
        {pv?.error && <div className="td-warn">{String(pv.error).replace(/^\w+Error: /, "")}</div>}
      </div>
      <button className={`td-go ${side}`} disabled={busy || !(Number(amt) > 0) || (needLimit && !(Number(limit) > 0)) || (needStop && !(Number(stop) > 0))}
        onClick={() => { setBusy(true); setMsg(""); core.direct("trade_order", { ...args(), reason: "ticket" }); }}>
        {busy ? "Checking with Kraken…" : `Review ${side} order`}
      </button>
      {msg && <div className="td-msg">{msg}</div>}
      <div className="muted small">Nothing is placed until you authorize it.</div>
    </div>
  );
}

function Book({ sym }: { sym: string }) {
  const [b, setB] = useState<any>(null);
  useEffect(() => {
    let live = true;
    const load = () => core.rpc("trade_book", { symbol: sym, depth: 10 }).then((r) => live && r.ok && setB(r.result));
    load();
    const t = window.setInterval(load, 5000);
    return () => { live = false; window.clearInterval(t); };
  }, [sym]);
  if (!b) return null;
  const max = Math.max(...b.asks.map((a: number[]) => a[1]), ...b.bids.map((x: number[]) => x[1]), 1e-9);
  const spread = b.asks[0] && b.bids[0] ? b.asks[0][0] - b.bids[0][0] : null;
  return (
    <div className="td-book">
      <h4>ORDER BOOK <span>{sym}/USD · live</span></h4>
      {[...b.asks].slice(0, 8).reverse().map((a: number[], i: number) => <div key={`a${i}`} className="ask"><i style={{ width: `${a[1] / max * 100}%` }} /><span>{fmtPrice(a[0])}</span><span>{qtyFmt(a[1])}</span></div>)}
      <div className="td-spread">spread {spread != null ? fmtPrice(spread) : "—"}</div>
      {b.bids.slice(0, 8).map((x: number[], i: number) => <div key={`b${i}`} className="bid"><i style={{ width: `${x[1] / max * 100}%` }} /><span>{fmtPrice(x[0])}</span><span>{qtyFmt(x[1])}</span></div>)}
    </div>
  );
}

function Orders({ orders }: { orders: any[] }) {
  if (!orders.length) return <div className="td-empty">No open orders.</div>;
  return (
    <div className="td-table">
      {orders.map((o) => (
        <div key={o.txid} className="td-tr td-ord">
          <span className={o.side === "buy" ? "up" : "down"}><b>{o.side?.toUpperCase()}</b></span>
          <span>{o.description}</span>
          <span>{o.filled ? `${qtyFmt(o.filled)} filled` : o.status}</span>
          <span>{ago(o.opened)}</span>
          <button className="td-x" onClick={(e) => { e.stopPropagation(); core.direct("trade_cancel", { txid: o.txid }); }}>Cancel</button>
        </div>
      ))}
    </div>
  );
}

function Fills() {
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => { core.rpc("trade_history", { limit: 40 }).then((r) => setRows(r.ok ? r.result : [])); }, []);
  if (!rows) return <div className="td-empty">Loading fills…</div>;
  return (
    <div className="td-table">
      <div className="td-tr td-th td-fill"><span>Date</span><span>Side</span><span>Asset</span><span>Qty</span><span>Price</span><span>Total</span><span>Fee</span></div>
      {rows.map((r) => (
        <div key={r.txid} className="td-tr td-fill">
          <span>{new Date(r.time * 1000).toLocaleDateString([], { month: "short", day: "numeric", year: "2-digit" })}</span>
          <span className={r.side === "buy" ? "up" : "down"}>{r.side}</span>
          <span><b>{r.symbol}</b></span><span>{qtyFmt(r.volume)}</span><span>{fmtPrice(r.price)}</span><span>{usd(r.cost)}</span><span>{usd(r.fee)}</span>
        </div>
      ))}
    </div>
  );
}

function Alerts({ alerts, sym }: { alerts: any[]; sym: string }) {
  const [px, setPx] = useState("");
  const [dir, setDir] = useState<"above" | "below">("above");
  return (
    <div className="td-alerts" onClick={(e) => e.stopPropagation()}>
      <div className="td-alert-new">
        <span>Alert me when <b>{sym}</b> goes</span>
        <select value={dir} onChange={(e) => setDir(e.target.value as any)}><option value="above">above</option><option value="below">below</option></select>
        <input inputMode="decimal" placeholder="price" value={px} onChange={(e) => setPx(e.target.value.replace(/[^0-9.]/g, ""))} />
        <button disabled={!(Number(px) > 0)} onClick={() => { core.direct("trade_alert_set", { symbol: sym, [dir]: Number(px) }); setPx(""); }}>Set</button>
      </div>
      {alerts.length === 0 && <div className="td-empty">No alerts. JARVIS also watches your holdings for 5%+ daily moves and order fills.</div>}
      {alerts.map((a) => (
        <div key={a.id} className={`td-alert ${a.active ? "" : "fired"}`}>
          <b>{a.symbol}</b><span>{a.above ? `≥ ${fmtPrice(a.above)}` : `≤ ${fmtPrice(a.below)}`}</span>
          <span className="muted">{a.active ? "watching" : `hit ${fmtPrice(a.fired_price)} · ${ago(a.fired_at)}`}</span>
          <button className="td-x" onClick={() => core.direct("trade_alert_remove", { alert_id: a.id })}>×</button>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ intelligence */
export function TradeInsightsCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  const hs: any[] = d.holdings ?? [];
  const risk = d.risk ?? {};
  return (
    <div className="ti">
      <div className="ti-top">
        <div className="ti-head">{d.headline}</div>
        <div className={`ti-risk ${risk.level}`}><span>RISK</span><b>{String(risk.level || "").toUpperCase()}</b></div>
      </div>
      {risk.summary && <div className="ti-risk-sum">{risk.summary}</div>}
      <div className="ti-obs">{(d.observations ?? []).map((o: string, i: number) => <div key={i}>▸ {o}</div>)}</div>
      <div className="ti-grid">
        {hs.map((h, i) => (
          <motion.div key={h.symbol} className={`ti-card r-${RATING_TONE[h.rating] ?? "hold"}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}>
            <div className="ti-card-h">
              <b>{h.symbol}</b>
              <em className={`td-rate ${RATING_TONE[h.rating] ?? ""}`}>{h.rating}</em>
              <span className="ti-conf"><i style={{ background: `linear-gradient(90deg, #39d0ff ${h.confidence ?? 0}%, rgba(159,240,255,0.1) 0)` }} />{h.confidence}%</span>
            </div>
            <div className="ti-thesis">{h.thesis}</div>
            <div className="ti-sig">{(h.signals ?? []).slice(0, 4).map((s: string, k: number) => <span key={k}>{s}</span>)}</div>
            {h.technicals && (
              <div className="ti-tech">
                <span>RSI <b className={(h.technicals.rsi14 ?? 50) > 70 ? "down" : (h.technicals.rsi14 ?? 50) < 30 ? "up" : ""}>{h.technicals.rsi14 ?? "—"}</b></span>
                <span>1M <b className={tone(h.technicals.ret_1m)}>{fmtPct(h.technicals.ret_1m, 1)}</b></span>
                <span>3M <b className={tone(h.technicals.ret_3m)}>{fmtPct(h.technicals.ret_3m, 1)}</b></span>
                <span>Trend <b>{h.technicals.trend}</b></span>
                <span>vs 200d <b className={h.technicals.above_200d ? "up" : "down"}>{h.technicals.above_200d == null ? "—" : h.technicals.above_200d ? "above" : "below"}</b></span>
              </div>
            )}
            {h.levels && (h.levels.support || h.levels.resistance) && (
              <div className="ti-lv">{h.levels.support ? <span>Support <b>{fmtPrice(h.levels.support)}</b></span> : null}{h.levels.resistance ? <span>Resistance <b>{fmtPrice(h.levels.resistance)}</b></span> : null}{h.levels.stop_idea ? <span>Stop idea <b>{fmtPrice(h.levels.stop_idea)}</b></span> : null}</div>
            )}
            {(h.catalysts ?? []).length > 0 && <div className="ti-cat">{h.catalysts.slice(0, 2).map((c: string, k: number) => <div key={k}>◆ {c}</div>)}</div>}
          </motion.div>
        ))}
      </div>
      {(d.actions ?? []).length > 0 && (
        <div className="ti-actions">
          <h4>SUGGESTED MOVES</h4>
          {d.actions.map((a: any, i: number) => (
            <div key={i} className="ti-act">
              <div><b>{a.title}</b><div className="muted small">{a.detail}</div></div>
              {a.side && a.side !== "none" && a.symbol && (
                <button className={`td-go ${a.side}`} onClick={(e) => { e.stopPropagation(); core.direct("trade_order", { symbol: a.symbol, side: a.side, amount_usd: a.amount_usd || 0, reason: a.title }); }}>
                  Review {a.side} {a.symbol}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="muted small">JARVIS's read from prices, your cost basis, technicals and headlines · {ago(d.generated_at)} · not financial advice.</div>
    </div>
  );
}

export function TradeAlertCard({ card }: { card: Card }) {
  const d = card.data ?? {};
  return (
    <div className={`ta ${d.type}`}>
      <span className="ta-ico">{d.type === "fill" ? "✓" : d.type === "move" ? (d.change_pct > 0 ? "▲" : "▼") : "◉"}</span>
      <div><b>{d.text || d.description}</b><div className="muted small">{ago(d.at || d.placed_at)}</div></div>
    </div>
  );
}

export function TradeOrderPreview({ p }: { p: any }) {
  return (
    <div className="td-conf">
      <div className={`td-conf-side ${p.side}`}>{p.side === "buy" ? "BUY" : "SELL"} <b>{p.symbol}</b> <span>{p.order_type}</span></div>
      <div className="td-pv">
        <div><span>Quantity</span><b>{qtyFmt(p.quantity)} {p.symbol}</b></div>
        <div><span>{p.order_type === "market" ? "Price now" : p.order_type === "limit" ? "Limit" : "Trigger"}</span><b>{fmtPrice(p.order_type === "market" ? p.ref_price : p.limit_price || p.stop_price)}</b></div>
        <div><span>Est. fee ({p.fee_rate_pct}%)</span><b>{usd(p.est_fee)}</b></div>
        <div className="tot"><span>{p.side === "buy" ? "Est. total" : "Est. proceeds"}</span><b>{usd(p.est_total)}</b></div>
      </div>
      {p.reason && p.reason !== "ticket" && <div className="muted small">Why: {p.reason}</div>}
      {p.order_type === "market" && <div className="muted small">Market orders fill at the live price; it won't be sent if the price moves more than 3% before you authorize.</div>}
    </div>
  );
}

export const TRADE_UP = UP;
export const TRADE_DOWN = DOWN;
