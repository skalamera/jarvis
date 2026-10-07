import { useContext, useEffect, useMemo, useRef, useState } from "react";
import type { Card } from "../types";
import { core } from "../ws/core";
import { useStore } from "../state/store";
import { MaxCtx } from "./HoloCard";

/* Kalshi display.
   Sidebar: account strip + Trending / Movers / Perps lists.
   Expanded: full terminal: search + categories, home (trending, movers, closing soon), event pages, market trade view
   (chart, order book, trades, rules, order ticket with review → confirm), perps, portfolio (positions / orders / fills). */

type Mkt = { ticker: string; event_ticker: string; title: string; label: string; yes_bid: number; yes_ask: number; no_bid: number; no_ask: number;
  last: number; prev: number; chance: number; change: number; volume: number; volume_24h: number; oi: number; status: string; close_time: string; rules: string; rules2: string; event_title?: string };
type Evt = { event_ticker: string; series_ticker: string; title: string; sub_title: string; category: string; volume_24h: number; volume: number;
  n_markets: number; markets: Mkt[]; close_time: string; image: string };
type View = { t: "home" } | { t: "search"; q: string; cat: string } | { t: "event"; id: string } | { t: "market"; id: string }
  | { t: "perps" } | { t: "perp"; id: string } | { t: "portfolio" } | { t: "auto" };

const ws = (op: string, args: Record<string, unknown> = {}) => core.workspace(op, args);
const toast = (text: string, error = false) => useStore.getState().toast({ text, error });
const usd = (n: number, d = 2) => `$${(n || 0).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const big = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : `${Math.round(n || 0)}`);
const cents = (p: number) => `${Math.round((p || 0) * 100)}¢`;
const until = (iso?: string) => {
  if (!iso) return "";
  const ms = new Date(iso).getTime() - Date.now();
  if (ms < 0) return "closed";
  const h = ms / 36e5;
  return h < 1 ? `${Math.round(h * 60)}m` : h < 48 ? `${Math.round(h)}h` : h < 24 * 60 ? `${Math.round(h / 24)}d` : new Date(iso).toLocaleDateString(undefined, { month: "short", year: "numeric" });
};
const stop = (e: React.SyntheticEvent) => e.stopPropagation();

function Chg({ v }: { v: number }) {
  if (!v) return <span className="ks-chg flat">–</span>;
  return <span className={`ks-chg ${v > 0 ? "up" : "dn"}`}>{v > 0 ? "▲" : "▼"} {Math.abs(v).toFixed(v % 1 ? 1 : 0)}</span>;
}

function Bar({ pct }: { pct: number }) {
  return <span className="ks-bar"><i style={{ width: `${Math.max(1, Math.min(100, pct))}%` }} /></span>;
}

/** Line chart with area fill + hover readout. values in % (0-100) or price. */
function Chart({ pts, unit = "%", h = 220 }: { pts: { t: number; p: number }[]; unit?: string; h?: number }) {
  const ref = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const W = 800;
  const data = pts.filter((x) => x.p != null && isFinite(x.p));
  if (data.length < 2) return <div className="ks-nochart">No price history yet.</div>;
  const lo0 = Math.min(...data.map((d) => d.p)), hi0 = Math.max(...data.map((d) => d.p));
  const pad = (hi0 - lo0) * 0.12 || (unit === "%" ? 2 : hi0 * 0.01);
  const lo = unit === "%" ? Math.max(0, lo0 - pad) : lo0 - pad, hi = unit === "%" ? Math.min(100, hi0 + pad) : hi0 + pad;
  const x = (i: number) => (i / (data.length - 1)) * W;
  const y = (v: number) => h - ((v - lo) / (hi - lo || 1)) * (h - 16) - 8;
  const line = data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.p).toFixed(1)}`).join("");
  const up = data[data.length - 1].p >= data[0].p;
  const hi_ = hover != null ? data[hover] : null;
  const fmt = (v: number) => (unit === "%" ? `${v.toFixed(1)}%` : usd(v, v < 10 ? 4 : 2));
  return (
    <div className="ks-chart">
      <div className="ks-chart-read">{hi_ ? <>{fmt(hi_.p)} <span>{new Date(hi_.t * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</span></> : <>{fmt(data[data.length - 1].p)} <span>now</span></>}</div>
      <svg ref={ref} viewBox={`0 0 ${W} ${h}`} preserveAspectRatio="none" onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => { const r = ref.current!.getBoundingClientRect(); setHover(Math.round(((e.clientX - r.left) / r.width) * (data.length - 1))); }}>
        <defs><linearGradient id="ksg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor={up ? "#3dffb0" : "#ff6b6b"} stopOpacity=".35" /><stop offset="1" stopColor={up ? "#3dffb0" : "#ff6b6b"} stopOpacity="0" /></linearGradient></defs>
        {[0.25, 0.5, 0.75].map((f) => <line key={f} x1="0" x2={W} y1={h * f} y2={h * f} className="ks-grid" />)}
        <path d={`${line}L${W},${h}L0,${h}Z`} fill="url(#ksg)" />
        <path d={line} fill="none" stroke={up ? "#3dffb0" : "#ff6b6b"} strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {hover != null && <><line x1={x(hover)} x2={x(hover)} y1="0" y2={h} className="ks-cross" /><circle cx={x(hover)} cy={y(data[hover].p)} r="4" fill="#fff" /></>}
      </svg>
      <div className="ks-axis"><span>{fmt(hi)}</span><span>{fmt(lo)}</span></div>
    </div>
  );
}

