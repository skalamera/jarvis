import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { createPortal } from "react-dom";
import { useStore } from "../state/store";
import { core } from "../ws/core";
import { HoloCard } from "../cards/HoloCard";
import { MarkdownCard } from "../cards/Cards";
import { fmtEventTime } from "../cards/format";
import { Reactor } from "./Reactor";
import { AttachChips, AttachControls, DropZone, Workbench } from "./Workbench";
import { Briefing } from "./Briefing";
import { media, fmtTime } from "../media/bus";
import { HoloForge } from "./HoloForge";
import { HoloRadar } from "./HoloRadar";
import { HoloTube } from "./HoloTube";

const STATE_TEXT: Record<string, string> = {
  idle: "STANDING BY",
  sleep: "ASLEEP",
  listening: "LISTENING",
  thinking: "PROCESSING",
  speaking: "RESPONDING",
  confirm: "AWAITING AUTHORIZATION",
  offline: "CORE OFFLINE — RECONNECTING",
};

function Clock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="clock">
      <div className="clock-time">{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}</div>
      <div className="clock-date">{now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }).toUpperCase()}</div>
    </div>
  );
}

function StatusBar() {
  const { connected, hermesOk, voiceOk, wakeAvailable, micMode, micEnabled, speak } = useStore();
  const dot = (ok: boolean, label: string) => (
    <span className={`sys ${ok ? "ok" : "bad"}`}><i />{label}</span>
  );
  return (
    <div className="sysbar">
      {dot(connected, "CORE")}
      {dot(connected && hermesOk, "HERMES")}
      {dot(connected && voiceOk, "VOICE")}
      {dot(connected && micEnabled && (micMode === "wake" || micMode === "capture" || micMode === "ptt"), wakeAvailable ? "WAKE · “HEY JARVIS”" : "MIC")}
      <span className="sys-spacer" />
      <button className={`tog ${speak ? "on" : ""}`} onClick={() => core.setSpeak(!speak)} title="Spoken replies">
        {speak ? "🔊 VOICE ON" : "🔇 VOICE OFF"}
      </button>
      <button className={`tog ${micEnabled ? "on" : ""}`} onClick={() => core.setMic(!micEnabled)} title="Microphone">
        {micEnabled ? "🎙 MIC ON" : "🎙 MIC OFF"}
      </button>
      <button className="tog" onClick={() => core.toggleSleep()} title="Sleep (⌘⇧S) · say “Morning, Jarvis” to power on">☾ SLEEP</button>
      <button className="tog" onClick={() => core.reset()} title="New conversation">⟲ NEW</button>
    </div>
  );
}

function Telemetry() {
  const t = useStore((s) => s.telemetry);
  const accts = t?.accounts ?? [];
  const events = useMemo(() => {
    const all = accts.flatMap((a) => (a.events ?? []).map((e) => ({ ...e, account: a.account })));
    return all.filter((e) => e.all_day || new Date(e.start).getTime() > Date.now() - 30 * 60e3)
      .sort((a, b) => new Date(a.start).getTime() - new Date(b.start).getTime()).slice(0, 5);
  }, [accts]);
  return (
    <div className="telemetry">
      <div className="panel-label">INBOX</div>
      {accts.map((a) => (
        <div key={a.account} className="tele-row" title="Open the briefing" onClick={() => useStore.getState().set({ rightTab: "briefing" })}>
          <span className={`acct acct-${a.account}`}>{a.account.toUpperCase()}</span>
          <span className="tele-num">{a.error ? "ERR" : (a.unread ?? 0).toLocaleString()}</span>
          <span className="muted small">unread</span>
        </div>
      ))}
      <div className="panel-label" style={{ marginTop: 18 }}>NEXT UP</div>
      {events.length === 0 && <div className="muted small">Clear skies, sir.</div>}
      {events.map((e, i) => (
        <div key={i} className="tele-ev">
          <span className="tele-ev-time">{e.all_day ? "ALL DAY" : fmtEventTime(e.start, false)}</span>
          <span className="tele-ev-title">{e.summary}</span>
          {e.hangout && <button className="hbtn hbtn-cyan sm" onClick={() => core.openExternal(e.hangout!)}>Join</button>}
        </div>
      ))}
    </div>
  );
}

