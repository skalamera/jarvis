import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useStore } from "../state/store";
import type { Card } from "../types";
import { attachYT, embedUrl, fmtTime, media, type YTInfo } from "../media/bus";
import { core } from "../ws/core";
import { MaxCtx } from "./HoloCard";

/* MUSIC (YouTube Music queue: art + player, progress, controls, volume, up next) and VIDEO (YouTube player +
   the other results). Both play through YouTube's embedded player; voice commands reach them via the media bus. */

const open = (u?: string) => u && window.jarvis?.open(u);
const stop = (e: React.MouseEvent | React.PointerEvent) => e.stopPropagation();

// YouTube player states
const ENDED = 0, PLAYING = 1, PAUSED = 2, BUFFERING = 3;

type Artist = { name: string; id?: string | null };
type Track = { video_id: string; title: string; artist: string; artists?: Artist[]; album: string; album_id?: string | null; duration: number | null; thumb: string };
type View = { type: "artist"; id?: string | null; name: string } | { type: "album"; id: string; title: string }
  | { type: "search"; query: string } | { type: "playlist"; id: string; title: string } | { type: "library" };
type Src = { mode: string; title: string; subtitle?: string };

/** His likes (thumbs up), shared across the card: optimistic toggle, synced with YouTube Music. */
const liked = new Set<string>();
const likeSubs = new Set<() => void>();
const likeEmit = () => likeSubs.forEach((f) => f());
function useLiked(ids: string[], enabled: boolean) {
  const [, force] = useState(0);
  useEffect(() => { const f = () => force((n) => n + 1); likeSubs.add(f); return () => { likeSubs.delete(f); }; }, []);
  const key = ids.join(",");
  useEffect(() => {
    if (!enabled || !ids.length) return;
    core.rpc("music_like_status", { video_ids: ids }).then((r) => {
      if (!r.ok) return;
      for (const id of ids) liked.delete(id);
      for (const id of r.result?.liked || []) liked.add(id);
      likeEmit();
    });
  }, [key, enabled]);
}
async function toggleLike(id: string, toast: (t: { text: string; error?: boolean }) => void) {
  const was = liked.has(id);
  if (was) liked.delete(id); else liked.add(id);
  likeEmit();
  const r = await core.rpc("music_rate", { video_id: id, rating: was ? "none" : "like" });
  if (!r.ok) {
    if (was) liked.add(id); else liked.delete(id);
    likeEmit();
    toast({ text: r.error || "Couldn't update that like.", error: true });
  }
}

function LikeBtn({ id, signedIn, size = "md" }: { id: string; signedIn: boolean; size?: "md" | "sm" }) {
  const toast = useStore((s) => s.toast);
  if (!signedIn) return null;
  const on = liked.has(id);
  return (
    <button className={`md-like ${size} ${on ? "on" : ""}`} title={on ? "Remove like" : "Like (thumbs up)"}
      onClick={(e) => { stop(e); toggleLike(id, toast); }}>{on ? "👍" : "👍︎"}</button>
  );
}

