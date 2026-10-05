import { useEffect, useState } from "react";
import { usePlayer } from "../cards/MediaCards";
import { useStore } from "../state/store";
import type { Card } from "../types";

type Vid = { video_id: string; title: string; channel: string; thumb: string; url: string };
type Tube = { cardId: string; results: Vid[]; idx: number; at: number };

/** HoloTube: a video he asks for materializes over the orb and plays there. ⤢ opens it in the expanded display,
    ⤓ docks it into the sidebar card at the same timestamp, × stops it. */
export function HoloTube() {
  const [t, setT] = useState<Tube | null>(null);
  const [leaving, setLeaving] = useState<"" | "dock" | "close">("");
  useEffect(() => {
    const on = (e: Event) => { const d = (e as CustomEvent).detail; setLeaving(""); setT({ ...d, at: Date.now() }); };
    const off = () => setT(null);
    window.addEventListener("jarvis:tube", on);
    window.addEventListener("jarvis:tube-close", off);
    return () => { window.removeEventListener("jarvis:tube", on); window.removeEventListener("jarvis:tube-close", off); };
  }, []);
  useEffect(() => { window.dispatchEvent(new CustomEvent("jarvis:tube-state", { detail: { open: !!t, cardId: t?.cardId } })); }, [t]);
  // the sidebar card was closed -> stop here too
  const alive = useStore((s) => (t ? s.cards.some((c) => c.id === t.cardId) : true));
  useEffect(() => { if (!alive) setT(null); }, [alive]);
  if (!t) return null;
  return <Stage key={t.at} t={t} leaving={leaving} setLeaving={setLeaving} done={() => setT(null)} next={(i) => setT({ ...t, idx: i, at: Date.now() })} />;
}

function Stage({ t, leaving, setLeaving, done, next }: { t: Tube; leaving: string; setLeaving: (s: "" | "dock" | "close") => void; done: () => void; next: (i: number) => void }) {
  const v = t.results[t.idx];
  const pseudo = { id: "holotube", kind: "video", title: "", account: null, createdAt: 0, data: {} } as unknown as Card;
  const p = usePlayer(pseudo, "video", v?.video_id || "", {
    next: () => t.idx + 1 < t.results.length && next(t.idx + 1),
    previous: () => t.idx > 0 && next(t.idx - 1),
  });
  const handoff = (max: boolean) => {
    // Kill this player FIRST (unmount it) and only then start the sidebar one; a late PLAYING event from this
    // iframe would otherwise re-activate it and pause the new player (frozen video / audio from the wrong one).
    const at = Math.floor(p.info.time || 0);
    p.cmd("stopVideo");
    done();
    const results = [v, ...t.results.filter((_, j) => j !== t.idx)];
    setTimeout(() => {
      const s = useStore.getState();
      s.set({ cards: s.cards.map((c) => (c.id === t.cardId ? { ...c, openMax: max || c.openMax, data: { ...c.data, stage: "card", results, current: 0, start: at, paused: false } } : c)) });
    }, 120);
  };
  const close = () => { p.cmd("stopVideo"); setLeaving("close"); setTimeout(done, 500); };
  if (!v) return null;
  return (
    <div className={`ht ${leaving}`}>
      <div className="ht-glow" />
      <div className="ht-frame">
        <div className="ht-scan" />
        <iframe ref={p.frame} src={p.src} title="YouTube" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
      </div>
      <div className="ht-bar">
        <span className="ht-t"><b>▶</b> {p.info.title || v.title}<em>{v.channel}</em></span>
        <button title="Expand" onClick={() => handoff(true)}>⤢</button>
        <button title="Minimize to Displays" onClick={() => handoff(false)}>⤓</button>
        <button title="Close" onClick={close}>×</button>
      </div>
    </div>
  );
}
