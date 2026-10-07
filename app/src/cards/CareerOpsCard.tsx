import { useContext, useEffect, useState } from "react";
import type { Card } from "../types";
import { core } from "../ws/core";
import { useStore } from "../state/store";
import { MaxCtx } from "./HoloCard";
import { WebAppCard } from "./WebAppCard";

/* career-ops: sidebar = pipeline summary + quick add; expanded = the full career-ops web app (local, loopback). */
const stop = (e: React.SyntheticEvent) => e.stopPropagation();
const STATUS_COLOR: Record<string, string> = { Evaluated: "#39d0ff", Applied: "#b388ff", Responded: "#ffc857", Interview: "#3dffb0", Offer: "#3dffb0", Rejected: "#ff6b6b", Discarded: "#6b7079", SKIP: "#6b7079" };

export function CareerOpsCard({ card }: { card: Card }) {
  const mx = useContext(MaxCtx);
  const [d, setD] = useState<any>(card.data?.applications != null ? card.data : null);
  const [err, setErr] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => core.workspace("careerops_summary", {}).then((r) => (r.ok ? (setD(r.result), setErr("")) : setErr(r.error || "career-ops failed to start")));
  useEffect(() => { load(); const t = setInterval(load, 60_000); return () => clearInterval(t); }, []);
  if (mx?.max) {
    if (!d) return <div className="ks-load"><span className="cx-spin" /> Starting career-ops…</div>;
    return <WebAppCard card={{ ...card, data: { url: d.url + (card.data?.view ? `/${card.data.view}` : ""), partition: "persist:career-ops" } } as Card} />;
  }
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    const r = await core.workspace("careerops_add", { url });
    setBusy(false);
    if (r.ok) { useStore.getState().toast({ text: r.result.status === "queued" ? "Queued in career-ops inbox." : "Already in the inbox." }); setUrl(""); load(); }
    else useStore.getState().toast({ text: r.error || "Couldn't add it", error: true });
  };
  if (err) return <div className="ks-warn">{err}</div>;
  if (!d) return <div className="ks-load"><span className="cx-spin" /> Starting career-ops…</div>;
  const open = () => mx?.setMax(true);
  return (
    <div className="co" onClick={stop}>
      {d.onboarding && (
        <div className="co-onb" onClick={open}>
          <b>Finish setup</b> — career-ops needs your CV and target roles ({d.missing.join(", ")}). Open it to set up ›
        </div>
      )}
      <div className="co-stats">
        <div><b>{d.applications}</b><span>tracked</span></div>
        <div><b>{d.inbox}</b><span>in inbox</span></div>
        {Object.entries(d.by_status as Record<string, number>).slice(0, 4).map(([s, n]) => <div key={s}><b style={{ color: STATUS_COLOR[s] || "#fff" }}>{n}</b><span>{s}</span></div>)}
      </div>
      <form className="cs-bar" onSubmit={add}>
        <div className="cs-q"><span>＋</span><input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Paste a job posting URL to evaluate" /></div>
        <button className="cs-go" disabled={busy}>{busy ? <span className="cx-spin" /> : "Queue"}</button>
      </form>
      {d.top.length > 0 && <div className="ks-sec">TOP MATCHES</div>}
      {d.top.map((a: any) => (
        <div key={a.n} className="co-row" onClick={open}>
          <span className="co-score">{a.score}</span>
          <div><div className="ks-tt">{a.role}</div><div className="muted small">{a.company}{a.location ? ` · ${a.location}` : ""}</div></div>
          <span className="co-st" style={{ color: STATUS_COLOR[a.status] || "#cfefff" }}>{a.status}</span>
        </div>
      ))}
      {d.inbox_items.length > 0 && <div className="ks-sec">INBOX (to evaluate)</div>}
      {d.inbox_items.map((x: any, i: number) => <div key={i} className="co-in">{typeof x === "string" ? x : x.company || x.url}{x.role ? ` · ${x.role}` : ""}</div>)}
      {!d.applications && !d.inbox && !d.onboarding && <div className="muted">No jobs yet. Paste a URL above or open career-ops to scan portals.</div>}
      <button className="hbtn hbtn-cyan co-open" onClick={open}>Open career-ops ⤢</button>
    </div>
  );
}
