/** Video analysis display: player (H.264 proxy, so iPhone HEVC / AVI / WMV / MKV all play), what JARVIS saw
 *  (summary, timeline, on-screen text, issues, steps, speech) and follow-up Q&A. Every timestamp seeks the player. */
import { useEffect, useRef, useState } from "react";
import { core } from "../ws/core";
import type { Card } from "../types";

const stop = (e: React.SyntheticEvent) => e.stopPropagation();
type Moment = { t: string; s: number | null; [k: string]: any };
const STATUS: Record<string, string> = {
  preparing: "Converting video…", uploading: "Uploading to vision…", analyzing: "Watching the video…",
  thinking: "Re-watching for your question…",
};
const fmtBytes = (n: number) => n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n > 1e6 ? `${(n / 1e6).toFixed(0)} MB` : `${Math.round(n / 1e3)} KB`;
const fmtDur = (s: number) => { s = Math.round(s || 0); return s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };

export function VideoAnalysisCard({ card, expanded }: { card: Card; expanded?: boolean }) {
  const v = card.data?.video || {};
  const a = v.analysis;
  const info = v.info || {};
  const ref = useRef<HTMLVideoElement>(null);
  const [tab, setTab] = useState<"summary" | "timeline" | "text" | "steps">("summary");
  const [q, setQ] = useState("");
  const [now, setNow] = useState(0);
  const busy = ["preparing", "uploading", "analyzing", "thinking"].includes(v.status);
  const portrait = info.orientation === "portrait";

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const f = () => setNow(el.currentTime);
    el.addEventListener("timeupdate", f);
    return () => el.removeEventListener("timeupdate", f);
  }, [v.proxy]);

  const seek = (s: number | null) => {
    const el = ref.current;
    if (el == null || s == null) return;
    el.currentTime = s;
    el.play().catch(() => {});
  };
  const T = ({ m }: { m: Moment }) => (
    <button className={`va-t ${m.s != null && now >= m.s && now < m.s + 4 ? "on" : ""}`} disabled={m.s == null}
      onClick={(e) => { stop(e); seek(m.s); }}>{m.t}</button>
  );
  const ask = (e: React.FormEvent) => {
    e.preventDefault();
    const text = q.trim();
    if (!text) return;
    core.sendText(text);   // a normal turn: JARVIS answers out loud and this display updates with the answer
    setQ("");
  };

  return (
    <div className={`va ${expanded ? "xl" : ""} ${portrait ? "portrait" : ""}`} onClick={stop}>
      <div className="va-media">
        {v.proxy ? (
          <video ref={ref} src={core.videoUrl(v.id)} controls playsInline preload="metadata" />
        ) : (
          <div className="va-wait"><span className="ws-spin" />{STATUS[v.status] || "Preparing…"}</div>
        )}
        {v.thumbs?.length > 0 && (
          <div className="va-strip">
            {v.thumbs.map((t: any) => (
              <button key={t.i} title={t.t} onClick={() => seek(t.s)}>
                <img src={core.videoThumbUrl(v.id, t.i)} alt="" loading="lazy" /><span>{t.t}</span>
              </button>
            ))}
          </div>
        )}
        <div className="va-meta">
          {[v.filename, info.duration ? fmtDur(info.duration) : "", info.width ? `${info.width}×${info.height}` : "",
            info.device || info.platform || "", info.vcodec ? `${String(info.vcodec).toUpperCase()}${info.hdr ? " HDR" : ""}` : "",
            info.size ? fmtBytes(info.size) : ""].filter(Boolean).join(" · ")}
        </div>
      </div>

      <div className="va-body">
        {v.status === "error" && <div className="va-err">Couldn't read this video: {v.error}</div>}
        {busy && <div className="va-busy"><span className="ws-spin" />{STATUS[v.status]}{v.pending_q ? ` “${v.pending_q}”` : ""}</div>}
        {a && (
          <>
            <div className="va-head">
              <div className="va-title">{a.title}</div>
              <div className="va-tags">
                {[String(a.type || "").replace(/_/g, " "), a.platform, ...(a.apps || []).slice(0, 4)].filter(Boolean).map((x: string, i: number) => <span key={i}>{x}</span>)}
              </div>
            </div>
            <div className="va-tabs">
              {(["summary", "timeline", "text", "steps"] as const).filter((k) => k !== "steps" || a.steps?.length).map((k) => (
                <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                  {{ summary: "SUMMARY", timeline: `TIMELINE · ${a.timeline?.length || 0}`, text: "ON SCREEN", steps: `STEPS · ${a.steps?.length}` }[k]}
                </button>
              ))}
            </div>
            {tab === "summary" && (
              <div className="va-sec">
                <p className="va-sum">{a.summary}</p>
                {a.issues?.length > 0 && <>
                  <div className="fx-sec">ISSUES</div>
                  {a.issues.map((m: Moment, i: number) => <div key={i} className="va-row va-issue"><T m={m} /><span>{m.issue}</span></div>)}
                </>}
                {a.takeaways?.length > 0 && <>
                  <div className="fx-sec">TAKEAWAYS</div>
                  <ul className="va-list">{a.takeaways.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
                </>}
                {a.speech && !/^no speech/i.test(a.speech) && <>
                  <div className="fx-sec">AUDIO</div>
                  <p className="va-speech">“{a.speech}”</p>
                </>}
              </div>
            )}
            {tab === "timeline" && (
              <div className="va-sec">{(a.timeline || []).map((m: Moment, i: number) => <div key={i} className="va-row"><T m={m} /><span>{m.event}</span></div>)}</div>
            )}
            {tab === "text" && (
              <div className="va-sec">
                {(a.on_screen_text || []).length === 0 && <div className="muted small">No notable on-screen text.</div>}
                {(a.on_screen_text || []).map((m: Moment, i: number) => <div key={i} className="va-row"><T m={m} /><pre>{m.text}</pre></div>)}
              </div>
            )}
            {tab === "steps" && (
              <ol className="va-sec va-steps">{(a.steps || []).map((x: string, i: number) => <li key={i}>{x.replace(/^\d+[.)]\s*/, "")}</li>)}</ol>
            )}
          </>
        )}
        {v.qa?.length > 0 && (
          <div className="va-qa">
            <div className="fx-sec">YOUR QUESTIONS</div>
            {[...v.qa].reverse().slice(0, 4).map((x: any, i: number) => (
              <div key={i} className="va-qa-item">
                <div className="va-q">{x.q}</div>
                <div className="va-a">{x.a}</div>
                {x.moments?.length > 0 && <div className="va-moments">{x.moments.map((m: Moment, j: number) => <span key={j} title={m.note}><T m={m} /></span>)}</div>}
              </div>
            ))}
          </div>
        )}
        <form className="va-ask" onSubmit={ask}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about this video…" disabled={v.status === "error"} />
          <button className="hbtn sm hbtn-cyan" disabled={!q.trim()}>Ask</button>
        </form>
      </div>
    </div>
  );
}
