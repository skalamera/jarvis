import { useEffect, useMemo, useState } from "react";
import { core } from "../ws/core";
import type { Card } from "../types";
import {
  CompareChart, DOWN, Logo, PriceChart, RangeBar, SERIES_COLORS, Spark, UP, ago, fmtBig, fmtChg, fmtEps, fmtNum, fmtPct,
  fmtPrice, fmtWhen, tone,
} from "./fx";

/* Finance cards: STOCK (hero + live chart + stats + analysts + earnings + revenue + profile + news),
   COMPARE (normalized % performance), MARKET (indices, sector heatmap, movers, crypto), CRYPTO (coin card).
   All data is read-only; range tabs re-fetch through Core's allow-listed rpc. */

const open = (u?: string) => u && window.jarvis?.open(u);
const STOCK_RANGES = ["1D", "5D", "1M", "6M", "YTD", "1Y", "5Y", "MAX"];
const CRYPTO_RANGES = ["1D", "7D", "1M", "1Y", "MAX"];
const RANGE_WORD: Record<string, string> = { "1D": "today", "5D": "past 5 days", "1M": "past month", "6M": "past 6 months", YTD: "year to date", "1Y": "past year", "5Y": "past 5 years", MAX: "all time", "7D": "past 7 days" };

const STATE_LABEL: Record<string, [string, string]> = {
  REGULAR: ["Market open", "live"], PRE: ["Pre-market", "pre"], PREPRE: ["Market closed", "closed"],
  POST: ["After hours", "post"], POSTPOST: ["Market closed", "closed"], CLOSED: ["Market closed", "closed"],
};

function Delta({ chg, pct, big = false }: { chg?: number | null; pct?: number | null; big?: boolean }) {
  const t = tone(pct ?? chg);
  return (
    <span className={`fx-delta ${t} ${big ? "big" : ""}`}>
      <b>{t === "up" ? "▲" : t === "down" ? "▼" : "■"}</b>
      {chg != null && <span>{fmtChg(chg)}</span>}
      <span>({fmtPct(pct)})</span>
    </span>
  );
}

function Ranges({ list, cur, busy, pick }: { list: string[]; cur: string; busy: string; pick: (r: string) => void }) {
  return (
    <div className="fx-ranges">
      {list.map((r) => (
        <button key={r} className={`fx-rng ${cur === r ? "on" : ""} ${busy === r ? "busy" : ""}`} onClick={(e) => { e.stopPropagation(); pick(r); }}>{r}</button>
      ))}
    </div>
  );
}

function useRangeChart<T extends { range: string }>(initial: T, op: string, args: (r: string) => Record<string, unknown>) {
  const [chart, setChart] = useState<T>(initial);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => setChart(initial), [initial]);
  const pick = async (r: string) => {
    if (r === chart?.range || busy) return;
    setBusy(r); setErr("");
    const res = await core.rpc(op, args(r));
    setBusy("");
    if (res.ok && res.result?.points?.length) setChart(res.result);
    else setErr(res.error || "No data for that range.");
  };
  return { chart, busy, err, pick };
}