/** "Add to playlist": his playlists + create new, anchored to a button. */
function PlaylistMenu({ track, onClose }: { track: Track; onClose: () => void }) {
  const [pls, setPls] = useState<any[] | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState("");
  const toast = useStore((s) => s.toast);
  useEffect(() => { core.rpc("music_my_playlists", {}).then((r) => setPls(r.ok ? r.result.playlists : [])); }, []);
  const add = async (p: any) => {
    setBusy(p.id);
    const r = await core.rpc("music_playlist_add", { playlist_id: p.id, video_ids: [track.video_id] });
    setBusy("");
    toast(r.ok ? { text: `Added “${track.title}” to ${p.title}.` } : { text: r.error || "Couldn't add it.", error: true });
    if (r.ok) onClose();
  };
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy("new");
    const r = await core.rpc("music_playlist_create", { title: name.trim(), video_ids: [track.video_id] });
    setBusy("");
    toast(r.ok ? { text: `Created “${name.trim()}” with “${track.title}”.` } : { text: r.error || "Couldn't create it.", error: true });
    if (r.ok) onClose();
  };
  return (
    <div className="md-plmenu" onClick={stop}>
      <div className="md-plmenu-h"><span>ADD TO PLAYLIST</span><button className="md-ic" onClick={onClose}>×</button></div>
      <div className="md-plmenu-t">{track.title}</div>
      <form className="md-plnew" onSubmit={create}>
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="New playlist name" />
        <button className="hbtn sm hbtn-cyan" disabled={!name.trim() || busy === "new"}>{busy === "new" ? "…" : "Create"}</button>
      </form>
      <div className="md-pllist">
        {pls === null && <div className="muted small">Loading your playlists…</div>}
        {pls?.length === 0 && <div className="muted small">No playlists yet.</div>}
        {pls?.map((p) => (
          <button key={p.id} disabled={!!busy} onClick={() => add(p)}>
            {p.thumb ? <img src={p.thumb} alt="" referrerPolicy="no-referrer" /> : <span className="md-q-ph">♪</span>}
            <span>{p.title}</span><em>{busy === p.id ? "adding…" : p.count ? `${p.count}` : ""}</em>
          </button>
        ))}
      </div>
    </div>
  );
}

const RowCtx = createContext<{ signedIn: boolean; addTo: (t: Track) => void }>({ signedIn: false, addTo: () => {} });

function RowActions({ t }: { t: Track }) {
  const c = useContext(RowCtx);
  if (!c.signedIn) return null;
  return (
    <span className="md-row-acts" onClick={stop}>
      <LikeBtn id={t.video_id} signedIn size="sm" />
      <button className="md-ic sm" title="Add to playlist" onClick={() => c.addTo(t)}>＋</button>
    </span>
  );
}

