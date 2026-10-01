import { useEffect, useRef, useState } from "react";
import { useStore } from "../state/store";
import type { Card } from "../types";
import { attachYT, embedUrl, fmtTime, media, type YTInfo } from "../media/bus";

/* MUSIC (YouTube Music queue: art + player, progress, controls, volume, up next) and VIDEO (YouTube player +
   the other results). Both play through YouTube's embedded player; voice commands reach them via the media bus. */

const open = (u?: string) => u && window.jarvis?.open(u);
const stop = (e: React.MouseEvent | React.PointerEvent) => e.stopPropagation();

// YouTube player states
const ENDED = 0, PLAYING = 1, PAUSED = 2, BUFFERING = 3;

type Track = { video_id: string; title: string; artist: string; album: string; duration: number | null; thumb: string };

function usePlayer(card: Card, kind: "music" | "video", firstId: string, handlers: { next?: () => void; previous?: () => void; onEnded?: () => void; onError?: (code: number) => void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const api = useRef<ReturnType<typeof attachYT> | null>(null);
  const [info, setInfo] = useState<YTInfo>({ state: -1, time: 0, duration: 0, volume: media.volume, muted: false, title: "" });
  const h = useRef(handlers);
  h.current = handlers;
  const [src] = useState(() => embedUrl(firstId, { controls: kind === "video" }));

  useEffect(() => {
    const f = frame.current;
    if (!f) return;
    const a = attachYT(
      f,
      (i) => setInfo((p) => ({ ...p, ...i })),
      (ev, v) => {
        if (ev === "ready") { media.activate(card.id); a.command("playVideo"); }
        if (ev === "state" && v === PLAYING && !media.isActive(card.id)) media.activate(card.id);
        if (ev === "state" && v === ENDED) h.current.onEnded?.();
        if (ev === "error") h.current.onError?.(v ?? 0);
      },
    );
    api.current = a;
    media.register({
      id: card.id, kind,
      command: a.command,
      next: () => h.current.next?.(),
      previous: () => h.current.previous?.(),
      stop: () => { a.command("stopVideo"); useStore.getState().removeCard(card.id); },
      playing: () => infoRef.current.state === PLAYING || infoRef.current.state === BUFFERING,
    });
    return () => { a.detach(); media.unregister(card.id); };
  }, [card.id]);

  const infoRef = useRef(info);
  infoRef.current = info;
  const cmd = (func: string, args?: unknown[]) => api.current?.command(func, args);
  return { frame, src, info, cmd };
}

function Progress({ info, seek }: { info: YTInfo; seek: (s: number) => void }) {
  const pct = info.duration ? Math.min(100, (info.time / info.duration) * 100) : 0;
  return (
    <div className="md-prog">
      <span>{fmtTime(info.time)}</span>
      <div className="md-bar" onClick={(e) => { stop(e); const r = e.currentTarget.getBoundingClientRect(); seek(((e.clientX - r.left) / r.width) * info.duration); }}>
        <i style={{ width: `${pct}%` }} /><b style={{ left: `${pct}%` }} />
      </div>
      <span>{fmtTime(info.duration)}</span>
    </div>
  );
}

function Volume() {
  const [v, setV] = useState(media.volume);
  const [muted, setMuted] = useState(media.muted);
  useEffect(() => media.subscribeVolume(() => { setV(media.volume); setMuted(media.muted); }), []);
  return (
    <div className="md-vol" onClick={stop}>
      <button className="md-ic" title={muted ? "Unmute" : "Mute"} onClick={() => { media.control(muted ? "unmute" : "mute"); setMuted(!muted); }}>
        {muted || v === 0 ? "🔇" : v < 40 ? "🔈" : "🔊"}
      </button>
      <input type="range" min={0} max={100} value={muted ? 0 : v} onChange={(e) => { const n = Number(e.target.value); setV(n); setMuted(false); media.setVolume(n); }} />
    </div>
  );
}

/* ------------------------------------------------------------------ MUSIC */
export function MusicCard({ card }: { card: Card }) {
  const d = card.data || {};
  const queue: Track[] = d.queue || [];
  const [idx, setIdx] = useState(0);
  const idxRef = useRef(0);
  const toast = useStore((s) => s.toast);
  const go = (i: number) => {
    if (i < 0 || i >= queue.length) return;
    idxRef.current = i;
    setIdx(i);
    p.cmd("loadVideoById", [queue[i].video_id, 0]);
  };
  const p = usePlayer(card, "music", queue[0]?.video_id || "", {
    next: () => go(idxRef.current + 1),
    previous: () => (p.info.time > 4 ? p.cmd("seekTo", [0, true]) : go(idxRef.current - 1)),
    onEnded: () => go(idxRef.current + 1),
    onError: () => {
      const t = queue[idxRef.current];
      toast({ text: `“${t?.title ?? "That track"}” can't play here; skipping.`, error: true });
      go(idxRef.current + 1);
    },
  });
  const t = queue[idx] || queue[0];
  if (!t) return <div className="fx-empty">Nothing to play.</div>;
  const playing = p.info.state === PLAYING || p.info.state === BUFFERING;
  const art = t.thumb || d.art;

  return (
    <div className="md md-music">
      <div className="md-glow" style={{ backgroundImage: art ? `url("${art}")` : undefined }} />
      <div className="md-top">
        <div className="md-art">
          <iframe ref={p.frame} src={p.src} title="YouTube Music player" allow="autoplay; encrypted-media" referrerPolicy="strict-origin-when-cross-origin" />
        </div>
        <div className="md-meta">
          <label>{({ song: "SONG RADIO", album: "ALBUM", playlist: "PLAYLIST", artist: "ARTIST" } as Record<string, string>)[d.mode] ?? "MUSIC"} · {idx + 1}/{queue.length}</label>
          <div className="md-title" title={t.title}>{t.title}</div>
          <div className="md-artist">{t.artist}</div>
          {t.album && <div className="md-album">{t.album}</div>}
          {d.mode !== "song" && <div className="md-src">{d.title}{d.subtitle ? ` · ${d.subtitle}` : ""}</div>}
        </div>
      </div>
      <Progress info={p.info} seek={(s) => p.cmd("seekTo", [s, true])} />
      <div className="md-ctl" onClick={stop}>
        <button className="md-ic" title="Previous" onClick={() => (p.info.time > 4 ? p.cmd("seekTo", [0, true]) : go(idx - 1))}>⏮</button>
        <button className="md-play" title={playing ? "Pause" : "Play"} onClick={() => { if (playing) p.cmd("pauseVideo"); else { media.activate(card.id); p.cmd("playVideo"); } }}>{playing ? "❚❚" : "▶"}</button>
        <button className="md-ic" title="Next" onClick={() => go(idx + 1)} disabled={idx >= queue.length - 1}>⏭</button>
        <Volume />
        <button className="hbtn sm hbtn-cyan md-open" onClick={() => open(`https://music.youtube.com/watch?v=${t.video_id}`)}>YouTube Music ↗</button>
      </div>
      {queue.length > 1 && (
        <div className="md-q">
          <div className="fx-sec">UP NEXT</div>
          {queue.slice(idx + 1, idx + 9).map((x, j) => (
            <button key={x.video_id} className="md-q-row" onClick={(e) => { stop(e); go(idx + 1 + j); }}>
              {x.thumb ? <img src={x.thumb} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.visibility = "hidden")} /> : <span className="md-q-ph">♪</span>}
              <div><div className="md-q-t">{x.title}</div><div className="md-q-a">{x.artist}</div></div>
              <span className="md-q-d">{x.duration ? fmtTime(x.duration) : ""}</span>
            </button>
          ))}
          {queue.length - idx - 9 > 0 && <div className="md-q-more">+{queue.length - idx - 9} more</div>}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ VIDEO */
type Vid = { video_id: string; title: string; channel: string; duration: number | null; views: number | null; live: boolean; thumb: string; url: string };

function views(n: number | null) {
  if (!n) return "";
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B views`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M views`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}K views`;
  return `${n} views`;
}

export function VideoCard({ card }: { card: Card }) {
  const d = card.data || {};
  const results: Vid[] = d.results || [];
  const [idx, setIdx] = useState(0);
  const idxRef = useRef(0);
  const toast = useStore((s) => s.toast);
  const go = (i: number) => {
    if (i < 0 || i >= results.length) return;
    idxRef.current = i;
    setIdx(i);
    p.cmd("loadVideoById", [results[i].video_id, 0]);
  };
  const p = usePlayer(card, "video", results[0]?.video_id || "", {
    next: () => go(idxRef.current + 1),
    previous: () => go(idxRef.current - 1),
    onError: (code) => {
      const v = results[idxRef.current];
      toast({ text: code === 101 || code === 150 || code === 153 ? `“${v?.title ?? "That video"}” doesn't allow playing outside YouTube.` : `Video error ${code}.`, error: true });
    },
  });
  const v = results[idx] || results[0];
  if (!v) return <div className="fx-empty">No video.</div>;
  return (
    <div className="md md-video">
      <div className="md-screen">
        <iframe ref={p.frame} src={p.src} title="YouTube player" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
      </div>
      <div className="md-vmeta">
        <div className="md-vtitle">{p.info.title || v.title}</div>
        <div className="md-vsub">{[v.channel, views(v.views), v.live ? "LIVE" : ""].filter(Boolean).join(" · ")}</div>
      </div>
      <div className="md-ctl" onClick={stop}>
        <Volume />
        <button className="hbtn sm hbtn-cyan md-open" onClick={() => open(v.url)}>YouTube ↗</button>
      </div>
      {results.length > 1 && (
        <div className="md-vlist">
          <div className="fx-sec">MORE RESULTS{d.query ? ` · “${d.query}”` : ""}</div>
          <div className="md-vgrid">
            {results.map((x, j) => j !== idx && (
              <button key={x.video_id} className="md-vrow" onClick={(e) => { stop(e); go(j); }}>
                <span className="md-vth">
                  <img src={x.thumb} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.visibility = "hidden")} />
                  {x.live ? <em className="live">LIVE</em> : x.duration ? <em>{fmtTime(x.duration)}</em> : null}
                </span>
                <span className="md-vt">{x.title}</span>
                <span className="md-vc">{[x.channel, views(x.views)].filter(Boolean).join(" · ")}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
