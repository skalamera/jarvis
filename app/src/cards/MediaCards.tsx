import { useEffect, useRef, useState } from "react";
import { useStore } from "../state/store";
import type { Card } from "../types";
import { attachYT, embedUrl, fmtTime, media, type YTInfo } from "../media/bus";
import { core } from "../ws/core";

/* MUSIC (YouTube Music queue: art + player, progress, controls, volume, up next) and VIDEO (YouTube player +
   the other results). Both play through YouTube's embedded player; voice commands reach them via the media bus. */

const open = (u?: string) => u && window.jarvis?.open(u);
const stop = (e: React.MouseEvent | React.PointerEvent) => e.stopPropagation();

// YouTube player states
const ENDED = 0, PLAYING = 1, PAUSED = 2, BUFFERING = 3;

type Artist = { name: string; id?: string | null };
type Track = { video_id: string; title: string; artist: string; artists?: Artist[]; album: string; album_id?: string | null; duration: number | null; thumb: string };
type View = { type: "artist"; id?: string | null; name: string } | { type: "album"; id: string; title: string };

/** Artist names as links. Each opens that artist's page inside the card; playback keeps going. */
function ArtistLinks({ t, onOpen, className }: { t: { artist: string; artists?: Artist[] }; onOpen: (v: View) => void; className?: string }) {
  const list = t.artists?.length ? t.artists : t.artist ? [{ name: t.artist }] : [];
  return (
    <span className={className}>
      {list.map((a, i) => (
        <span key={a.name + i}>
          {i > 0 && ", "}
          <a className="md-link" role="button" title={`Open ${a.name}`} onClick={(e) => { stop(e); onOpen({ type: "artist", id: a.id, name: a.name }); }}>{a.name}</a>
        </span>
      ))}
    </span>
  );
}

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
  // The play queue can be swapped (tapping a song on an artist / album page) without remounting the player.
  const [queue, setQueue] = useState<Track[]>(d.queue || []);
  const [source, setSource] = useState<{ mode: string; title: string; subtitle?: string }>({ mode: d.mode, title: d.title, subtitle: d.subtitle });
  const qRef = useRef(queue);
  qRef.current = queue;
  const [idx, setIdx] = useState(0);
  const idxRef = useRef(0);
  const toast = useStore((s) => s.toast);
  // Browsing an artist / album is an overlay ON this card: the YouTube player underneath keeps playing.
  const [views, setViews] = useState<View[]>([]);
  const openView = (v: View) => setViews((vs) => [...vs, v]);
  const go = (i: number, list = qRef.current) => {
    if (i < 0 || i >= list.length) return;
    idxRef.current = i;
    setIdx(i);
    p.cmd("loadVideoById", [list[i].video_id, 0]);
  };
  const playFrom = (list: Track[], i: number, src: { mode: string; title: string; subtitle?: string }) => {
    qRef.current = list;
    setQueue(list);
    setSource(src);
    setViews([]);
    media.activate(card.id);
    go(i, list);
  };
  const p = usePlayer(card, "music", queue[0]?.video_id || "", {
    next: () => go(idxRef.current + 1),
    previous: () => (p.info.time > 4 ? p.cmd("seekTo", [0, true]) : go(idxRef.current - 1)),
    onEnded: () => go(idxRef.current + 1),
    onError: () => {
      const t = qRef.current[idxRef.current];
      toast({ text: `“${t?.title ?? "That track"}” can't play here; skipping.`, error: true });
      go(idxRef.current + 1);
    },
  });
  const t = queue[idx] || queue[0];
  if (!t) return <div className="fx-empty">Nothing to play.</div>;
  const playing = p.info.state === PLAYING || p.info.state === BUFFERING;
  const art = t.thumb || d.art;
  const view = views[views.length - 1];

  return (
    <div className="md md-music">
      <div className="md-glow" style={{ backgroundImage: art ? `url("${art}")` : undefined }} />
      <div className="md-top">
        <div className="md-art">
          <iframe ref={p.frame} src={p.src} title="YouTube Music player" allow="autoplay; encrypted-media" referrerPolicy="strict-origin-when-cross-origin" />
        </div>
        <div className="md-meta">
          <label>{({ song: "SONG RADIO", album: "ALBUM", playlist: "PLAYLIST", artist: "ARTIST" } as Record<string, string>)[source.mode] ?? "MUSIC"} · {idx + 1}/{queue.length}</label>
          <div className="md-title" title={t.title}>{t.title}</div>
          <ArtistLinks t={t} onOpen={openView} className="md-artist" />
          {t.album && (t.album_id
            ? <a className="md-album md-link" role="button" onClick={(e) => { stop(e); openView({ type: "album", id: t.album_id!, title: t.album }); }}>{t.album}</a>
            : <div className="md-album">{t.album}</div>)}
          {source.mode !== "song" && <div className="md-src">{source.title}{source.subtitle ? ` · ${source.subtitle}` : ""}</div>}
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
      {view ? (
        <BrowseView key={JSON.stringify(view)} view={view} depth={views.length} nowId={t.video_id} playing={playing}
          onBack={() => setViews((vs) => vs.slice(0, -1))} onClose={() => setViews([])} onOpen={openView} onPlay={playFrom} />
      ) : queue.length > 1 && (
        <div className="md-q">
          <div className="fx-sec">UP NEXT</div>
          {queue.slice(idx + 1, idx + 9).map((x, j) => (
            <div key={x.video_id} className="md-q-row" role="button" onClick={(e) => { stop(e); go(idx + 1 + j); }}>
              {x.thumb ? <img src={x.thumb} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.visibility = "hidden")} /> : <span className="md-q-ph">♪</span>}
              <div><div className="md-q-t">{x.title}</div><ArtistLinks t={x} onOpen={openView} className="md-q-a" /></div>
              <span className="md-q-d">{x.duration ? fmtTime(x.duration) : ""}</span>
            </div>
          ))}
          {queue.length - idx - 9 > 0 && <div className="md-q-more">+{queue.length - idx - 9} more</div>}
        </div>
      )}
    </div>
  );
}