/** YouTube Music account state, shared by every music card. */
function useYtmAccount() {
  const [acct, setAcct] = useState<{ signed_in: boolean; name?: string; photo?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useStore((s) => s.toast);
  const refresh = () => core.rpc("music_auth_status", {}).then((r) => setAcct(r.ok ? r.result : { signed_in: false }));
  useEffect(() => { refresh(); }, []);
  const signIn = async () => {
    if (!window.jarvis?.ytmLogin) return;
    setBusy(true);
    try {
      const cookie = await window.jarvis.ytmLogin();
      if (!cookie) return;
      const r = await core.rpc("music_set_auth", { cookie });
      if (r.ok) { setAcct(r.result); toast({ text: `Signed in to YouTube Music${r.result?.name ? ` as ${r.result.name}` : ""}.` }); }
      else toast({ text: r.error || "YouTube Music sign-in failed.", error: true });
    } finally { setBusy(false); }
  };
  const signOut = async () => {
    await core.rpc("music_sign_out", {});
    await window.jarvis?.ytmLogout?.();
    setAcct({ signed_in: false });
  };
  return { acct, busy, signIn, signOut };
}

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
  const [src] = useState(() => embedUrl(firstId, { controls: kind === "video", paused: !!card.data?.paused }));

  useEffect(() => {
    const f = frame.current;
    if (!f) return;
    const a = attachYT(
      f,
      (i) => setInfo((p) => ({ ...p, ...i })),
      (ev, v) => {
        if (ev === "ready" && !card.data?.paused) { media.activate(card.id); a.command("playVideo"); }
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
  const root = useRef<HTMLDivElement>(null);
  const mx = useContext(MaxCtx);
  const big = !!mx?.max;
  const setBig = (b: boolean) => mx?.setMax(b);
  const [q, setQ] = useState("");
  const yt = useYtmAccount();
  const [plFor, setPlFor] = useState<Track | null>(null);
  // Expanding grows THIS card in place (HoloCard's .holo-xl, Esc / click-outside handled there), so the
  // YouTube player iframe is never re-mounted and the song keeps playing.
  // The play queue can be swapped (tapping a song on an artist / album page) without remounting the player.
  const [queue, setQueue] = useState<Track[]>(d.queue || []);
  const [source, setSource] = useState<Src>({ mode: d.mode, title: d.title, subtitle: d.subtitle });
  const qRef = useRef(queue);
  qRef.current = queue;
  const [idx, setIdx] = useState(0);
  const idxRef = useRef(0);
  const toast = useStore((s) => s.toast);
  // Browsing an artist / album is an overlay ON this card: the YouTube player underneath keeps playing.
  const [views, setViews] = useState<View[]>(d.start_view === "library" ? [{ type: "library" }] : []);
  const openView = (v: View) => setViews((vs) => [...vs, v]);
  const go = (i: number, list = qRef.current) => {
    if (i < 0 || i >= list.length) return;
    idxRef.current = i;
    setIdx(i);
    p.cmd("loadVideoById", [list[i].video_id, 0]);
  };
  const playFrom = (list: Track[], i: number, src: Src) => {
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
  const playing = p.info.state === PLAYING || p.info.state === BUFFERING;
  // Mirror the now-playing state for the left-column mini player.
  const pRef = useRef(p);
  pRef.current = p;
  useEffect(() => {
    if (!t) return;
    media.setNowPlaying({
      cardId: card.id, title: t.title, artist: t.artists?.map((a) => a.name).join(", ") || t.artist || "",
      art: t.thumb || d.art, playing, time: p.info.time, duration: p.info.duration,
      toggle: () => { const pp = pRef.current; const on = pp.info.state === PLAYING || pp.info.state === BUFFERING; if (on) pp.cmd("pauseVideo"); else { media.activate(card.id); pp.cmd("playVideo"); } },
      expand: () => setBig(true),
      next: () => go(idxRef.current + 1),
      previous: () => { const pp = pRef.current; if (pp.info.time > 4) pp.cmd("seekTo", [0, true]); else go(idxRef.current - 1); },
      seek: (s) => pRef.current.cmd("seekTo", [s, true]),
    });
  }, [t?.video_id, playing, Math.floor(p.info.time), p.info.duration]);
  useEffect(() => () => media.clearNowPlaying(card.id), [card.id]);
  if (!t) return <div className="fx-empty">Nothing to play.</div>;
  const art = t.thumb || d.art;
  const view = views[views.length - 1];
  const signedIn = !!yt.acct?.signed_in;

  const search = (e?: React.FormEvent) => {
    e?.preventDefault();
    const query = q.trim();
    if (query) setViews([{ type: "search", query }]);
  };

  return (
    <RowCtx.Provider value={{ signedIn, addTo: (x) => setPlFor(x) }}>
    <LikeSync ids={[t.video_id, ...queue.slice(idx + 1, idx + 9).map((x) => x.video_id)]} enabled={signedIn} />
    <div className={`md md-music ${big ? "xl" : ""}`} ref={root}>
      <div className="md-bar-top" onClick={stop}>
        <form className="md-search" onSubmit={search}>
          <span>⌕</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search songs, artists, albums, playlists" />
          {q && <button type="button" className="md-ic" title="Clear" onClick={() => setQ("")}>×</button>}
        </form>
        {yt.acct?.signed_in ? (
          <button className="md-acct" title="Your library" onClick={() => setViews([{ type: "library" }])}>
            {yt.acct.photo ? <img src={yt.acct.photo} alt="" referrerPolicy="no-referrer" /> : <span>♪</span>}
            <em>{yt.acct.name || "Library"}</em>
          </button>
        ) : (
          <button className="hbtn sm" disabled={yt.busy || !window.jarvis?.ytmLogin} onClick={yt.signIn}
            title="Opens Google Chrome (Google blocks sign-in inside apps). Sign in there once; JARVIS stays signed in.">
            {yt.busy ? "Finish signing in in Chrome…" : "Sign in to YouTube Music"}</button>
        )}
        <button className="md-ic md-expand" title={big ? "Shrink (Esc)" : "Expand"} onClick={() => setBig(!big)}>{big ? "⤡" : "⤢"}</button>
      </div>
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
        {signedIn && <>
          <LikeBtn id={t.video_id} signedIn />
          <span className="md-plwrap">
            <button className="md-ic md-addpl" title="Add to playlist" onClick={() => setPlFor(plFor?.video_id === t.video_id ? null : t)}>＋</button>
          </span>
        </>}
        <button className="hbtn sm hbtn-cyan md-open" onClick={() => open(`https://music.youtube.com/watch?v=${t.video_id}`)}>YouTube Music ↗</button>
      </div>
      {plFor && <PlaylistMenu track={plFor} onClose={() => setPlFor(null)} />}
      {view ? (
        <BrowseView key={JSON.stringify(view)} view={view} depth={views.length} nowId={t.video_id} playing={playing}
          onBack={() => setViews((vs) => vs.slice(0, -1))} onClose={() => setViews([])} onOpen={openView} onPlay={playFrom}
          onSignOut={async () => { await yt.signOut(); setViews([]); }} />
      ) : queue.length > 1 && (
        <div className="md-q">
          <div className="fx-sec">UP NEXT</div>
          {queue.slice(idx + 1, idx + 9).map((x, j) => (
            <div key={x.video_id} className="md-q-row" role="button" onClick={(e) => { stop(e); go(idx + 1 + j); }}>
              {x.thumb ? <img src={x.thumb} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.visibility = "hidden")} /> : <span className="md-q-ph">♪</span>}
              <div><div className="md-q-t">{x.title}</div><ArtistLinks t={x} onOpen={openView} className="md-q-a" /></div>
              <RowActions t={x} />
              <span className="md-q-d">{x.duration ? fmtTime(x.duration) : ""}</span>
            </div>
          ))}
          {queue.length - idx - 9 > 0 && <div className="md-q-more">+{queue.length - idx - 9} more</div>}
        </div>
      )}
    </div>
    </RowCtx.Provider>
  );
}

function LikeSync({ ids, enabled }: { ids: string[]; enabled: boolean }) {
  useLiked(ids, enabled);
  return null;
}

/** Artist / album page shown inside the music card. Read-only until he taps a song: then that list becomes the queue. */
function BrowseView({ view, depth, nowId, playing, onBack, onClose, onOpen, onPlay, onSignOut }: {
  view: View; depth: number; nowId: string; playing: boolean;
  onBack: () => void; onClose: () => void; onOpen: (v: View) => void;
  onPlay: (list: Track[], i: number, src: Src) => void; onSignOut: () => void;
}) {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    let live = true;
    const req = view.type === "artist" ? core.rpc("music_artist", { artist_id: view.id || "", name: view.name })
      : view.type === "album" ? core.rpc("music_album", { album_id: view.id })
      : view.type === "search" ? core.rpc("music_find", { query: view.query })
      : view.type === "playlist" ? core.rpc("music_playlist", { playlist_id: view.id })
      : core.rpc("music_library", {});
    req.then((r) => { if (!live) return; if (r.ok) setData(r.result); else setErr(r.error || "Couldn't load that."); });
    return () => { live = false; };
  }, []);
  const head = (
    <div className="mb-nav" onClick={stop}>
      <button className="md-ic" onClick={onBack} title="Back">‹</button>
      <span>{({ artist: "ARTIST", album: "ALBUM", search: "SEARCH", playlist: "PLAYLIST", library: "YOUR LIBRARY" } as Record<string, string>)[view.type]}{depth > 1 ? ` · ${depth}` : ""}</span>
      <span className="mb-now">{playing ? "♪ still playing" : ""}</span>
      <button className="md-ic" onClick={onClose} title="Back to Up Next">×</button>
    </div>
  );
  if (err) return <div className="mb">{head}<div className="fx-empty">{err}</div></div>;
  if (!data) return <div className="mb">{head}<div className="fx-empty">{view.type === "search" ? `Searching “${view.query}”…` : view.type === "library" ? "Loading your library…" : `Loading ${view.type === "artist" ? view.name : view.title}…`}</div></div>;
  const playSong = async (x: Track) => {
    const r = await core.rpc("music_radio", { video_id: x.video_id });
    const list: Track[] = r.ok && r.result?.queue?.length ? r.result.queue : [x];
    if (list[0]?.video_id !== x.video_id) list.unshift(x);
    onPlay(list, 0, { mode: "song", title: x.title, subtitle: x.artist });
  };
  if (view.type === "search") {
    const top = data.top;
    const empty = !top && !data.songs?.length && !data.artists?.length && !data.albums?.length;
    const tile = (a: any) => (
      <button key={a.type + a.id} className="mb-tile" onClick={(e) => { stop(e); onOpen(a.type === "album" ? { type: "album", id: a.id, title: a.title } : a.type === "playlist" ? { type: "playlist", id: a.id, title: a.title } : { type: "artist", id: a.id, name: a.name }); }}>
        {a.thumb ? <img src={a.thumb} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span className="md-q-ph">♪</span>}
        <span className="mb-tile-t">{a.title || a.name}</span>
        <span className="mb-tile-s">{a.type === "album" ? [a.kind, a.artist, a.year].filter(Boolean).join(" · ") : a.type === "playlist" ? a.author : a.sub ? `${a.sub} subscribers` : "Artist"}</span>
      </button>
    );
    return (
      <div className="mb">
        {head}
        {empty && <div className="fx-empty">Nothing on YouTube Music for “{view.query}”.</div>}
        {top && (
          <div className="mb-top" role="button" onClick={(e) => { stop(e); if (top.type === "song" || top.type === "video") playSong(top); else onOpen(top.type === "album" ? { type: "album", id: top.id, title: top.title } : top.type === "playlist" ? { type: "playlist", id: top.id, title: top.title } : { type: "artist", id: top.id, name: top.name }); }}>
            {top.thumb && <img className={top.type === "artist" ? "round" : ""} src={top.thumb} alt="" referrerPolicy="no-referrer" />}
            <div>
              <div className="mb-kind">TOP RESULT · {String(top.type).toUpperCase()}</div>
              <div className="mb-name">{top.title || top.name}</div>
              <div className="mb-sub">{top.artist || top.author || (top.sub ? `${top.sub} subscribers` : "")}</div>
            </div>
            <span className="mb-top-go">{top.type === "song" || top.type === "video" ? "▶" : "›"}</span>
          </div>
        )}
        {data.songs?.length > 0 && <><div className="fx-sec">SONGS</div><TrackList tracks={data.songs} nowId={nowId} onOpen={onOpen} onPick={(i) => playSong(data.songs[i])} /></>}
        {data.artists?.length > 0 && <><div className="fx-sec">ARTISTS</div><div className="mb-grid round">{data.artists.slice(0, 6).map(tile)}</div></>}
        {data.albums?.length > 0 && <><div className="fx-sec">ALBUMS</div><div className="mb-grid">{data.albums.slice(0, 8).map(tile)}</div></>}
        {data.playlists?.length > 0 && <><div className="fx-sec">PLAYLISTS</div><div className="mb-grid">{data.playlists.slice(0, 8).map(tile)}</div></>}
      </div>
    );
  }
  if (view.type === "playlist") {
    const tracks: Track[] = data.tracks || [];
    const src = { mode: "playlist", title: data.title, subtitle: data.author };
    return (
      <div className="mb">
        {head}
        <div className="mb-hero">
          {data.art && <img className="mb-cover" src={data.art} alt="" referrerPolicy="no-referrer" />}
          <div>
            <div className="mb-kind">PLAYLIST</div>
            <div className="mb-name">{data.title}</div>
            <div className="mb-sub">{[data.author, `${tracks.length} songs`].filter(Boolean).join(" · ")}</div>
            <button className="hbtn sm hbtn-cyan" disabled={!tracks.length} onClick={(e) => { stop(e); onPlay(tracks, 0, src); }}>▶ Play</button>
          </div>
        </div>
        <TrackList tracks={tracks} nowId={nowId} numbered onOpen={onOpen} onPick={(i) => onPlay(tracks, i, src)} />
      </div>
    );
  }
  if (view.type === "library") {
    if (!data.signed_in) return <div className="mb">{head}<div className="fx-empty">Sign in to YouTube Music to see your library.</div></div>;
    const liked: Track[] = data.liked || [], recent: Track[] = data.recent || [];
    return (
      <div className="mb">
        {head}
        {data.playlists?.length > 0 && <><div className="fx-sec">YOUR PLAYLISTS</div><div className="mb-grid">
          {data.playlists.map((p: any) => (
            <button key={p.id} className="mb-tile" onClick={(e) => { stop(e); onOpen({ type: "playlist", id: p.id, title: p.title }); }}>
              {p.thumb ? <img src={p.thumb} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span className="md-q-ph">♪</span>}
              <span className="mb-tile-t">{p.title}</span><span className="mb-tile-s">{p.count ? `${p.count} songs` : "Playlist"}</span>
            </button>
          ))}
        </div></>}
        {liked.length > 0 && <>
          <div className="fx-sec">LIKED SONGS <button className="mb-more" onClick={(e) => { stop(e); onPlay(liked, 0, { mode: "playlist", title: "Liked songs" }); }}>▶ Play all</button></div>
          <TrackList tracks={liked.slice(0, 12)} nowId={nowId} onOpen={onOpen} onPick={(i) => onPlay(liked, i, { mode: "playlist", title: "Liked songs" })} />
        </>}
        {recent.length > 0 && <><div className="fx-sec">RECENTLY PLAYED</div><TrackList tracks={recent.slice(0, 10)} nowId={nowId} onOpen={onOpen} onPick={(i) => playSong(recent[i])} /></>}
        <button className="mb-more" onClick={(e) => { stop(e); onSignOut(); }}>Sign out of YouTube Music</button>
      </div>
    );
  }
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
  const c = useContext(RowCtx);
  useLiked(tracks.slice(0, 50).map((x) => x.video_id), c.signedIn);
  return (
    <div className="md-q mb-list">
      {tracks.map((x, i) => (
        <div key={x.video_id} className={`md-q-row ${x.video_id === nowId ? "now" : ""}`} role="button" title="Play" onClick={(e) => { stop(e); onPick(i); }}>
          {numbered && <span className="mb-n">{x.video_id === nowId ? "♪" : i + 1}</span>}
          {x.thumb ? <img src={x.thumb} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.visibility = "hidden")} /> : <span className="md-q-ph">♪</span>}
          <div><div className="md-q-t">{x.title}</div><ArtistLinks t={x} onOpen={onOpen} className="md-q-a" /></div>
          <RowActions t={x} />
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
  const [q, setQ] = useState(d.query || "");
  const [busy, setBusy] = useState(false);
  const find = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setBusy(true);
    const r = await core.rpc("youtube_find", { query: q });
    setBusy(false);
    if (r.ok && r.result?.results?.length) {
      core.patchCard(card.id, { ...r.result, paused: false }, r.result.query ? `YouTube · ${r.result.query}` : "YouTube · Home");
      idxRef.current = 0; setIdx(0);
      p.cmd("loadVideoById", [r.result.results[0].video_id, 0]);
    } else toast({ text: r.error || `No videos for “${q}”.`, error: true });
  };
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
      <form className="md-search md-vsearch" onSubmit={find} onClick={stop}>
        <span>⌕</span>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search YouTube" />
        {busy ? <span className="cx-spin" /> : q && <button type="button" className="md-ic" title="Clear" onClick={() => setQ("")}>×</button>}
      </form>
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
          <div className="fx-sec">{d.home === "subscriptions" && !d.query ? "FROM YOUR SUBSCRIPTIONS" : d.home ? "TRENDING" : "MORE RESULTS"}{d.query ? ` · “${d.query}”` : ""}</div>
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