function ActivityFeed() {
  const tools = useStore((s) => s.tools);
  const hud = useStore((s) => s.hud);
  const visible = tools.slice(-7);
  return (
    <div className="activity">
      <div className="panel-label">SUBSYSTEMS</div>
      <AnimatePresence initial={false}>
        {visible.map((t) => (
          <motion.div key={t.id} layout initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }}
            className={`act act-${t.status}`}>
            <span className="act-dot" />
            <span className="act-label">{t.label}</span>
            <span className="act-time">{t.status === "start" ? "…" : t.duration != null ? `${t.duration.toFixed(1)}s` : "✓"}</span>
          </motion.div>
        ))}
      </AnimatePresence>
      {!visible.length && <div className="muted small">{hud === "thinking" ? "Engaging…" : "All systems nominal."}</div>}
    </div>
  );
}

/** Compact now-playing strip in the left column while the music card is alive. Its buttons drive the
 *  card's own player (no second iframe); tapping the title jumps to the full card on Displays. */
function MiniPlayer() {
  const [np, setNp] = useState(media.nowPlaying);
  useEffect(() => media.subscribeNowPlaying(() => setNp(media.nowPlaying)), []);
  if (!np) return null;
  const pct = np.duration ? Math.min(100, (np.time / np.duration) * 100) : 0;
  const reveal = () => {
    useStore.getState().set({ rightTab: "displays", displaysUnseen: 0 });
    requestAnimationFrame(() => document.querySelector(`[data-card-id="${np.cardId}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };
  return (
    <div className={`mini-player ${np.playing ? "on" : ""}`}>
      <div className="mp-row">
        <button className="mp-art" title="Show player" onClick={reveal} style={{ backgroundImage: np.art ? `url("${np.art}")` : undefined }} />
        <button className="mp-meta" title="Show player" onClick={reveal}>
          <span className="mp-lbl">{np.playing ? "NOW PLAYING" : "PAUSED"}</span>
          <span className="mp-title">{np.title}</span>
          <span className="mp-artist">{np.artist}</span>
        </button>
        <button className="mp-max" title="Maximize player" onClick={() => {
          // The full card lives on Displays; show that tab first so the fixed overlay isn't inside a hidden panel.
          useStore.getState().set({ rightTab: "displays", displaysUnseen: 0 });
          requestAnimationFrame(() => np.expand());
        }}>⤢</button>
      </div>
      <div className="mp-bar" onClick={(e) => { const r = e.currentTarget.getBoundingClientRect(); np.seek(((e.clientX - r.left) / r.width) * np.duration); }}>
        <i style={{ width: `${pct}%` }} />
      </div>
      <div className="mp-ctl">
        <span className="mp-t">{fmtTime(np.time)}</span>
        <button title="Previous" onClick={np.previous}>⏮</button>
        <button className="mp-play" title={np.playing ? "Pause" : "Play"} onClick={np.toggle}>{np.playing ? "❚❚" : "▶"}</button>
        <button title="Next" onClick={np.next}>⏭</button>
        <span className="mp-t">{fmtTime(np.duration)}</span>
      </div>
    </div>
  );
}

export function setLogOpen(open: boolean) {
  localStorage.setItem("jarvis.logOpen", open ? "1" : "0");
  useStore.getState().set(open ? { logOpen: true, logUnseen: 0 } : { logOpen: false });
}

/** App launcher: every icon opens its display directly (no model turn), maximized. */
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
async function viaRpc(op: string, args: Record<string, unknown>, kind: string, title: string, label: string) {
  const s = useStore.getState();
  s.toast({ text: `Opening ${label}…` });
  const r = await core.rpc(op, args);
  if (!r.ok) { s.toast({ text: `Couldn't open ${label}: ${r.error}`, error: true }); return; }
  core.launchCard({ kind, title, account: null, data: r.result });
}
const HOME = "New Rochelle, NY";
const LAUNCH: { icon: string; label: string; run: () => void }[] = [
  { icon: "🚘", label: "Garage", run: () => core.launchTool("car_profile", { section: "overview" }) },
  { icon: "⛅", label: "Weather", run: () => core.launchTool("weather", {}) },
  { icon: "▶", label: "YouTube", run: () => viaRpc("youtube_home", {}, "video", "YouTube · Home", "YouTube") },
  { icon: "♫", label: "YT Music", run: () => viaRpc("music_home", {}, "music", "YouTube Music · Your library", "YouTube Music") },
  { icon: "📈", label: "Markets", run: () => core.launchTool("market_overview", {}) },
  { icon: "₿", label: "Trading", run: () => core.launchTool("trade_portfolio", {}) },
  { icon: "🍽", label: "Resy", run: () => viaRpc("resy_find", { date: today(), party_size: 2 }, "travel_restaurants", "Resy · near you", "Resy") },
  { icon: "📍", label: "Places", run: () => viaRpc("places_find", {}, "places", "Places · near you", "Places") },
  { icon: "🗺", label: "Maps", run: () => core.launchCard({ kind: "directions", title: "Maps", account: null, data: { blank: true, origin: "", destination: "", home: HOME, mode: "driving", routes: [] } }) },
  { icon: "◈", label: "Kalshi", run: () => core.launchCard({ kind: "kalshi", title: "Kalshi", account: null, data: {} }) },
  { icon: "💼", label: "Careers", run: () => core.launchCard({ kind: "career_ops", title: "Career Ops", account: null, data: {} }) },
  { icon: "🥡", label: "Uber Eats", run: () => core.launchCard({ kind: "webapp", title: "Uber Eats", account: null, data: { url: "https://www.ubereats.com/", partition: "persist:ubereats" } }) },
  { icon: "🧳", label: "Trips", run: () => core.launchCard({ kind: "trip", title: "Trip planner", account: null, data: {} }) },
  { icon: "✈", label: "Flights", run: () => core.launchCard({ kind: "travel_flights", title: "Flights", account: null, data: { form: true, offers: [] } }) },
  { icon: "🏟", label: "Sports", run: () => core.launchTool("sports_game", { league: "nfl" }) },
  { icon: "📅", label: "Calendar", run: () => core.launchCard({ kind: "calendar", title: "Calendar", account: "personal", data: { events: [] } }) },
  { icon: "✉", label: "Inbox", run: () => core.launchTool("gmail_search", { account: "personal", query: "in:inbox", max_results: 25 }) },
  { icon: "💬", label: "Slack", run: () => core.launchTool("slack_updates", {}) },
  { icon: "🎫", label: "Pylon", run: () => core.launchTool("pylon_tickets", {}) },
  { icon: "🗂", label: "Files", run: () => viaRpc("file_list", {}, "launch_files", "Your files", "Files") },
];

function Launcher() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: Event) => { const n = e.target as Node; if (!ref.current?.contains(n) && !popRef.current?.contains(n)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };
    window.addEventListener("pointerdown", off, true);
    window.addEventListener("keydown", esc, true);
    return () => { window.removeEventListener("pointerdown", off, true); window.removeEventListener("keydown", esc, true); };
  }, [open]);
  return (
    <div className="launcher" ref={ref}>
      <button className={`attach-btn launch-btn ${open ? "on" : ""}`} title="Launcher" onClick={() => setOpen(!open)}>
        <svg viewBox="0 0 24 24" width="17" height="17"><path fill="currentColor" d="M4 4h4v4H4zm6 0h4v4h-4zm6 0h4v4h-4zM4 10h4v4H4zm6 0h4v4h-4zm6 0h4v4h-4zM4 16h4v4H4zm6 0h4v4h-4zm6 0h4v4h-4z" /></svg>
      </button>
      {open && createPortal(
        <div className="launch-pop" role="menu" ref={popRef}
          style={(() => { const b = ref.current!.getBoundingClientRect(); return { left: Math.max(12, b.left - 60), bottom: window.innerHeight - b.top + 14 }; })()}>
          <div className="launch-h">LAUNCH</div>
          <div className="launch-grid">
            {LAUNCH.map((a) => (
              <button key={a.label} className="launch-app" title={`Open ${a.label}`}
                onClick={() => { setOpen(false); a.run(); }}>
                <span className="launch-ic img"><img src={`launcher/${encodeURIComponent(a.label)}.svg`} alt="" draggable={false} onError={(e) => { e.currentTarget.replaceWith(document.createTextNode(a.icon)); }} /></span>
                <span className="launch-lb">{a.label}</span>
              </button>
            ))}
          </div>
        </div>, document.body,
      )}
    </div>
  );
}