/** Artist / album page shown inside the music card. Read-only until he taps a song: then that list becomes the queue. */
function BrowseView({ view, depth, nowId, playing, onBack, onClose, onOpen, onPlay }: {
  view: View; depth: number; nowId: string; playing: boolean;
  onBack: () => void; onClose: () => void; onOpen: (v: View) => void;
  onPlay: (list: Track[], i: number, src: { mode: string; title: string; subtitle?: string }) => void;
}) {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    let live = true;
    const req = view.type === "artist" ? core.rpc("music_artist", { artist_id: view.id || "", name: view.name }) : core.rpc("music_album", { album_id: view.id });
    req.then((r) => { if (!live) return; if (r.ok) setData(r.result); else setErr(r.error || "Couldn't load that."); });
    return () => { live = false; };
  }, []);
  const head = (
    <div className="mb-nav" onClick={stop}>
      <button className="md-ic" onClick={onBack} title="Back">‹</button>
      <span>{view.type === "artist" ? "ARTIST" : "ALBUM"}{depth > 1 ? ` · ${depth}` : ""}</span>
      <span className="mb-now">{playing ? "♪ still playing" : ""}</span>
      <button className="md-ic" onClick={onClose} title="Back to Up Next">×</button>
    </div>
  );
  if (err) return <div className="mb">{head}<div className="fx-empty">{err}</div></div>;
  if (!data) return <div className="mb">{head}<div className="fx-empty">Loading {view.type === "artist" ? view.name : view.title}…</div></div>;
  if (view.type === "album") {
    const tracks: Track[] = data.tracks || [];
    return (
      <div className="mb">
        {head}
        <div className="mb-hero">
          {data.art && <img className="mb-cover" src={data.art} alt="" referrerPolicy="no-referrer" />}
          <div>
            <div className="mb-kind">{(data.type || "Album").toUpperCase()}{data.year ? ` · ${data.year}` : ""}</div>
            <div className="mb-name">{data.title}</div>
            <ArtistLinks t={data} onOpen={onOpen} className="mb-sub" />
            <div className="mb-sub">{tracks.length} songs{data.duration ? ` · ${data.duration}` : ""}</div>
            <button className="hbtn sm hbtn-cyan" disabled={!tracks.length} onClick={(e) => { stop(e); onPlay(tracks, 0, { mode: "album", title: data.title, subtitle: data.artist }); }}>▶ Play album</button>
          </div>
        </div>
        <TrackList tracks={tracks} nowId={nowId} numbered onOpen={onOpen} onPick={(i) => onPlay(tracks, i, { mode: "album", title: data.title, subtitle: data.artist })} />
      </div>
    );
  }
  const top: Track[] = data.top_songs || [];
  const rel = [...(data.albums || []), ...(data.singles || [])];
  return (
    <div className="mb">
      {head}
      <div className="mb-hero artist">
        {data.art && <img className="mb-avatar" src={data.art} alt="" referrerPolicy="no-referrer" />}
        <div>
          <div className="mb-kind">ARTIST</div>
          <div className="mb-name">{data.name}</div>
          <div className="mb-sub">{[data.monthly_listeners && `${data.monthly_listeners} monthly listeners`, data.subscribers && `${data.subscribers} subscribers`].filter(Boolean).join(" · ")}</div>
          <div className="mb-acts" onClick={stop}>
            <button className="hbtn sm hbtn-cyan" disabled={!top.length} onClick={() => onPlay(top, 0, { mode: "artist", title: data.name, subtitle: "Top songs" })}>▶ Play top songs</button>
            <button className="hbtn sm" onClick={() => open(data.url)}>YouTube Music ↗</button>
          </div>
        </div>
      </div>
      {data.description && <div className="mb-desc">{data.description}</div>}
      {top.length > 0 && <>
        <div className="fx-sec">TOP SONGS</div>
        <TrackList tracks={showAll ? top : top.slice(0, 6)} nowId={nowId} numbered onOpen={onOpen} onPick={(i) => onPlay(top, i, { mode: "artist", title: data.name, subtitle: "Top songs" })} />
        {top.length > 6 && <button className="mb-more" onClick={(e) => { stop(e); setShowAll(!showAll); }}>{showAll ? "Show less" : `Show all ${top.length}`}</button>}
      </>}
      {rel.length > 0 && <>
        <div className="fx-sec">ALBUMS & SINGLES</div>
        <div className="mb-grid">
          {rel.map((a: any) => (
            <button key={a.id} className="mb-tile" onClick={(e) => { stop(e); onOpen({ type: "album", id: a.id, title: a.title }); }}>
              {a.thumb ? <img src={a.thumb} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span className="md-q-ph">♪</span>}
              <span className="mb-tile-t">{a.title}</span>
              <span className="mb-tile-s">{[a.type, a.year].filter(Boolean).join(" · ")}</span>
            </button>
          ))}
        </div>
      </>}
      {(data.related || []).length > 0 && <>
        <div className="fx-sec">FANS ALSO LIKE</div>
        <div className="mb-grid round">
          {data.related.map((a: any) => (
            <button key={a.id} className="mb-tile" onClick={(e) => { stop(e); onOpen({ type: "artist", id: a.id, name: a.name }); }}>
              {a.thumb ? <img src={a.thumb} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span className="md-q-ph">♪</span>}
              <span className="mb-tile-t">{a.name}</span>
              {a.subscribers && <span className="mb-tile-s">{a.subscribers} subscribers</span>}
            </button>
          ))}
        </div>
      </>}
    </div>
  );
}

function TrackList({ tracks, nowId, numbered, onPick, onOpen }: { tracks: Track[]; nowId: string; numbered?: boolean; onPick: (i: number) => void; onOpen: (v: View) => void }) {
  return (
    <div className="md-q mb-list">
      {tracks.map((x, i) => (
        <div key={x.video_id} className={`md-q-row ${x.video_id === nowId ? "now" : ""}`} role="button" title="Play" onClick={(e) => { stop(e); onPick(i); }}>
          {numbered && <span className="mb-n">{x.video_id === nowId ? "♪" : i + 1}</span>}
          {x.thumb ? <img src={x.thumb} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.visibility = "hidden")} /> : <span className="md-q-ph">♪</span>}
          <div><div className="md-q-t">{x.title}</div><ArtistLinks t={x} onOpen={onOpen} className="md-q-a" /></div>
          <span className="md-q-d">{x.duration ? fmtTime(x.duration) : ""}</span>
        </div>
      ))}
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