/* ------------------------------------------------------------------ STOCK */
export function StockCard({ card }: { card: Card }) {
  const d = card.data || {};
  const { chart, busy, err, pick } = useRangeChart(d.chart || { range: "1D", points: [] }, "market_chart", (r) => ({ symbol: d.symbol, range: r }));
  const [hover, setHover] = useState<{ t: number; c: number; pct: number | null } | null>(null);
  const [tab, setTab] = useState<"overview" | "analysts" | "earnings" | "about">("overview");
  const st = d.stats || {};
  const is1D = chart.range === "1D";
  const chartBase: number | null = is1D ? d.prev_close ?? chart.base : chart.base; // one reference for $ and %
  const heroPrice = hover ? hover.c : is1D ? d.price : chart.points?.[chart.points.length - 1]?.c ?? d.price;
  const heroPct = hover ? hover.pct : is1D ? d.change_pct : chart.period_change_pct;
  const heroChg = hover ? (chartBase != null ? hover.c - chartBase : null) : is1D ? d.change : chart.period_change;
  const [stateLabel, stateCls] = STATE_LABEL[d.market_state] || ["", ""];
  const hasAnalyst = !!d.analyst, hasEarn = !!(d.earnings?.history?.length || d.financials?.length);
  const up = (heroPct ?? 0) >= 0;
  return (
    <div className={`fx fx-stock ${up ? "is-up" : "is-down"}`}>
      <div className="fx-hero">
        <Logo urls={d.logos} label={d.symbol} size={52} />
        <div className="fx-id">
          <div className="fx-name" title={d.name}>{d.name}</div>
          <div className="fx-sub">
            <b>{d.symbol}</b>{d.exchange && <> · {d.exchange}</>}{d.profile?.sector && <> · {d.profile.sector}</>}
            {stateLabel && <span className={`fx-state ${stateCls}`}><i />{stateLabel}</span>}
          </div>
        </div>
      </div>

      <div className="fx-quote">
        <div className="fx-price">{fmtPrice(heroPrice, d.currency)}</div>
        <div className="fx-quote-side">
          <Delta chg={heroChg} pct={heroPct} big />
          <div className="fx-when">{hover ? fmtWhen(hover.t, !!chart.intraday) : RANGE_WORD[chart.range] || ""}</div>
        </div>
      </div>
      {d.ext && !hover && is1D && (
        <div className="fx-ext">{d.ext.label}: <b>{fmtPrice(d.ext.price, d.currency)}</b> <Delta chg={d.ext.change} pct={d.ext.change_pct} /></div>
      )}

      <div className="fx-chart-wrap">
        {chart.points?.length ? (
          <PriceChart points={chart.points} base={chartBase} intraday={chart.intraday} onHover={setHover} />
        ) : <div className="fx-empty">No chart data</div>}
        {busy && <div className="fx-chart-busy">LOADING {busy}</div>}
      </div>
      <Ranges list={STOCK_RANGES} cur={chart.range} busy={busy} pick={pick} />
      {err && <div className="fx-err">{err}</div>}

      <div className="fx-tabs">
        {(["overview", hasAnalyst && "analysts", hasEarn && "earnings", "about"].filter(Boolean) as string[]).map((t) => (
          <button key={t} className={`fx-tab ${tab === t ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); setTab(t as any); }}>{t}</button>
        ))}
      </div>

      {tab === "overview" && (
        <>
          <div className="fx-ranges-2">
            <div><label>Day range</label><RangeBar lo={st.day_low} hi={st.day_high} v={d.price} /></div>
            <div><label>52-week range</label><RangeBar lo={st.year_low} hi={st.year_high} v={d.price} /></div>
          </div>
          <div className="fx-stats">
            {d.fund ? (
              <>
                <Stat k="Net assets" v={fmtBig(d.fund.total_assets)} />
                <Stat k="Expense ratio" v={d.fund.expense_ratio != null ? `${d.fund.expense_ratio}%` : "—"} />
                <Stat k="Yield" v={d.fund.yield != null ? `${d.fund.yield}%` : "—"} />
                <Stat k="YTD return" v={fmtPct(d.fund.ytd_return)} t={tone(d.fund.ytd_return)} />
                <Stat k="Category" v={d.fund.category || "—"} />
                <Stat k="Volume" v={fmtBig(st.volume, false)} />
              </>
            ) : (
              <>
                <Stat k="Market cap" v={fmtBig(st.market_cap)} />
                <Stat k="P/E (TTM)" v={fmtNum(st.pe)} />
                <Stat k="Fwd P/E" v={fmtNum(st.fwd_pe)} />
                <Stat k="EPS" v={fmtEps(st.eps)} />
                <Stat k="Div yield" v={st.div_yield != null ? `${st.div_yield}%` : "—"} />
                <Stat k="Beta" v={fmtNum(st.beta)} />
                <Stat k="Volume" v={fmtBig(st.volume, false)} />
                <Stat k="Avg volume" v={fmtBig(st.avg_volume, false)} />
                {st.profit_margin != null && <Stat k="Profit margin" v={`${st.profit_margin}%`} />}
                {st.revenue_growth != null && <Stat k="Rev growth" v={fmtPct(st.revenue_growth, 1)} t={tone(st.revenue_growth)} />}
                <Stat k="Open" v={fmtPrice(st.open)} />
                <Stat k="Prev close" v={fmtPrice(d.prev_close)} />
              </>
            )}
          </div>
          {d.analyst && <AnalystMini a={d.analyst} price={d.price} onMore={() => setTab("analysts")} />}
        </>
      )}
      {tab === "analysts" && d.analyst && <Analysts a={d.analyst} price={d.price} />}
      {tab === "earnings" && <Earnings e={d.earnings} fin={d.financials} />}
      {tab === "about" && <About p={d.profile} website={d.website} name={d.name} />}

      {!!d.news?.length && <News items={d.news} />}
      <div className="fx-foot">
        <span>Yahoo Finance · delayed up to 15 min · as of {new Date((d.as_of || 0) * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
        <button className="hbtn sm hbtn-cyan" onClick={(e) => { e.stopPropagation(); open(`https://finance.yahoo.com/quote/${encodeURIComponent(d.symbol)}`); }}>Yahoo ↗</button>
      </div>
    </div>
  );
}

function Stat({ k, v, t }: { k: string; v: string; t?: string }) {
  return <div className="fx-stat"><label>{k}</label><b className={t}>{v}</b></div>;
}

const REC_LABEL = (k: string) => k.replace(/\b\w/g, (c) => c.toUpperCase()) || "—";
const REC_TONE = (k: string) => (/buy/i.test(k) ? "up" : /sell|under/i.test(k) ? "down" : "flat");

function AnalystMini({ a, price, onMore }: { a: any; price?: number; onMore: () => void }) {
  const up = a.target?.mean && price ? (a.target.mean / price - 1) * 100 : null;
  return (
    <button className="fx-anmini" onClick={(e) => { e.stopPropagation(); onMore(); }}>
      <span className={`fx-rec ${REC_TONE(a.key)}`}>{REC_LABEL(a.key)}</span>
      <span>{a.count} analysts</span>
      {a.target?.mean != null && <span>Target <b>{fmtPrice(a.target.mean)}</b> <em className={tone(up)}>{fmtPct(up, 1)}</em></span>}
      <span className="fx-more">details ›</span>
    </button>
  );
}

function Analysts({ a, price }: { a: any; price?: number }) {
  const dist = a.dist || {};
  const rows: [string, number, string][] = [
    ["Strong buy", dist.strongBuy || 0, "#3dffb0"], ["Buy", dist.buy || 0, "#8dffcf"], ["Hold", dist.hold || 0, "#ffb020"],
    ["Sell", dist.sell || 0, "#ff8a95"], ["Strong sell", dist.strongSell || 0, "#ff4d5e"],
  ];
  const total = rows.reduce((s, r) => s + r[1], 0) || 1;
  const t = a.target || {};
  const lo = Math.min(t.low ?? price ?? 0, price ?? Infinity), hi = Math.max(t.high ?? price ?? 0, price ?? -Infinity);
  const pos = (v?: number | null) => (v == null || hi <= lo ? 0 : ((v - lo) / (hi - lo)) * 100);
  const up = t.mean && price ? (t.mean / price - 1) * 100 : null;
  // consensus gauge: 1 (strong buy) .. 5 (strong sell)
  const g = a.mean != null ? Math.max(0, Math.min(1, (5 - a.mean) / 4)) : null;
  return (
    <div className="fx-analysts">
      <div className="fx-an-top">
        {g != null && (
          <svg viewBox="0 0 120 70" className="fx-gauge">
            <defs><linearGradient id="gg" x1="0" x2="1"><stop offset="0" stopColor={DOWN} /><stop offset="0.5" stopColor="#ffb020" /><stop offset="1" stopColor={UP} /></linearGradient></defs>
            <path d="M10,62 A50,50 0 0 1 110,62" fill="none" stroke="rgba(57,208,255,0.12)" strokeWidth="9" strokeLinecap="round" />
            <path d="M10,62 A50,50 0 0 1 110,62" fill="none" stroke="url(#gg)" strokeWidth="9" strokeLinecap="round" strokeDasharray={`${g * 157} 999`} />
            <line x1="60" y1="62" x2={60 - 40 * Math.cos(g * Math.PI)} y2={62 - 40 * Math.sin(g * Math.PI)} stroke="#fff" strokeWidth="2" strokeLinecap="round" />
            <circle cx="60" cy="62" r="4" fill="#fff" />
          </svg>
        )}
        <div>
          <div className={`fx-rec big ${REC_TONE(a.key)}`}>{REC_LABEL(a.key)}</div>
          <div className="muted small">Consensus of {a.count} analysts{a.mean != null && <> · score {a.mean.toFixed(2)} (1 = strong buy)</>}</div>
        </div>
      </div>
      <div className="fx-dist">
        {rows.map(([k, n, c]) => (
          <div key={k} className="fx-dist-row">
            <span>{k}</span>
            <div className="fx-dist-bar"><i style={{ width: `${(n / total) * 100}%`, background: c, boxShadow: `0 0 10px ${c}66` }} /></div>
            <b>{n}</b>
          </div>
        ))}
      </div>
      {t.mean != null && (
        <div className="fx-targets">
          <div className="fx-tg-head">12-month price target <b>{fmtPrice(t.mean)}</b> <em className={tone(up)}>{fmtPct(up, 1)} vs now</em></div>
          <div className="fx-tg-track">
            <div className="fx-tg-band" style={{ left: `${pos(t.low)}%`, width: `${pos(t.high) - pos(t.low)}%` }} />
            {price != null && <i className="fx-tg-now" style={{ left: `${pos(price)}%` }}><span>Now {fmtPrice(price)}</span></i>}
            <i className="fx-tg-mean" style={{ left: `${pos(t.mean)}%` }}><span>Avg {fmtPrice(t.mean)}</span></i>
          </div>
          <div className="fx-tg-ends"><span>Low {fmtPrice(t.low)}</span><span>High {fmtPrice(t.high)}</span></div>
        </div>
      )}
    </div>
  );
}

function Earnings({ e, fin }: { e: any; fin: any[] }) {
  const hist: any[] = e?.history || [];
  const all = hist.flatMap((h) => [h.actual, h.estimate]).filter((v) => v != null) as number[];
  const lo = Math.min(...all, 0), hi = Math.max(...all, 0.01);
  const y = (v: number) => 100 - ((v - lo) / (hi - lo || 1)) * 84 - 8;
  const maxRev = Math.max(...(fin || []).map((f) => f.revenue || 0), 1);
  const qLabel = (s: string) => { const d = new Date(s + "T00:00:00"); return `Q${Math.floor(d.getMonth() / 3) + 1} '${String(d.getFullYear()).slice(2)}`; };
  return (
    <div className="fx-earn">
      {e?.next && (
        <div className="fx-next">
          <span className="fx-next-tag">NEXT EARNINGS</span>
          <b>{new Date(e.next * 1000).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" })}</b>
          {e.next_estimate?.avg != null && <span className="muted">EPS est. {fmtEps(e.next_estimate.avg)} ({e.next_estimate.analysts} analysts{e.next_estimate.year_ago != null ? `, year ago ${fmtEps(e.next_estimate.year_ago)}` : ""})</span>}
        </div>
      )}
      {!!hist.length && (
        <>
          <div className="fx-sec">EPS · ACTUAL VS ESTIMATE</div>
          <div className="fx-eps">
            <div className="fx-eps-plot">
              {hist.map((h, i) => {
                const beat = h.actual != null && h.estimate != null && h.actual >= h.estimate;
                const left = `${((i + 0.5) / hist.length) * 100}%`;
                return (
                  <div key={i}>
                    {h.estimate != null && <i className="fx-eps-dot est" style={{ left, top: `${y(h.estimate)}%` }} title={`Estimate ${fmtEps(h.estimate)}`} />}
                    {h.actual != null && <i className={`fx-eps-dot act ${beat ? "beat" : "miss"}`} style={{ left, top: `${y(h.actual)}%` }} title={`Actual ${fmtEps(h.actual)}`} />}
                  </div>
                );
              })}
            </div>
            <div className="fx-eps-labels">
              {hist.map((h, i) => {
                const beat = h.actual != null && h.estimate != null && h.actual >= h.estimate;
                return (
                  <div key={i}>
                    <b>{qLabel(h.q)}</b>
                    <span>{h.actual != null ? fmtEps(h.actual) : "—"}</span>
                    <em className={beat ? "up" : "down"}>{h.surprise_pct != null ? `${beat ? "Beat" : "Miss"} ${fmtPct(h.surprise_pct, 1)}` : ""}</em>
                  </div>
                );
              })}
            </div>
          </div>
          <div className="fx-legend"><span><i className="dot solid" />Actual</span><span><i className="dot hollow" />Estimate</span></div>
        </>
      )}
      {!!fin?.length && (
        <>
          <div className="fx-sec">QUARTERLY REVENUE &amp; NET INCOME</div>
          <div className="fx-rev">
            {fin.map((f) => (
              <div key={f.q} className="fx-rev-col">
                <div className="fx-rev-val">{fmtBig(f.revenue)}</div>
                <div className="fx-rev-bars">
                  <i className="rev" style={{ height: `${(f.revenue / maxRev) * 100}%` }} />
                  {f.net_income != null && <i className={`net ${f.net_income < 0 ? "neg" : ""}`} style={{ height: `${(Math.abs(f.net_income) / maxRev) * 100}%` }} />}
                </div>
                <div className="fx-rev-q">{qLabel(f.q)}</div>
              </div>
            ))}
          </div>
          <div className="fx-legend"><span><i className="sq rev" />Revenue</span><span><i className="sq net" />Net income</span></div>
        </>
      )}
    </div>
  );
}

function About({ p, website, name }: { p: any; website?: string; name: string }) {
  if (!p) return null;
  return (
    <div className="fx-about">
      <div className="fx-about-grid">
        {p.sector && <Stat k="Sector" v={p.sector} />}
        {p.industry && <Stat k="Industry" v={p.industry} />}
        {p.hq && <Stat k="Headquarters" v={p.hq} />}
        {p.employees && <Stat k="Employees" v={fmtNum(p.employees, 0)} />}
      </div>
      {p.summary && <p className="fx-summary">{p.summary}</p>}
      {website && <button className="hbtn sm hbtn-cyan" onClick={(e) => { e.stopPropagation(); open(website); }}>{name.split(" ")[0]} website ↗</button>}
    </div>
  );
}

function News({ items }: { items: any[] }) {
  return (
    <div className="fx-news">
      <div className="fx-sec">LATEST NEWS</div>
      {items.slice(0, 5).map((n, i) => (
        <button key={i} className="fx-news-row" onClick={(e) => { e.stopPropagation(); open(n.url); }}>
          {n.thumb ? <img src={n.thumb} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.visibility = "hidden")} /> : <span className="fx-news-ph">◆</span>}
          <div>
            <div className="fx-news-t">{n.title}</div>
            <div className="fx-news-m">{n.publisher}{n.ts ? ` · ${ago(n.ts)}` : ""}{n.related?.length ? <span className="fx-news-tk">{n.related.slice(0, 3).join(" ")}</span> : null}</div>
          </div>
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ COMPARE */
export function StockCompareCard({ card }: { card: Card }) {
  const d = card.data || {};
  const [data, setData] = useState(d);
  const [busy, setBusy] = useState("");
  const [hover, setHover] = useState<{ vals: Record<string, number>; t?: number } | null>(null);
  useEffect(() => setData(card.data || {}), [card.data]);
  const series: any[] = data.series || [];
  const pick = async (r: string) => {
    if (r === data.range || busy) return;
    setBusy(r);
    const res = await Promise.all(series.map((s) => core.rpc("market_chart", { symbol: s.symbol, range: r })));
    setBusy("");
    if (res.every((x) => x.ok)) {
      setData({
        ...data, range: r, intraday: res[0].result.intraday,
        series: series.map((s, i) => {
          const pts = res[i].result.points || [];
          const b = pts[0]?.c;
          const np = b ? pts.map((p: any) => ({ t: p.t, v: Math.round((p.c / b - 1) * 100000) / 1000 })) : [];
          return { ...s, points: np, period_pct: np.length ? np[np.length - 1].v : null };
        }),
      });
    }
  };
  const ranked = useMemo(() => [...series].sort((a, b) => (b.period_pct ?? -1e9) - (a.period_pct ?? -1e9)), [series]);
  const best = ranked[0];
  return (
    <div className="fx fx-compare">
      <div className="fx-cmp-head">
        {series.map((s, i) => {
          const v = hover ? hover.vals[s.symbol] : s.period_pct;
          return (
            <button key={s.symbol} className="fx-cmp-chip" style={{ ["--c" as any]: SERIES_COLORS[i % SERIES_COLORS.length] }} onClick={(e) => { e.stopPropagation(); core.openMarket(s.symbol); }} title={`Open ${s.name}`}>
              <Logo urls={s.logos} label={s.symbol} size={30} />
              <div>
                <b>{s.symbol}</b>
                <span className={tone(v)}>{fmtPct(v, 1)}</span>
              </div>
            </button>
          );
        })}
      </div>
      <div className="fx-when center">{hover?.t ? fmtWhen(hover.t, !!data.intraday) : `Performance · ${RANGE_WORD[data.range] || data.range}`}</div>
      <div className="fx-chart-wrap">
        <CompareChart series={series} intraday={data.intraday} onHover={(vals, t) => setHover(vals ? { vals, t } : null)} />
        {busy && <div className="fx-chart-busy">LOADING {busy}</div>}
      </div>
      <Ranges list={STOCK_RANGES} cur={data.range} busy={busy} pick={pick} />
      {best && best.period_pct != null && series.length > 1 && (
        <div className="fx-winner"><span>🏆</span> <b>{best.name}</b> led {RANGE_WORD[data.range] || ""} at <em className={tone(best.period_pct)}>{fmtPct(best.period_pct, 1)}</em></div>
      )}
      <table className="fx-cmp-table">
        <thead><tr><th /><th>Price</th><th>Today</th><th>{data.range}</th><th>Mkt cap</th><th>P/E</th></tr></thead>
        <tbody>
          {series.map((s, i) => (
            <tr key={s.symbol} onClick={(e) => { e.stopPropagation(); core.openMarket(s.symbol); }}>
              <td><i className="fx-sw" style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} />{s.symbol}</td>
              <td>{fmtPrice(s.price)}</td>
              <td className={tone(s.change_pct)}>{fmtPct(s.change_pct)}</td>
              <td className={tone(s.period_pct)}>{fmtPct(s.period_pct, 1)}</td>
              <td>{fmtBig(s.market_cap)}</td>
              <td>{fmtNum(s.pe, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ MARKET OVERVIEW */
export function MarketCard({ card }: { card: Card }) {
  const d = card.data || {};
  const [mv, setMv] = useState<"gainers" | "losers" | "active">("gainers");
  const [stateLabel, stateCls] = STATE_LABEL[d.market_state] || ["", ""];
  const cryptoFirst = d.focus === "crypto";
  const idx: any[] = d.indices || [];
  const main = idx.filter((x) => ["^GSPC", "^IXIC", "^DJI", "^RUT"].includes(x.symbol));
  const other = idx.filter((x) => !["^GSPC", "^IXIC", "^DJI", "^RUT"].includes(x.symbol));
  const spx = idx.find((x) => x.symbol === "^GSPC");
  const sectors: any[] = [...(d.sectors || [])].sort((a, b) => (b.change_pct ?? 0) - (a.change_pct ?? 0));
  const maxAbs = Math.max(...sectors.map((s) => Math.abs(s.change_pct || 0)), 0.5);
  const crypto = <CryptoTable rows={d.crypto || []} />;
  return (
    <div className="fx fx-market">
      <div className="fx-mk-head">
        <div>
          <div className="fx-mk-title">{cryptoFirst ? "CRYPTO" : new Date((d.as_of || Date.now() / 1000) * 1000).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }).toUpperCase()}</div>
          {spx && !cryptoFirst && <div className="fx-mk-sub">S&P 500 <Delta chg={spx.change} pct={spx.change_pct} /></div>}
        </div>
        {stateLabel && <span className={`fx-state ${stateCls}`}><i />{stateLabel}</span>}
      </div>
      {cryptoFirst && crypto}
      <div className="fx-idx-grid">
        {main.map((x) => (
          <button key={x.symbol} className={`fx-idx ${tone(x.change_pct)}`} onClick={(e) => { e.stopPropagation(); core.openMarket(x.symbol); }}>
            <div className="fx-idx-top"><span>{x.label}</span><em className={tone(x.change_pct)}>{fmtPct(x.change_pct)}</em></div>
            <div className="fx-idx-px">{fmtNum(x.price, 2)}</div>
            <Spark values={x.spark} base={x.prev_close} w={150} h={36} />
          </button>
        ))}
      </div>
      <div className="fx-tape">
        {other.map((x) => (
          <button key={x.symbol} className="fx-tape-i" onClick={(e) => { e.stopPropagation(); x.symbol === "BTC-USD" ? core.openMarket("bitcoin", "crypto") : core.openMarket(x.symbol); }}>
            <span>{x.label}</span><b>{x.symbol === "^TNX" ? `${fmtNum(x.price, 3)}%` : fmtNum(x.price, 2)}</b><em className={tone(x.change_pct)}>{fmtPct(x.change_pct)}</em>
          </button>
        ))}
      </div>
      {!!sectors.length && (
        <>
          <div className="fx-sec">SECTORS · TODAY</div>
          <div className="fx-heat">
            {sectors.map((s) => {
              const a = Math.min(1, Math.abs(s.change_pct || 0) / maxAbs);
              const pos = (s.change_pct || 0) >= 0;
              const bg = pos ? `rgba(61,255,176,${0.08 + a * 0.42})` : `rgba(255,77,94,${0.08 + a * 0.42})`;
              return (
                <button key={s.symbol} className="fx-heat-cell" style={{ background: bg, borderColor: pos ? `rgba(61,255,176,${0.2 + a * 0.5})` : `rgba(255,77,94,${0.2 + a * 0.5})` }} onClick={(e) => { e.stopPropagation(); core.openMarket(s.symbol); }}>
                  <span>{s.label}</span><b>{fmtPct(s.change_pct)}</b>
                </button>
              );
            })}
          </div>
        </>
      )}
      <div className="fx-sec fx-sec-row">
        <span>MOVERS</span>
        <div className="fx-seg">
          {(["gainers", "losers", "active"] as const).map((k) => (
            <button key={k} className={mv === k ? "on" : ""} onClick={(e) => { e.stopPropagation(); setMv(k); }}>{k === "active" ? "Most active" : k[0].toUpperCase() + k.slice(1)}</button>
          ))}
        </div>
      </div>
      <div className="fx-movers">
        {(d.movers?.[mv] || []).slice(0, 6).map((m: any) => (
          <button key={m.symbol} className="fx-mover" onClick={(e) => { e.stopPropagation(); core.openMarket(m.symbol); }}>
            <Logo urls={m.logos} label={m.symbol} size={28} />
            <div className="fx-mover-id"><b>{m.symbol}</b><span title={m.name}>{m.name}</span></div>
            <div className="fx-mover-px"><b>{fmtPrice(m.price)}</b><em className={tone(m.change_pct)}>{fmtPct(m.change_pct)}</em></div>
          </button>
        ))}
      </div>
      {!cryptoFirst && !!d.crypto?.length && (<><div className="fx-sec">CRYPTO</div>{crypto}</>)}
      <div className="fx-foot"><span>Yahoo Finance · CoinGecko · as of {new Date((d.as_of || 0) * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span></div>
    </div>
  );
}

function CryptoTable({ rows }: { rows: any[] }) {
  return (
    <div className="fx-crypto-rows">
      {rows.slice(0, 8).map((c) => (
        <button key={c.id} className="fx-crow" onClick={(e) => { e.stopPropagation(); core.openMarket(c.id, "crypto"); }}>
          <span className="fx-rank">{c.rank}</span>
          <Logo urls={[c.logo]} label={c.symbol} size={24} round />
          <div className="fx-crow-id"><b>{c.name}</b><span>{c.symbol}</span></div>
          <Spark values={c.spark} w={70} h={22} />
          <div className="fx-crow-px"><b>{fmtPrice(c.price)}</b><em className={tone(c.change_24h)}>{fmtPct(c.change_24h)}</em></div>
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ CRYPTO */
export function CryptoCard({ card }: { card: Card }) {
  const d = card.data || {};
  const { chart, busy, err, pick } = useRangeChart(d.chart || { range: "7D", points: [] }, "crypto_chart", (r) => ({ coin: d.id, range: r }));
  const [hover, setHover] = useState<{ t: number; c: number; pct: number | null } | null>(null);
  const ch = d.changes || {};
  const periodPct = { "1D": ch["24h"], "7D": ch["7d"], "1M": ch["30d"], "1Y": ch["1y"] }[chart.range as string] ?? chart.period_change_pct;
  const pct = hover ? hover.pct : periodPct;
  const sup = d.supply || {};
  const supPct = sup.max ? (sup.circulating / sup.max) * 100 : null;
  return (
    <div className={`fx fx-crypto ${(pct ?? 0) >= 0 ? "is-up" : "is-down"}`}>
      <div className="fx-hero">
        <Logo urls={[d.logo]} label={d.symbol} size={52} round />
        <div className="fx-id">
          <div className="fx-name">{d.name}</div>
          <div className="fx-sub"><b>{d.symbol}</b>{d.rank && <span className="fx-rank-badge">#{d.rank}</span>}{(d.categories || []).slice(0, 2).map((c: string) => <span key={c} className="fx-cat">{c}</span>)}</div>
        </div>
      </div>
      <div className="fx-quote">
        <div className="fx-price">{fmtPrice(hover ? hover.c : chart.range === "1D" || chart.range === "7D" ? d.price : chart.points?.[chart.points.length - 1]?.c ?? d.price)}</div>
        <div className="fx-quote-side">
          <Delta pct={pct} big />
          <div className="fx-when">{hover ? fmtWhen(hover.t, !!chart.intraday) : RANGE_WORD[chart.range] || ""}</div>
        </div>
      </div>
      <div className="fx-chips">
        {(["1h", "24h", "7d", "30d", "1y"] as const).map((k) => (
          <span key={k} className={`fx-chip ${tone(ch[k])}`}><label>{k}</label>{fmtPct(ch[k], 1)}</span>
        ))}
      </div>
      <div className="fx-chart-wrap">
        {chart.points?.length ? <PriceChart points={chart.points} base={chart.base} intraday={chart.intraday} onHover={setHover} /> : <div className="fx-empty">No chart data</div>}
        {busy && <div className="fx-chart-busy">LOADING {busy}</div>}
      </div>
      <Ranges list={CRYPTO_RANGES} cur={chart.range} busy={busy} pick={pick} />
      {err && <div className="fx-err">{err}</div>}
      <div className="fx-ranges-2"><div><label>24h range</label><RangeBar lo={d.low_24h} hi={d.high_24h} v={d.price} left={fmtPrice(d.low_24h)} right={fmtPrice(d.high_24h)} /></div></div>
      <div className="fx-stats">
        <Stat k="Market cap" v={fmtBig(d.market_cap)} />
        <Stat k="24h volume" v={fmtBig(d.volume)} />
        <Stat k="Fully diluted" v={fmtBig(d.fdv)} />
        <Stat k="Circulating" v={`${fmtBig(sup.circulating, false)} ${d.symbol}`} />
        <Stat k="Max supply" v={sup.max ? `${fmtBig(sup.max, false)} ${d.symbol}` : "∞"} />
        <Stat k="All-time high" v={fmtPrice(d.ath?.price)} />
      </div>
      {supPct != null && (
        <div className="fx-supply"><label>Supply mined</label><div className="fx-supply-bar"><i style={{ width: `${supPct}%` }} /></div><b>{supPct.toFixed(1)}%</b></div>
      )}
      {d.ath?.price != null && (
        <div className="fx-ath"><span>ATH {fmtPrice(d.ath.price)} on {d.ath.date}</span><em className={tone(d.ath.pct)}>{fmtPct(d.ath.pct, 1)} from ATH</em></div>
      )}
      {d.description && <p className="fx-summary">{d.description}</p>}
      <div className="fx-foot">
        <span>CoinGecko · as of {new Date((d.as_of || 0) * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
        <div style={{ display: "flex", gap: 6 }}>
          {d.homepage && <button className="hbtn sm" onClick={(e) => { e.stopPropagation(); open(d.homepage); }}>Website ↗</button>}
          <button className="hbtn sm hbtn-cyan" onClick={(e) => { e.stopPropagation(); open(`https://www.coingecko.com/en/coins/${d.id}`); }}>CoinGecko ↗</button>
        </div>
      </div>
    </div>
  );
}