/** The conversation log: hidden by default (the orb + captions are the main view), opened from the composer. */
function Transcript() {
  const messages = useStore((s) => s.messages);
  const open = useStore((s) => s.logOpen);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: messages.length ? "smooth" : "auto" });
  }, [messages, open]);
  return (
    <section className={`log ${open ? "open" : ""}`} aria-hidden={!open}>
      <div className="log-head">
        <span>CONVERSATION LOG</span>
        <span className="log-count">{messages.filter((m) => m.role !== "system").length}</span>
        <button className="log-close" onClick={() => setLogOpen(false)} title="Hide log (⌘L)">✕</button>
      </div>
      <div className="transcript" ref={ref}>
        {messages.length === 0 && <div className="muted small log-empty">Nothing said yet this session.</div>}
        {messages.map((m) => (
        <div key={m.id} className={`msg msg-${m.role} ${m.error ? "msg-error" : ""}`}>
          {m.role === "user" && <div className="msg-who">YOU {m.source === "voice" ? "· 🎙" : ""}</div>}
          {m.role === "jarvis" && <div className="msg-who">J.A.R.V.I.S.{m.elapsed ? ` · ${m.elapsed.toFixed(1)}s` : ""}</div>}
          {m.role === "jarvis" ? (
            m.text ? <MarkdownCard text={m.text} /> : <div className="typing"><i /><i /><i /></div>
          ) : (
            <div className="msg-text">{m.text}</div>
          )}
        </div>
        ))}
      </div>
    </section>
  );
}