function EventTile({ e, open, openMkt }: { e: Evt; open: (id: string) => void; openMkt: (id: string) => void }) {
  return (
    <div className="ks-tile" onClick={(x) => { stop(x); open(e.event_ticker); }}>
      <div className="ks-tile-h">
        <img src={e.image} alt="" onError={(x) => (x.currentTarget.style.display = "none")} />
        <div><div className="ks-cat">{e.category}</div><div className="ks-tt">{e.title}</div></div>
      </div>
      <div className="ks-outs">
        {e.markets.slice(0, 3).map((m) => (
          <div key={m.ticker} className="ks-out" onClick={(x) => { stop(x); openMkt(m.ticker); }}>
            <span className="ks-ol">{m.label}</span>
            <b>{m.chance < 1 ? "<1" : Math.round(m.chance)}%</b>
            <span className="ks-yn"><em className="y">Yes {cents(m.yes_ask)}</em><em className="n">No {cents(m.no_ask)}</em></span>
          </div>
        ))}
      </div>
      <div className="ks-foot"><span>{usd(e.volume_24h, 0)} 24h</span><span>{e.n_markets > 3 ? `+${e.n_markets - 3} more` : ""}</span><span>{until(e.close_time)}</span></div>
    </div>
  );
}

/** Kalshi-style ticket: BUY / SELL, Dollars / Shares / Limit, outcome pill, amount, odds + max payout, 1-click
    (first click arms, second click within 5s places it; Esc or any edit disarms). */
