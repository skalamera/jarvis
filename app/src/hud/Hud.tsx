import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useStore } from "../state/store";
import { core } from "../ws/core";
import { HoloCard } from "../cards/HoloCard";
import { MarkdownCard } from "../cards/Cards";
import { fmtEventTime } from "../cards/format";
import { Reactor } from "./Reactor";
import { AttachChips, AttachControls, DropZone, Workbench } from "./Workbench";
import { Briefing } from "./Briefing";

const STATE_TEXT: Record<string, string> = {
  idle: "STANDING BY",
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

export function setLogOpen(open: boolean) {
  localStorage.setItem("jarvis.logOpen", open ? "1" : "0");
  useStore.getState().set(open ? { logOpen: true, logUnseen: 0 } : { logOpen: false });
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
const CENTER_MIN = 520; // keep the reactor + conversation usable
const clampRight = (w: number) => {
  const left = document.querySelector("aside.left")?.getBoundingClientRect().width ?? 250;
  return Math.round(Math.max(340, Math.min(w, window.innerWidth - left - CENTER_MIN)));
};

/** Restore the saved right-panel width (px) into the --right-w CSS variable. */
function applyRightWidth(w: number | null) {
  const root = document.documentElement;
  if (w == null) root.style.removeProperty("--right-w");
  else root.style.setProperty("--right-w", `${clampRight(w)}px`);
}
{
  const saved = Number(localStorage.getItem(RIGHT_KEY));
  if (saved > 0) applyRightWidth(saved);
}

/** Drag handle on the right panel's left edge. Drag to resize, double-click to reset. */
function PanelResizer() {
  const [drag, setDrag] = useState(false);
  const d = useRef<{ x0: number; moved: boolean } | null>(null);
  useEffect(() => {
    const onWin = () => { const s = Number(localStorage.getItem(RIGHT_KEY)); if (s > 0) applyRightWidth(s); };
    window.addEventListener("resize", onWin);
    return () => window.removeEventListener("resize", onWin);
  }, []);
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
    const w = clampRight(window.innerWidth - e.clientX);
    applyRightWidth(w);
    localStorage.setItem(RIGHT_KEY, String(w));
  };
  const end = () => { d.current = null; setDrag(false); document.body.classList.remove("resizing"); };
  return (
    <div className={`panel-resizer ${drag ? "on" : ""}`} title="Drag to resize · double-click to reset"
      onPointerDown={start} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
      onDoubleClick={() => { localStorage.removeItem(RIGHT_KEY); applyRightWidth(null); }} />
  );
}

export function Hud() {
  const hud = useStore((s) => s.hud);
  const cards = useStore((s) => s.cards);
  const confirmPending = cards.some((c) => c.kind === "confirm" && (c.status ?? "pending") === "pending");
  return (
    <div className={`hud hud-${hud} ${confirmPending ? "hud-alert" : ""}`}>
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
        <Telemetry />
        <ActivityFeed />
      </aside>
      <main className="center">
        <div className="stage">
          <div className="reactor-wrap">
            <Reactor />
            <div className={`state-label st-${hud}`}>{STATE_TEXT[hud]}</div>
          </div>
          <Caption />
        </div>
        <Transcript />
        <Composer />
      </main>
      <aside className="right">
        <PanelResizer />
        <RightPanel />
      </aside>
      <Toasts />
      <DropZone />
      <Workbench />
    </div>
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
      {tab === "briefing" ? (
        <div className="cards">
          <AnimatePresence initial={false}>
            {pending.map((c, i) => <HoloCard key={c.id} card={c} index={i} />)}
          </AnimatePresence>
          <Briefing />
        </div>
      ) : (
        <div className="cards" ref={displaysRef}>
          <AnimatePresence initial={false}>
            {cards.map((c, i) => <HoloCard key={c.id} card={c} index={i} />)}
          </AnimatePresence>
          {!cards.length && <div className="cards-empty">Visual output will appear here.</div>}
        </div>
      )}
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