function LogToggle() {
  const open = useStore((s) => s.logOpen);
  const unseen = useStore((s) => s.logUnseen);
  return (
    <button className={`log-btn ${open ? "on" : ""}`} onClick={() => setLogOpen(!open)} title={`${open ? "Hide" : "Show"} conversation log (⌘L)`}>
      <svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="M4 5h16v2H4V5Zm0 6h16v2H4v-2Zm0 6h10v2H4v-2Z" /></svg>
      {!open && unseen > 0 && <b>{unseen > 9 ? "9+" : unseen}</b>}
    </button>
  );
}

/** A reply as caption text: markdown and jarvis-visual blocks stripped, capped at a few sentences. */
function speakableCaption(text: string): string {
  const plain = text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`#>|]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (plain.length <= 280) return plain;
  const cut = plain.slice(0, 280);
  const end = Math.max(cut.lastIndexOf(". "), cut.lastIndexOf("? "), cut.lastIndexOf("! "));
  return (end > 120 ? cut.slice(0, end + 1) : cut.replace(/\s+\S*$/, "") + " …");
}

function greeting(): string {
  const h = new Date().getHours();
  return h < 5 ? "evening" : h < 12 ? "morning" : h < 17 ? "afternoon" : "evening";
}

function Composer() {
  const [text, setText] = useState("");
  const hud = useStore((s) => s.hud);
  const connected = useStore((s) => s.connected);
  const holding = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const busy = hud === "thinking" || hud === "speaking";
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "s") {
        e.preventDefault();
        core.toggleSleep();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "d") {
        e.preventDefault();
        core.showcase("demo");
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "l") {
        e.preventDefault();
        setLogOpen(!useStore.getState().logOpen);
        return;
      }
      if (e.key === "Escape" && useStore.getState().logOpen) { setLogOpen(false); return; }
      if (e.key === "Escape") core.cancel();
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT" && document.activeElement?.tagName !== "TEXTAREA") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const submit = () => {
    const t = text.trim();
    if (!t) return;
    core.sendText(t);
    setText("");
  };
  return (
    <div className="composer-wrap">
    <AttachChips />
    <div className="composer">
      <LogToggle />
      <AttachControls />
      <Launcher />
      <button
        className={`mic-btn ${hud === "listening" ? "live" : ""}`}
        title="Click to talk · hold to push-to-talk"
        onPointerDown={() => {
          holding.current = false;
          const t = window.setTimeout(() => { holding.current = true; core.pttStart(); }, 280);
          (window as any).__pttTimer = t;
        }}
        onPointerUp={() => {
          window.clearTimeout((window as any).__pttTimer);
          if (holding.current) core.pttEnd();
          else core.listenNow();
          holding.current = false;
        }}
        onPointerLeave={() => {
          window.clearTimeout((window as any).__pttTimer);
          if (holding.current) { core.pttEnd(); holding.current = false; }
        }}
        disabled={!connected}
      >
        <svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z" /></svg>
      </button>
      <input
        ref={inputRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && submit()}
        onPaste={(e) => {
          const files = Array.from(e.clipboardData.files);
          if (files.length) { e.preventDefault(); core.upload(files); }  // pasted screenshots / images
        }}
        placeholder={connected ? "Speak or type a command…" : "Reconnecting to core…"}
        disabled={!connected}
        autoFocus
      />
      {busy ? (
        <button className="send-btn stop" onClick={() => core.cancel()} title="Stop (Esc)">■</button>
      ) : (
        <button className="send-btn" onClick={submit} disabled={!text.trim()} title="Send (Enter)">➤</button>
      )}
    </div>
    </div>
  );
}

/** Under the orb: what he just said (small), then JARVIS's current line (large). Welcome text when idle and new. */
function Caption() {
  const caption = useStore((s) => s.caption);
  const hud = useStore((s) => s.hud);
  const messages = useStore((s) => s.messages);
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  const showUser = !!lastUser && (hud === "thinking" || hud === "speaking" || !!caption) && Date.now() - lastUser.at < 120_000;
  const fresh = messages.length === 0;
  // After he stops talking (or with voice off), keep his last reply under the orb, dimmed, for a while.
  const lastJarvis = [...messages].reverse().find((m) => m.role === "jarvis");
  const [, tick] = useState(0);
  useEffect(() => { const t = window.setInterval(() => tick((n) => n + 1), 5000); return () => window.clearInterval(t); }, []);
  const lingering = !caption && hud === "idle" && lastJarvis && !lastJarvis.pending && lastJarvis.text && Date.now() - lastJarvis.at < 60_000 ? lastJarvis : null;
  const linger = lingering ? speakableCaption(lingering.text) : "";
  return (
    <div className="caption-zone">
      <AnimatePresence>
        {showUser && (
          <motion.div key={lastUser!.id} className="caption-you" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.25 }}>
            “{lastUser!.text}”
          </motion.div>
        )}
      </AnimatePresence>
      <AnimatePresence mode="wait">
        {caption ? (
          <motion.div key={caption} className="caption" initial={{ opacity: 0, y: 8, filter: "blur(4px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} exit={{ opacity: 0, y: -6, filter: "blur(3px)" }} transition={{ duration: 0.22 }}>
            {caption}
          </motion.div>
        ) : linger ? (
          <motion.div key={`l_${lingering!.id}`} className="caption caption-rest" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.4 }}>
            {linger}
          </motion.div>
        ) : fresh && hud !== "thinking" && hud !== "listening" ? (
          <motion.div key="welcome" className="welcome" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <div className="welcome-title">Good {greeting()}, Stephen.</div>
            <div className="muted">Say “Hey Jarvis”, press ⌥⇧Space, or type below.</div>
            <div className="suggest">
              {["What's on my calendar today?", "Summarize my unread work email", "Any new Slack messages?", "What's the weather?"].map((s) => (
                <button key={s} className="chip clickable" onClick={() => core.sendText(s)}>{s}</button>
              ))}
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

const RIGHT_KEY = "jarvis.rightWidth";
const LEFT_KEY = "jarvis.leftWidth";
const CENTER_MIN = 520; // keep the reactor + conversation usable
type Side = "left" | "right";
const SIDES: Record<Side, { key: string; cssVar: string; min: number; other: string }> = {
  right: { key: RIGHT_KEY, cssVar: "--right-w", min: 340, other: "aside.left" },
  left: { key: LEFT_KEY, cssVar: "--left-w", min: 220, other: "aside.right" },
};
/** Clamp a side panel so the centre column keeps at least CENTER_MIN px next to the other panel. */
const clampSide = (side: Side, w: number) => {
  const s = SIDES[side];
  const other = document.querySelector(s.other)?.getBoundingClientRect().width ?? (side === "right" ? 250 : 380);
  return Math.round(Math.max(s.min, Math.min(w, window.innerWidth - other - CENTER_MIN)));
};

/** Restore a saved side-panel width (px) into its CSS variable (--left-w / --right-w). */
function applySideWidth(side: Side, w: number | null) {
  const root = document.documentElement;
  if (w == null) root.style.removeProperty(SIDES[side].cssVar);
  else root.style.setProperty(SIDES[side].cssVar, `${clampSide(side, w)}px`);
}
for (const side of ["left", "right"] as Side[]) {
  const saved = Number(localStorage.getItem(SIDES[side].key));
  if (saved > 0) applySideWidth(side, saved);
}

/** Drag handle on a side panel's inner edge. Drag to resize, double-click to reset. */
function PanelResizer({ side = "right" }: { side?: Side }) {
  const cfg = SIDES[side];
  const [drag, setDrag] = useState(false);
  const d = useRef<{ x0: number; moved: boolean } | null>(null);
  useEffect(() => {
    const onWin = () => { const s = Number(localStorage.getItem(cfg.key)); if (s > 0) applySideWidth(side, s); };
    window.addEventListener("resize", onWin);
    return () => window.removeEventListener("resize", onWin);
  }, [side]);
  const start = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    d.current = { x0: e.clientX, moved: false };
    setDrag(true);
    document.body.classList.add("resizing");
  };
  const move = (e: React.PointerEvent) => {
    const s = d.current;
    if (!s) return;
    if (!s.moved && Math.abs(e.clientX - s.x0) < 3) return; // a click / double-click is not a resize
    s.moved = true;
    const w = clampSide(side, side === "right" ? window.innerWidth - e.clientX : e.clientX);
    applySideWidth(side, w);
    localStorage.setItem(cfg.key, String(w));
  };
  // Always clear the drag state, even if the pointer is released off the handle / outside the window or
  // capture is lost; otherwise body.resizing keeps forcing the col-resize cursor everywhere.
  const end = (e?: React.PointerEvent) => {
    const el = e?.currentTarget as HTMLElement | undefined;
    if (el && e && el.hasPointerCapture?.(e.pointerId)) el.releasePointerCapture(e.pointerId);
    d.current = null;
    setDrag(false);
    document.body.classList.remove("resizing");
  };
  useEffect(() => {
    if (!drag) return;
    const stopAll = () => end();
    window.addEventListener("pointerup", stopAll, true);
    window.addEventListener("blur", stopAll);
    return () => { window.removeEventListener("pointerup", stopAll, true); window.removeEventListener("blur", stopAll); };
  }, [drag]);
  useEffect(() => () => document.body.classList.remove("resizing"), []);
  return (
    <div className={`panel-resizer pr-${side} ${drag ? "on" : ""}`} title="Drag to resize · double-click to reset"
      onPointerDown={start} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={() => end()}
      onDoubleClick={() => { localStorage.removeItem(cfg.key); applySideWidth(side, null); }} />
  );
}

/** Orb caption; reads "♪ NOW PLAYING" while the orb is the music visualizer (playing + otherwise idle). */
function StateLabel({ hud }: { hud: string }) {
  const [playing, setPlaying] = useState(!!media.nowPlaying?.playing);
  useEffect(() => media.subscribeNowPlaying(() => setPlaying(!!media.nowPlaying?.playing)), []);
  const music = playing && hud === "idle";
  return <div className={`state-label st-${hud} ${music ? "st-music" : ""}`}>{music ? "♪ NOW PLAYING" : STATE_TEXT[hud]}</div>;
}

export function Hud() {
  const hud = useStore((s) => s.hud);
  const asleep = useStore((s) => s.asleep);
  const powering = useStore((s) => s.powerOnAt > 0);
  const cards = useStore((s) => s.cards);
  const confirmPending = cards.some((c) => c.kind === "confirm" && (c.status ?? "pending") === "pending");
  return (
    <div className={`hud hud-${hud} ${confirmPending ? "hud-alert" : ""} ${asleep ? "hud-asleep" : ""} ${powering ? "hud-boot" : ""}`}>
      <div className="bg-grid" />
      <div className="bg-scan" />
      <div className="drag-bar" />
      <header className="top">
        <div className="brand">
          <img className="brand-logo" src="./brand/logo_64.png" alt="" draggable={false} />
          <span className="brand-mark">J.A.R.V.I.S.</span>
          <span className="brand-sub">JUST A RATHER VERY INTELLIGENT SYSTEM</span>
        </div>
        <Clock />
      </header>
      <StatusBar />
      <aside className="left">
        <PanelResizer side="left" />
        <Telemetry />
        <ActivityFeed />
        <MiniPlayer />
      </aside>
      <main className="center">
        <div className="stage">
          <div className="reactor-wrap">
            <Reactor />
            <StateLabel hud={hud} />
            <HoloForge />
            <HoloRadar />
            <HoloTube />
          </div>
          <Caption />
        </div>
        <Transcript />
        <Composer />
      </main>
      <aside className="right">
        <PanelResizer side="right" />
        <RightPanel />
      </aside>
      <Toasts />
      <DropZone />
      <Workbench />
      <ShowcaseHud />
      <SleepVeil />
      <PowerOn />
    </div>
  );
}

/** Asleep: the HUD dims, the orb breathes, and a quiet hint says how to wake it. Clicking powers it on. */
function SleepVeil() {
  const asleep = useStore((s) => s.asleep);
  const wake = useStore((s) => s.wakeAvailable && s.micEnabled);
  return (
    <AnimatePresence>
      {asleep && (
        <motion.div className="sleep-veil" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.25 } }}
          transition={{ duration: 1.4, ease: "easeInOut" }} onClick={() => core.toggleSleep()}>
          <div className="sleep-hint">
            <div className="sleep-t">{wake ? "Say “Morning, Jarvis”" : "Click to power on"}</div>
            <div className="sleep-s">{wake ? "or “Hey Jarvis” · ⌘⇧S" : "⌘⇧S"}</div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/** "Morning, Jarvis": a short boot sequence over the HUD (scan line, rings charging, system checks, flare). */
const BOOT_LINES = ["CORE", "NEURAL INTERFACE", "VOICE SYNTHESIS", "COMMS ARRAY", "MARKET FEEDS", "ALL SYSTEMS"];
function PowerOn() {
  const at = useStore((s) => s.powerOnAt);
  const [n, setN] = useState(0);
  const [c, setC] = useState({ x: 50, y: 40, r: 26 });  // centre the rings/flare on the orb (vw/vh, vmin)
  useEffect(() => {
    if (!at) return;
    setN(0);
    const el = document.querySelector(".reactor-wrap") as HTMLElement | null;
    if (el) {
      const b = el.getBoundingClientRect();
      const vmin = Math.min(innerWidth, innerHeight) / 100;
      setC({ x: ((b.left + b.width / 2) / innerWidth) * 100, y: ((b.top + b.height / 2) / innerHeight) * 100, r: Math.min(b.width, b.height) / 2 / vmin * 0.62 });
    }
    const ts = BOOT_LINES.map((_, i) => window.setTimeout(() => setN(i + 1), 380 + i * 360));
    return () => ts.forEach(window.clearTimeout);
  }, [at]);
  return createPortal(
    <AnimatePresence>
      {at > 0 && (
        <motion.div key={at} className="boot" initial={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.6 } }}>
          <motion.div className="boot-dark" initial={{ opacity: 0.92 }} animate={{ opacity: [0.92, 0.85, 0.6, 0] }}
            transition={{ duration: 3.4, times: [0, 0.5, 0.78, 1], ease: "easeInOut" }} />
          <motion.div className="boot-scan" initial={{ top: "-4%" }} animate={{ top: "104%" }} transition={{ duration: 1.5, ease: [0.5, 0, 0.3, 1] }} />
          <div className="boot-rings" style={{ left: `${c.x}vw`, top: `${c.y}vh`, ["--r" as any]: `${c.r}vmin` }}>
            {[0, 1, 2].map((i) => (
              <motion.i key={i} initial={{ scale: 0.2, opacity: 0 }} animate={{ scale: [0.2, 1, 1.12 + i * 0.3], opacity: [0, 0.9, 0] }}
                transition={{ duration: 1.3, delay: 1.5 + i * 0.32, ease: "easeOut" }} />
            ))}
          </div>
          <motion.div className="boot-flare" style={{ left: `${c.x}vw`, top: `${c.y}vh` }} initial={{ opacity: 0, scale: 0.4 }} animate={{ opacity: [0, 0, 1, 0], scale: [0.4, 0.4, 1.6, 2.4] }}
            transition={{ duration: 3.4, times: [0, 0.78, 0.82, 1] }} />
          <div className="boot-log">
            {BOOT_LINES.slice(0, n).map((l, i) => (
              <motion.div key={l} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} className={i === BOOT_LINES.length - 1 ? "last" : ""}>
                <span>{l}</span><i />{i === BOOT_LINES.length - 1 ? <b>ONLINE</b> : <b>OK</b>}
              </motion.div>
            ))}
          </div>
          <motion.div className="boot-title" style={{ left: `${c.x}vw`, top: `calc(${c.y}vh + ${c.r * 1.25}vmin)` }} initial={{ opacity: 0, letterSpacing: "1.2em" }} animate={{ opacity: [0, 0, 1, 1, 0], letterSpacing: ["1.2em", "1.2em", "0.55em", "0.5em", "0.5em"] }}
            transition={{ duration: 4, times: [0, 0.55, 0.75, 0.9, 1] }}>J.A.R.V.I.S.</motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

/** Progress strip while the preset showcase / support tour runs (Esc stops it, like any turn). */
function ShowcaseHud() {
  const sc = useStore((s) => s.showcase);
  const booting = useStore((s) => s.powerOnAt > 0);
  return createPortal(
    <AnimatePresence>
      {sc?.active && !booting && (
        <motion.div className="sc-hud" initial={{ opacity: 0, y: -14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}>
          <span className="sc-dot" />
          <span className="sc-mode">{sc.mode === "tour" ? "SUPPORT MODEL" : "YOUR DAY"}</span>
          <AnimatePresence mode="wait">
            <motion.span key={sc.label} className="sc-label" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}>{sc.label}</motion.span>
          </AnimatePresence>
          <span className="sc-bar">
            {Array.from({ length: Math.max(1, sc.total) }).map((_, i) => <i key={i} className={i < sc.step ? "on" : i === sc.step ? "cur" : ""} />)}
          </span>
          <button className="sc-stop" onClick={() => core.cancel()} title="Stop (Esc)">ESC</button>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

function RightPanel() {
  const cards = useStore((s) => s.cards);
  const tab = useStore((s) => s.rightTab);
  const b = useStore((s) => s.briefing);
  const setTab = (t: "briefing" | "displays") => useStore.getState().set({ rightTab: t });
  const sl = b.slack?.available ? b.slack.conversations.filter((c) => !c.bot).length + b.slack.mentions.length : 0;
  const n = (b.data ? b.data.todos.length + b.data.priority.length : 0) + sl;
  const unseen = useStore((s) => s.displaysUnseen);
  const pending = cards.filter((c) => c.kind === "confirm" && (c.status ?? "pending") === "pending");
  // New cards are added at the top: bring the list back to the top so the new one is in view.
  const displaysRef = useRef<HTMLDivElement>(null);
  const newest = cards[0]?.id;
  useEffect(() => {
    if (tab === "displays" && newest) displaysRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  }, [newest, tab]);
  return (
    <>
      <div className="panel-label right-label tabs">
        <button className={`rtab ${tab === "briefing" ? "on" : ""}`} onClick={() => setTab("briefing")}>
          BRIEFING {n > 0 && <span className="rtab-n">{n}</span>}
        </button>
        <button className={`rtab ${tab === "displays" ? "on" : ""}`} onClick={() => useStore.getState().set({ rightTab: "displays", displaysUnseen: 0 })}>
          DISPLAYS {cards.length > 0 && <span className={`rtab-n ${unseen > 0 && tab !== "displays" ? "fresh" : ""}`}>{cards.length}</span>}
        </button>
        {tab === "displays" && cards.length > 0 && (
          <button className="clear" onClick={() => {
            const keep = cards.filter((c) => c.kind === "confirm" && (c.status ?? "pending") === "pending");
            useStore.getState().set({ cards: keep, rightTab: keep.length ? "displays" : "briefing" });
          }}>CLEAR</button>
        )}
      </div>
      {tab === "briefing" && (
        <div className="cards">
          <AnimatePresence initial={false}>
            {pending.map((c, i) => <HoloCard key={c.id} card={c} index={i} />)}
          </AnimatePresence>
          <Briefing />
        </div>
      )}
      {/* Displays stays MOUNTED while Briefing is shown (just hidden): unmounting it would kill the music/video
          players (and the left mini player) whenever he glances at the briefing. */}
      <div className="cards" ref={displaysRef} style={tab === "displays" ? undefined : { display: "none" }}>
        <AnimatePresence initial={false}>
          {cards.map((c, i) => <HoloCard key={c.id} card={c} index={i} />)}
        </AnimatePresence>
        {!cards.length && <div className="cards-empty">Visual output will appear here.</div>}
      </div>
    </>
  );
}

function Toasts() {
  const toasts = useStore((s) => s.toasts);
  return (
    <div className="toasts">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div key={t.id} className={`toast ${t.error ? "err" : ""}`} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }}>
            <span>{t.text}</span>
            {(t.undoItem || t.onUndo) && (
              <button onClick={() => {
                if (t.onUndo) t.onUndo();
                else core.briefingAction(t.undoItem!, "undo");
                useStore.getState().dropToast(t.id);
              }}>UNDO</button>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