function Ticket({ m, connected, pos, image, eventTitle }: { m: Mkt; connected: boolean; pos: any; image?: string; eventTitle?: string }) {
  const [tab, setTab] = useState<"buy" | "sell">("buy");
  const [mode, setMode] = useState<"dollars" | "shares" | "limit">("dollars");
  const [menu, setMenu] = useState(false);
  const [outcome, setOutcome] = useState<"yes" | "no">("yes");
  const [amt, setAmt] = useState("");
  const [limit, setLimit] = useState(Math.round((m.yes_ask || 0.5) * 100));
  const [q, setQ] = useState<any>(null);
  const [wallet, setWallet] = useState<any>(null);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const yw = (m as any).yes_word || "Yes", nw = (m as any).no_word || "No";
  const word = outcome === "yes" ? yw : nw;
  const loadWallet = () => connected && ws("kalshi_wallet", { ticker: m.ticker }).then((r) => r.ok && setWallet(r.result));
  useEffect(() => { loadWallet(); }, [m.ticker, connected]);
  useEffect(() => { setArmed(false); }, [tab, mode, outcome, amt, limit]);
  useEffect(() => { setAmt(""); }, [tab]);
  useEffect(() => { if (!armed) return; const t = setTimeout(() => setArmed(false), 5000); const k = (e: KeyboardEvent) => e.key === "Escape" && setArmed(false); window.addEventListener("keydown", k); return () => { clearTimeout(t); window.removeEventListener("keydown", k); }; }, [armed]);
  useEffect(() => { if (tab === "sell" && mode === "dollars") setMode("shares"); if (tab === "buy" && mode === "shares") setMode("dollars"); if (tab === "sell" && wallet?.position) setOutcome(wallet.position.side); }, [tab, wallet]);
  const n = parseFloat(amt) || 0;
  // live quote (walks the order book)
  useEffect(() => {
    setQ(null);
    if (!n || mode === "limit") return;
    const t = setTimeout(() => ws("kalshi_quote", { ticker: m.ticker, outcome, action: tab, ...(tab === "buy" ? { dollars: n } : { shares: n }) }).then((r) => r.ok && setQ(r.result)), 250);
    return () => clearTimeout(t);
  }, [n, outcome, tab, mode, m.ticker]);
  const px = (o: "yes" | "no") => tab === "buy" ? (o === "yes" ? m.yes_ask : m.no_ask) : (o === "yes" ? m.yes_bid : m.no_bid);
  const odds = q?.shares ? q.odds : Math.round(px(outcome) * 1000) / 10;
  const limitCost = (limit / 100) * n;
  const payout = mode === "limit" ? (tab === "buy" ? n : 0) : q?.payout || 0;
  const closes = m.close_time ? new Date(m.close_time).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "";
  const held = wallet?.position && wallet.position.side === outcome ? wallet.position.shares : 0;
  const can = connected && n > 0 && (mode === "limit" || q?.shares > 0) && (tab === "buy" ? true : n <= held || mode === "limit");
  const go = async () => {
    if (!can) return;
    if (!armed) { setArmed(true); return; }
    setBusy(true); setArmed(false);
    const r = mode === "limit"
      ? await ws("kalshi_order_place", { ticker: m.ticker, outcome, action: tab, count: n, price: limit / 100, tif: "good_till_canceled", confirmed: true })
      : await ws("kalshi_quick_order", { ticker: m.ticker, outcome, action: tab, ...(tab === "buy" ? { dollars: n } : { shares: n }), confirmed: true });
    setBusy(false);
    if (r.ok) {
      const f = Number(r.result.fill_count || 0);
      toast(mode === "limit" ? `Limit order placed: ${tab} ${n} ${word} @ ${limit}¢${f ? ` · ${f} filled` : ""}` : f ? `${tab === "buy" ? "Bought" : "Sold"} ${f} ${word}${r.result.average_fill_price ? ` @ ${Math.round(+r.result.average_fill_price * 100)}¢` : ""}` : "Nothing filled (price moved). Try again.");
      setAmt(""); loadWallet();
    } else toast(r.error || "Order failed.", true);
  };
  return (
    <div className="kt" onClick={stop}>
      <div className="kt-head">
        <button className={tab === "buy" ? "on" : ""} onClick={() => setTab("buy")}>BUY</button>
        <button className={tab === "sell" ? "on" : ""} onClick={() => setTab("sell")}>SELL</button>
        <div className="kt-mode">
          <button onClick={() => setMenu(!menu)}>{mode.toUpperCase()} ⌄</button>
          {menu && <div className="kt-menu">{(tab === "buy" ? ["dollars", "limit"] : ["shares", "limit"]).map((x) => <button key={x} onClick={() => { setMode(x as any); setMenu(false); }}>{x === "limit" ? "Limit order" : x[0].toUpperCase() + x.slice(1)}</button>)}</div>}
        </div>
      </div>
      <div className="kt-title">{image && <img src={image} alt="" onError={(e) => (e.currentTarget.style.display = "none")} />}<span>{tab === "sell" && <em className={outcome}>{word} · </em>}{(m as any).yes_word === "Up" ? `${eventTitle || m.title}` : m.label && m.label !== m.title ? m.label : m.title}</span></div>
      {(tab === "buy" || mode === "limit") && (
        <div className="kt-pill">
          <button className={`y ${outcome === "yes" ? "on" : ""}`} onClick={() => setOutcome("yes")}>{yw.toUpperCase()} {Math.round(px("yes") * 1000) / 10}¢</button>
          <button className={`n ${outcome === "no" ? "on" : ""}`} onClick={() => setOutcome("no")}>{nw.toUpperCase()} {Math.round(px("no") * 1000) / 10}¢</button>
        </div>
      )}
      <label className="kt-amt">
        <span>{mode === "dollars" ? "Dollars" : mode === "shares" ? "Shares" : "Contracts"}</span>
        <input inputMode="decimal" value={mode === "dollars" && amt ? `$${amt}` : amt} placeholder={mode === "dollars" ? "$0" : "0"}
          onChange={(e) => setAmt(e.target.value.replace(/[^0-9.]/g, ""))} />
      </label>
      {mode === "limit" && <label className="kt-amt sm"><span>Limit price</span><input inputMode="numeric" value={`${limit}¢`} onChange={(e) => setLimit(Math.max(1, Math.min(99, parseInt(e.target.value.replace(/\D/g, "")) || 1)))} /></label>}
      {tab === "sell" && mode !== "limit" && held > 0 && <div className="kt-quick">{[0.25, 0.5, 1].map((f) => <button key={f} onClick={() => setAmt(String(Math.floor(held * f)))}>{f === 1 ? "Max" : `${f * 100}%`}</button>)}</div>}
      <div className="kt-sub">{connected ? tab === "buy" ? `Predictions account · ${usd(wallet?.balance ?? 0)} available` : `You own ${held} ${word} share${held === 1 ? "" : "s"}` : "Connect your Kalshi account to trade"}</div>
      {tab === "buy" ? (
        <>
          <div className="kt-line"><span>Odds</span><b>{odds}% chance</b></div>
          <div className="kt-line big"><span>Max payout<small>{closes}</small></span><b>{usd(payout, payout % 1 ? 2 : 0)}</b></div>
          {mode !== "limit" && q?.shares > 0 && <div className="kt-fine">{q.shares} shares · avg {Math.round(q.avg_price * 1000) / 10}¢ · fee {usd(q.fee)} · cost {usd(q.cost)}</div>}
          {mode === "limit" && n > 0 && <div className="kt-fine">Cost {usd(limitCost)} if filled · rests on the book until matched</div>}
        </>
      ) : (
        <>
          {mode !== "limit" && q?.shares > 0 && <div className="kt-line big"><span>You'll receive</span><b>{usd(q.proceeds)}</b></div>}
          {mode !== "limit" && q?.shares > 0 && <div className="kt-fine">avg {Math.round(q.avg_price * 1000) / 10}¢ · fee {usd(q.fee)}</div>}
        </>
      )}
      {n > 0 && mode !== "limit" && q && !q.liquidity_ok && <div className="ks-warn">Not enough on the book for that size right now.</div>}
      {tab === "sell" && n > held && mode !== "limit" && <div className="ks-warn">You only own {held}.</div>}
      <button className={`kt-go ${armed ? "armed" : ""} ${can ? "ready" : ""}`} disabled={!can || busy} onClick={go}>
        {busy ? <span className="cx-spin" /> : armed ? `Tap again to ${tab} · ${tab === "buy" ? usd(mode === "limit" ? limitCost : q?.cost || 0) : n + " shares"}` : <>⚡ {tab === "buy" ? "Buy" : "Sell"} with 1-Click</>}
      </button>
    </div>
  );
}

function MarketView({ id, connected, openEvt }: { id: string; connected: boolean; openEvt: (id: string) => void }) {
  const [d, setD] = useState<any>(null);
  const [period, setPeriod] = useState("1w");
  const load = (pd = period) => ws("kalshi_market", { ticker: id, period: pd }).then((r) => (r.ok ? setD(r.result) : toast(r.error || "Market failed to load", true)));
  useEffect(() => { setD(null); load(); const t = setInterval(() => load(), 20_000); return () => clearInterval(t); }, [id]);
  if (!d) return <div className="ks-load"><span className="cx-spin" /> Loading market…</div>;
  const m: Mkt = d.market;
  const yes = d.book.yes as number[][], no = d.book.no as number[][];
  const maxQ = Math.max(1, ...yes.map((x) => x[1]), ...no.map((x) => x[1]));
  return (
    <div className="ks-mkt">
      <div className="ks-mkt-main">
        <div className="ks-crumb" onClick={() => openEvt(d.event.event_ticker)}>{d.event.category} · {d.event.title} ›</div>
        <div className="ks-mkt-t">{m.title}</div>
        <div className="ks-big"><b>{m.chance}%</b> chance <Chg v={m.change} /> <span className="muted small">· {usd(m.volume, 0)} vol · {big(m.oi)} open interest · closes {until(m.close_time)}</span></div>
        <div className="ks-periods">{["1d", "1w", "1m", "all"].map((p) => <button key={p} className={period === p ? "on" : ""} onClick={() => { setPeriod(p); load(p); }}>{p.toUpperCase()}</button>)}</div>
        <Chart pts={d.candles} />
        <div className="ks-two">
          <div>
            <div className="ks-sec">ORDER BOOK</div>
            <div className="ks-book">
              <div className="ks-bk-h"><span>Yes bids</span><span>Qty</span><span>No bids</span><span>Qty</span></div>
              {Array.from({ length: Math.max(yes.length, no.length) }).map((_, i) => {
                const a = yes[yes.length - 1 - i], b = no[no.length - 1 - i];
                return (
                  <div key={i} className="ks-bk-r">
                    <span className="y">{a ? cents(a[0]) : ""}<i style={{ width: a ? `${(a[1] / maxQ) * 100}%` : 0 }} /></span><span>{a ? big(a[1]) : ""}</span>
                    <span className="n">{b ? cents(b[0]) : ""}<i style={{ width: b ? `${(b[1] / maxQ) * 100}%` : 0 }} /></span><span>{b ? big(b[1]) : ""}</span>
                  </div>
                );
              })}
            </div>
          </div>
          <div>
            <div className="ks-sec">RECENT TRADES</div>
            <div className="ks-trades">
              {d.trades.map((t: any, i: number) => (
                <div key={i}><span className={t.side === "yes" ? "y" : "n"}>{t.side?.toUpperCase()}</span><span>{cents(t.side === "yes" ? t.price : 1 - t.price)}</span><span>{big(t.count)}</span><span className="muted">{new Date(t.t).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</span></div>
              ))}
            </div>
          </div>
        </div>
        <div className="ks-sec">RULES</div>
        <div className="ks-rules">{m.rules}{m.rules2 && <details><summary>More</summary>{m.rules2}</details>}</div>
      </div>
      <div className="ks-side">
        {d.position && <div className="ks-pos">You hold <b>{d.position.position > 0 ? d.position.position : -d.position.position} {d.position.position > 0 ? "YES" : "NO"}</b> · exposure {usd(d.position.exposure)}</div>}
        <Ticket m={m} connected={connected} pos={d.position} image={`https://kalshi-public-docs.s3.amazonaws.com/series-images-webp/${d.event.series_ticker}.webp`} eventTitle={d.event.title} />
      </div>
    </div>
  );
}

function EventView({ id, openMkt }: { id: string; openMkt: (id: string) => void }) {
  const [e, setE] = useState<Evt | null>(null);
  useEffect(() => { ws("kalshi_event", { event_ticker: id }).then((r) => (r.ok ? setE(r.result) : toast(r.error || "Event failed", true))); }, [id]);
  if (!e) return <div className="ks-load"><span className="cx-spin" /> Loading event…</div>;
  return (
    <div className="ks-evt">
      <div className="ks-tile-h big"><img src={e.image} alt="" onError={(x) => (x.currentTarget.style.display = "none")} /><div><div className="ks-cat">{e.category}</div><div className="ks-mkt-t">{e.title}</div><div className="muted small">{e.sub_title} · {usd(e.volume, 0)} total vol · {e.n_markets} outcomes</div></div></div>
      <div className="ks-tbl">
        <div className="ks-tr h"><span>Outcome</span><span>Chance</span><span>24h</span><span>Yes</span><span>No</span><span>Volume</span></div>
        {e.markets.map((m) => (
          <div key={m.ticker} className="ks-tr" onClick={() => openMkt(m.ticker)}>
            <span>{m.label}</span><span><b>{m.chance}%</b><Bar pct={m.chance} /></span><span><Chg v={m.change} /></span>
            <span><em className="ks-px y">{cents(m.yes_ask)}</em></span><span><em className="ks-px n">{cents(m.no_ask)}</em></span><span>{usd(m.volume, 0)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PerpsView({ list, open }: { list: any[]; open: (id: string) => void }) {
  return (
    <div className="ks-tbl">
      <div className="ks-tr h p"><span>Perp</span><span>Price</span><span>Bid / Ask</span><span>24h volume</span><span>Open interest</span><span>Leverage</span></div>
      {list.map((p) => (
        <div key={p.ticker} className="ks-tr p" onClick={() => open(p.ticker)}>
          <span><b>{p.title}</b> <em className="muted">{p.asset_class}</em></span><span>{usd(p.price, p.price < 10 ? 4 : 2)}</span>
          <span className="muted">{p.bid} / {p.ask}</span><span>{usd(p.volume_24h_usd, 0)}</span><span>{usd(p.oi_usd, 0)}</span><span>{p.leverage ? `${Number(p.leverage).toFixed(1)}×` : ""}</span>
        </div>
      ))}
    </div>
  );
}

function PerpView({ id }: { id: string }) {
  const [d, setD] = useState<any>(null);
  useEffect(() => { const l = () => ws("kalshi_perp", { ticker: id }).then((r) => r.ok && setD(r.result)); l(); const t = setInterval(l, 15_000); return () => clearInterval(t); }, [id]);
  if (!d) return <div className="ks-load"><span className="cx-spin" /> Loading perp…</div>;
  const maxQ = Math.max(1, ...d.bids.map((x: number[]) => x[1]), ...d.asks.map((x: number[]) => x[1]));
  return (
    <div className="ks-mkt">
      <div className="ks-mkt-main">
        <div className="ks-mkt-t">{d.title} perpetual</div>
        <div className="ks-big"><b>{usd(d.price, d.price < 10 ? 4 : 2)}</b> <span className="muted small">· {d.asset_class} · OI {usd(d.oi_usd, 0)} · 24h {usd(d.volume_24h_usd, 0)} · ~{Number(d.leverage || 0).toFixed(1)}× max leverage</span></div>
        <div className="ks-fund">Funding (est.) <b className={(d.funding_rate || 0) >= 0 ? "up" : "dn"}>{((d.funding_rate || 0) * 100).toFixed(4)}%</b> · next {d.next_funding ? new Date(d.next_funding).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }) : "—"}</div>
        <Chart pts={d.candles} unit="$" />
      </div>
      <div className="ks-side">
        <div className="ks-sec">ORDER BOOK</div>
        <div className="ks-pbook">
          {d.asks.map((a: number[], i: number) => <div key={"a" + i} className="n"><span>{a[0]}</span><span>{big(a[1])}</span><i style={{ width: `${(a[1] / maxQ) * 100}%` }} /></div>)}
          <div className="ks-mid">{usd(d.price, 4)}</div>
          {d.bids.map((b: number[], i: number) => <div key={"b" + i} className="y"><span>{b[0]}</span><span>{big(b[1])}</span><i style={{ width: `${(b[1] / maxQ) * 100}%` }} /></div>)}
        </div>
        <div className="ks-warn">Perps trading isn't wired into JARVIS yet; this is live market data.</div>
      </div>
    </div>
  );
}

function Portfolio({ connected, openMkt }: { connected: boolean; openMkt: (id: string) => void }) {
  const [d, setD] = useState<any>(null);
  const [tab, setTab] = useState<"positions" | "orders" | "fills" | "settlements">("positions");
  const load = () => ws("kalshi_account").then((r) => (r.ok ? setD(r.result) : setD({ error: r.error })));
  useEffect(() => { if (connected) load(); }, [connected]);
  if (!connected) return (
    <div className="ks-connect">
      <div className="ks-mkt-t">Connect your Kalshi account</div>
      <ol>
        <li>On kalshi.com → Account → Profile Settings → <b>API Keys</b>, create a key (Ed25519 is fine).</li>
        <li>Save the downloaded private key file somewhere private, e.g. <code>~/.hermes/kalshi.key</code>.</li>
        <li>Add to <code>~/.hermes/.env</code>: <code>KALSHI_API_KEY_ID=…</code> and <code>KALSHI_PRIVATE_KEY_PATH=~/.hermes/kalshi.key</code></li>
        <li>Reopen this card. Balance, positions, orders and trading light up.</li>
      </ol>
      <div className="muted small">The key never goes in the chat or the repo; JARVIS signs each request locally.</div>
    </div>
  );
  if (!d) return <div className="ks-load"><span className="cx-spin" /> Loading portfolio…</div>;
  if (d.error) return <div className="ks-warn">{d.error}</div>;
  const cancel = async (id: string) => { const r = await ws("kalshi_order_cancel", { order_id: id }); r.ok ? (toast("Order canceled."), load()) : toast(r.error || "Cancel failed", true); };
  const unreal = d.positions.reduce((n: number, p: any) => n + (p.unrealized || 0), 0);
  return (
    <div>
      <div className="ks-acct">
        <div><span>Cash</span><b>{usd(d.balance)}</b></div><div><span>Positions value</span><b>{usd(d.portfolio_value)}</b></div>
        <div><span>Total</span><b>{usd(d.balance + d.portfolio_value)}</b></div><div><span>Unrealized P&L</span><b className={unreal >= 0 ? "up" : "dn"}>{usd(unreal)}</b></div>
        <button className="cs-go" onClick={load}>⟳</button>
      </div>
      <div className="ks-tabs">{(["positions", "orders", "fills", "settlements"] as const).map((t) => <button key={t} className={`tog ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)} {t === "positions" ? d.positions.length : t === "orders" ? d.orders.length : ""}</button>)}</div>
      {tab === "positions" && <div className="ks-tbl">
        <div className="ks-tr h q"><span>Market</span><span>Side</span><span>Contracts</span><span>Cost</span><span>Value</span><span>P&L</span></div>
        {d.positions.map((p: any) => <div key={p.ticker} className="ks-tr q" onClick={() => openMkt(p.ticker)}><span>{p.title}<em className="muted"> {p.label}</em></span><span className={p.side === "yes" ? "y" : "n"}>{p.side.toUpperCase()}</span><span>{p.contracts}</span><span>{usd(p.exposure)}</span><span>{usd(p.value)}</span><span className={p.unrealized >= 0 ? "up" : "dn"}>{usd(p.unrealized)}</span></div>)}
        {!d.positions.length && <div className="muted">No open positions.</div>}
      </div>}
      {tab === "orders" && <div className="ks-tbl">
        {d.orders.map((o: any) => <div key={o.order_id} className="ks-tr q"><span>{o.ticker}</span><span className={o.side === "yes" ? "y" : "n"}>{o.action} {o.side}</span><span>{o.remaining_count_fp}</span><span>{cents(+(o.side === "yes" ? o.yes_price_dollars : o.no_price_dollars))}</span><span className="muted">{o.status}</span><span><button className="ks-cancel" onClick={() => cancel(o.order_id)}>Cancel</button></span></div>)}
        {!d.orders.length && <div className="muted">No resting orders.</div>}
      </div>}
      {tab === "fills" && <div className="ks-tbl">{d.fills.map((f: any, i: number) => <div key={i} className="ks-tr q"><span>{f.ticker}</span><span className={f.side === "yes" ? "y" : "n"}>{f.action} {f.side}</span><span>{f.count_fp}</span><span>{cents(+(f.side === "yes" ? f.yes_price_dollars : f.no_price_dollars))}</span><span className="muted">{new Date(f.created_time).toLocaleString()}</span><span /></div>)}{!d.fills.length && <div className="muted">No fills yet.</div>}</div>}
      {tab === "settlements" && <div className="ks-tbl">{d.settlements.map((s: any, i: number) => <div key={i} className="ks-tr q"><span>{s.ticker}</span><span>{s.market_result}</span><span>{s.yes_count_fp ?? ""}/{s.no_count_fp ?? ""}</span><span>{usd(+(s.revenue_dollars ?? (s.revenue || 0) / 100))}</span><span className="muted">{s.settled_time && new Date(s.settled_time).toLocaleDateString()}</span><span /></div>)}{!d.settlements.length && <div className="muted">No settlements.</div>}</div>}
    </div>
  );
}

function useOverview(card: Card) {
  const [d, setD] = useState<any>(card.data?.trending ? card.data : null);
  useEffect(() => {
    const l = () => ws("kalshi_overview").then((r) => (r.ok ? setD(r.result) : toast(r.error || "Kalshi failed to load", true)));
    if (!d) l();
    const t = setInterval(l, 60_000);
    return () => clearInterval(t);
  }, []);
  return d;
}

export function KalshiCard({ card }: { card: Card }) {
  const mx = useContext(MaxCtx);
  const d = useOverview(card);
  const [stack, setStack] = useState<View[]>([card.data?.focus ? { t: "market", id: card.data.focus.market.ticker } : card.data?.search ? { t: "search", q: card.data.search.query, cat: "" } : { t: "home" }]);
  const v = stack[stack.length - 1];
  const push = (x: View) => { setStack([...stack, x]); if (!mx?.max) mx?.setMax(true); };
  if (!mx?.max) return <KalshiMini d={d} push={push} />;
  return <KalshiXL d={d} v={v} push={push} back={stack.length > 1 ? () => setStack(stack.slice(0, -1)) : null} home={() => setStack([{ t: "home" }])} />;
}

function KalshiMini({ d, push }: { d: any; push: (v: View) => void }) {
  const [tab, setTab] = useState<"trending" | "movers" | "perps">("trending");
  if (!d) return <div className="ks-load"><span className="cx-spin" /> Loading Kalshi…</div>;
  return (
    <div className="ks mini">
      {d.connected && d.account && !d.account.error ? (
        <div className="ks-acct sm" onClick={(e) => { stop(e); push({ t: "portfolio" }); }}>
          <div><span>Cash</span><b>{usd(d.account.balance)}</b></div><div><span>Positions</span><b>{usd(d.account.portfolio_value)}</b></div><div><span>Total</span><b>{usd(d.account.balance + d.account.portfolio_value)}</b></div>
        </div>
      ) : <div className="ks-hint" onClick={(e) => { stop(e); push({ t: "portfolio" }); }}>Connect your Kalshi account for balance & positions ›</div>}
      <div className="ks-tabs" onClick={stop}>{(["trending", "movers", "perps"] as const).map((t) => <button key={t} className={`tog ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{t === "trending" ? "🔥 Trending" : t === "movers" ? "⚡ Movers" : "∞ Perps"}</button>)}</div>
      {tab === "trending" && d.trending.slice(0, 8).map((e: Evt) => (
        <div key={e.event_ticker} className="ks-row" onClick={(x) => { stop(x); push({ t: "event", id: e.event_ticker }); }}>
          <img src={e.image} alt="" onError={(x) => (x.currentTarget.style.visibility = "hidden")} />
          <div className="ks-row-m"><div className="ks-tt">{e.title}</div>
            {e.markets.slice(0, 2).map((m) => <div key={m.ticker} className="ks-mo"><span>{m.label}</span><Bar pct={m.chance} /><b>{Math.round(m.chance)}%</b></div>)}</div>
          <span className="ks-vol">{usd(e.volume_24h, 0)}</span>
        </div>
      ))}
      {tab === "movers" && d.movers.map((m: Mkt) => (
        <div key={m.ticker} className="ks-row" onClick={(x) => { stop(x); push({ t: "market", id: m.ticker }); }}>
          <div className="ks-row-m"><div className="ks-tt">{m.event_title}</div><div className="ks-mo"><span>{m.label}</span><b>{Math.round(m.chance)}%</b><Chg v={m.change} /></div></div>
        </div>
      ))}
      {tab === "perps" && d.perps.slice(0, 10).map((p: any) => (
        <div key={p.ticker} className="ks-row" onClick={(x) => { stop(x); push({ t: "perp", id: p.ticker }); }}>
          <div className="ks-row-m"><div className="ks-tt">{p.title}</div><div className="muted small">{p.asset_class} · 24h {usd(p.volume_24h_usd, 0)}</div></div>
          <b>{usd(p.price, p.price < 10 ? 4 : 2)}</b>
        </div>
      ))}
    </div>
  );
}

function KalshiXL({ d, v, push, back, home }: { d: any; v: View; push: (v: View) => void; back: (() => void) | null; home: () => void }) {
  const [q, setQ] = useState(v.t === "search" ? v.q : "");
  const [cat, setCat] = useState(v.t === "search" ? v.cat : "");
  const [sort, setSort] = useState("volume");
  const [res, setRes] = useState<any>(null);
  useEffect(() => {
    if (v.t !== "search") return;
    setRes(null);
    ws("kalshi_search", { query: v.q, category: v.cat, sort, limit: 60 }).then((r) => (r.ok ? setRes(r.result) : toast(r.error || "Search failed", true)));
  }, [v, sort]);
  const openEvt = (id: string) => push({ t: "event", id });
  const openMkt = (id: string) => push({ t: "market", id });
  const nav = useMemo(() => [["home", "Home"], ["perps", "Perps"], ["portfolio", "Portfolio"], ["auto", "Autopilot"]] as const, []);
  return (
    <div className="ks xl" onClick={stop}>
      <div className="ks-top">
        {back && <button className="ks-back" onClick={back}>‹</button>}
        {nav.map(([t, l]) => <button key={t} className={`ks-nav ${v.t === t ? "on" : ""}`} onClick={() => (t === "home" ? home() : push({ t } as View))}>{l}</button>)}
        <form className="cs-q" onSubmit={(e) => { e.preventDefault(); push({ t: "search", q, cat }); }}>
          <span>⌕</span><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search markets: elections, bitcoin, fed, NFL, weather…" />
        </form>
        {d?.connected && d.account && !d.account.error && <div className="ks-pill" onClick={() => push({ t: "portfolio" })}>{usd(d.account.balance + d.account.portfolio_value)}</div>}
      </div>
      <div className="ks-cats">
        <button className={!cat && v.t === "search" ? "on" : ""} onClick={() => { setCat(""); push({ t: "search", q, cat: "" }); }}>All</button>
        {(d?.categories || []).map((c: string) => <button key={c} className={cat === c && v.t === "search" ? "on" : ""} onClick={() => { setCat(c); push({ t: "search", q, cat: c }); }}>{c}</button>)}
      </div>
      <div className="ks-body">
        {!d && v.t === "home" && <div className="ks-load"><span className="cx-spin" /> Loading Kalshi (first load reads the whole market catalog, ~10s)…</div>}
        {d && v.t === "home" && (
          <>
            <div className="ks-sec">🔥 TRENDING · {d.n_events.toLocaleString()} open events</div>
            <div className="ks-grid">{d.trending.slice(0, 12).map((e: Evt) => <EventTile key={e.event_ticker} e={e} open={openEvt} openMkt={openMkt} />)}</div>
            <div className="ks-two">
              <div>
                <div className="ks-sec">⚡ BIGGEST MOVERS</div>
                <div className="ks-tbl">{d.movers.map((m: Mkt) => <div key={m.ticker} className="ks-tr m" onClick={() => openMkt(m.ticker)}><span>{m.event_title}<em className="muted"> · {m.label}</em></span><span><b>{Math.round(m.chance)}%</b></span><span><Chg v={m.change} /></span></div>)}</div>
              </div>
              <div>
                <div className="ks-sec">⏳ CLOSING SOON</div>
                <div className="ks-tbl">{d.closing_soon.map((e: Evt) => <div key={e.event_ticker} className="ks-tr m" onClick={() => openEvt(e.event_ticker)}><span>{e.title}<em className="muted"> · {e.markets[0]?.label} {Math.round(e.markets[0]?.chance || 0)}%</em></span><span>{until(e.close_time)}</span><span className="muted">{usd(e.volume_24h, 0)}</span></div>)}</div>
              </div>
            </div>
            <div className="ks-sec">∞ PERPS</div>
            <PerpsView list={d.perps.slice(0, 8)} open={(id) => push({ t: "perp", id })} />
          </>
        )}
        {v.t === "search" && (
          <>
            <div className="ks-sec ks-sr">{res ? `${res.total.toLocaleString()} events${v.q ? ` for “${v.q}”` : ""}${v.cat ? ` in ${v.cat}` : ""}` : "Searching…"}
              <select value={sort} onChange={(e) => setSort(e.target.value)}><option value="volume">24h volume</option><option value="total">Total volume</option><option value="closing">Closing soonest</option><option value="new">Newest</option></select></div>
            {res && <div className="ks-grid">{res.events.map((e: Evt) => <EventTile key={e.event_ticker} e={e} open={openEvt} openMkt={openMkt} />)}</div>}
          </>
        )}
        {v.t === "event" && <EventView id={v.id} openMkt={openMkt} />}
        {v.t === "market" && <MarketView id={v.id} connected={!!d?.connected} openEvt={openEvt} />}
        {v.t === "perps" && d && <PerpsView list={d.perps} open={(id) => push({ t: "perp", id })} />}
        {v.t === "perp" && <PerpView id={v.id} />}
        {v.t === "portfolio" && <Portfolio connected={!!d?.connected} openMkt={openMkt} />}
        {v.t === "auto" && <Autopilot openMkt={openMkt} />}
      </div>
    </div>
  );
}

/** BTC 15-min autopilot: rules, ON/OFF (two-step to turn on), live status, trades + P&L, decision log. */
function Autopilot({ openMkt }: { openMkt: (id: string) => void }) {
  const [s, setS] = useState<any>(null);
  const [c, setC] = useState<any>(null);
  const [arm, setArm] = useState(false);
  const [live, setLive] = useState<any>(null);
  const load = () => ws("kalshi_auto_status").then((r) => { if (r.ok) { setS(r.result); setC((x: any) => x || r.result.config); } });
  useEffect(() => { load(); const t = setInterval(load, 10_000); return () => clearInterval(t); }, []);
  const [, tick] = useState(0);
  useEffect(() => { const l = () => ws("kalshi_auto_live").then((r) => r.ok && setLive(r.result)); l(); const t = setInterval(l, 10_000); const s1 = setInterval(() => tick((x) => x + 1), 1000); return () => { clearInterval(t); clearInterval(s1); }; }, []);
  if (!s || !c) return <div className="ks-load"><span className="cx-spin" /> Loading autopilot…</div>;
  const save = async (patch: any) => { const r = await ws("kalshi_auto_config", { ...c, ...patch }); if (r.ok) { setS(r.result); setC(r.result.config); } else toast(r.error || "Couldn't save", true); };
  const on = s.config.enabled;
  const secs = live?.close_time ? Math.max(0, Math.round((new Date(live.close_time).getTime() - Date.now()) / 1000)) : null;
  const inWin = secs != null && secs <= c.window_s;
  return (
    <div className="ap">
      <div className={`ap-hero ${on ? "on" : ""}`}>
        <div>
          <div className="ap-k">BTC 15-MIN AUTOPILOT</div>
          <div className="ap-rule">In the last <b>{Math.round(c.window_s / 60)} min</b> of each 15-min BTC market, if <b>Up or Down ≥ {Math.round(c.threshold * 100)}%</b>, buy <b>${c.stake}</b> of that side. One trade per market.</div>
          <div className="ap-st">{on ? <><i className="ap-dot" /> RUNNING</> : "OFF"} · today {usd(s.today_pnl)} · all-time {usd(s.total_pnl)} ({s.wins}W / {s.losses}L)</div>
        </div>
        {!s.connected ? <div className="ks-warn">Connect Kalshi first.</div> : on ? (
          <button className="ap-btn off" onClick={() => save({ enabled: false })}>Turn off</button>
        ) : arm ? (
          <div className="ap-arm"><div>Real money: up to <b>${c.stake}</b> every 15 min, stops for the day at <b>-${c.daily_loss_limit}</b>.</div>
            <div><button className="ap-btn go" onClick={() => { setArm(false); save({ enabled: true }); }}>Start autopilot</button><button className="ks-cancel" onClick={() => setArm(false)}>Cancel</button></div></div>
        ) : <button className="ap-btn go" onClick={() => setArm(true)}>Turn on…</button>}
      </div>
      {live && (
        <div className="ap-live" onClick={() => openMkt(live.ticker)}>
          <span>NOW · {live.ticker}</span><span>{live.title?.replace("Will ", "")}</span>
          <b className="up">UP {Math.round(live.yes_ask * 100)}¢</b><b className="dn">DOWN {Math.round(live.no_ask * 100)}¢</b>
          <span className={inWin ? "ap-win" : ""}>{secs != null ? `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")} left${inWin ? " · in window" : ""}` : ""}</span>
        </div>
      )}
      <div className="ap-grid" onClick={stop}>
        <label>Stake per trade<span>$<input type="number" value={c.stake} onChange={(e) => setC({ ...c, stake: +e.target.value })} /></span></label>
        <label>Min odds<span><input type="number" value={Math.round(c.threshold * 100)} onChange={(e) => setC({ ...c, threshold: +e.target.value / 100 })} />%</span></label>
        <label>Window (last…)<span><input type="number" value={Math.round(c.window_s / 60)} onChange={(e) => setC({ ...c, window_s: +e.target.value * 60 })} />min</span></label>
        <label>Max price paid<span><input type="number" value={Math.round(c.max_price * 100)} onChange={(e) => setC({ ...c, max_price: +e.target.value / 100 })} />¢</span></label>
        <label>Daily loss limit<span>$<input type="number" value={c.daily_loss_limit} onChange={(e) => setC({ ...c, daily_loss_limit: +e.target.value })} /></span></label>
        <button className="cs-go" onClick={() => save({})}>Save rules</button>
      </div>
      <div className="ks-two">
        <div>
          <div className="ks-sec">TRADES</div>
          <div className="ks-tbl">
            {s.trades.map((t: any) => (
              <div key={t.ticker} className="ks-tr ap-tr" onClick={() => openMkt(t.ticker)}>
                <span>{new Date(t.t * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</span>
                <span className={t.direction === "UP" ? "up" : "dn"}>{t.direction}</span><span>{t.shares} @ {Math.round(t.avg_price * 100)}¢</span><span>{usd(t.cost)}</span>
                <span className={t.result === "won" ? "up" : t.result === "lost" ? "dn" : "muted"}>{t.result ? `${t.result} ${usd(t.pnl)}` : "pending"}</span>
              </div>
            ))}
            {!s.trades.length && <div className="muted">No trades yet.</div>}
          </div>
        </div>
        <div>
          <div className="ks-sec">ACTIVITY</div>
          <div className="ap-log">{s.log.map((l: any, i: number) => <div key={i} className={l.trade ? "t" : ""}><span>{new Date(l.t * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</span>{l.msg}</div>)}</div>
        </div>
      </div>
    </div>
  );
}
