import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { core } from "../ws/core";
import { useStore } from "../state/store";
import type { Card, SlackConv, SlackData, SlackMsg, SlackPost } from "../types";

/** Slack's aubergine + the four brand accents, tuned for the HUD's dark glass. */
const SLACK_MARK = (
  <svg viewBox="0 0 54 54" className="sl-mark" aria-hidden>
    <path fill="#36C5F0" d="M19.7 0a5.4 5.4 0 0 0 0 10.8h5.4V5.4A5.4 5.4 0 0 0 19.7 0m0 14.4H5.4a5.4 5.4 0 0 0 0 10.8h14.3a5.4 5.4 0 0 0 0-10.8" />
    <path fill="#2EB67D" d="M54 19.8a5.4 5.4 0 0 0-10.8 0v5.4h5.4a5.4 5.4 0 0 0 5.4-5.4m-14.4 0V5.4a5.4 5.4 0 0 0-10.8 0v14.4a5.4 5.4 0 0 0 10.8 0" />
    <path fill="#ECB22E" d="M34.2 54a5.4 5.4 0 0 0 0-10.8h-5.4v5.4a5.4 5.4 0 0 0 5.4 5.4m0-14.4h14.4a5.4 5.4 0 0 0 0-10.8H34.2a5.4 5.4 0 0 0 0 10.8" />
    <path fill="#E01E5A" d="M0 34.2a5.4 5.4 0 0 0 10.8 0v-5.4H5.4A5.4 5.4 0 0 0 0 34.2m14.4 0v14.4a5.4 5.4 0 0 0 10.8 0V34.2a5.4 5.4 0 0 0-10.8 0" />
  </svg>
);

export function slackAgo(ts: string) {
  const s = Date.now() / 1000 - parseFloat(ts);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  const d = new Date(parseFloat(ts) * 1000);
  return s < 6 * 86400 ? d.toLocaleDateString([], { weekday: "short" }) : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

const HUES = ["#e01e5a", "#36c5f0", "#2eb67d", "#ecb22e", "#b18cff", "#ff8a4c", "#4cd6c0"];
function hue(name: string) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return HUES[h % HUES.length];
}

export function Avatar({ src, name, size = 30, group }: { src?: string; name: string; size?: number; group?: boolean }) {
  const [bad, setBad] = useState(false);
  const initials = name.replace(/^[#@]/, "").split(/[\s,]+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("");
  const style = { width: size, height: size, fontSize: size * 0.38 };
  if (src && !bad) return <img className="sl-av" src={src} alt="" style={style} onError={() => setBad(true)} />;
  return (
    <span className={`sl-av sl-av-mono ${group ? "group" : ""}`} style={{ ...style, background: group ? undefined : hue(name) }}>
      {group ? "👥" : initials || "?"}
    </span>
  );
}

/** Text with @you / @here highlighted and links shown as chips. */
function Body({ m, clamp = 4 }: { m: SlackMsg; clamp?: number }) {
  const parts = m.text.split(/(@you\b|@here\b|@channel\b|@everyone\b)/g);
  return (
    <>
      <div className="sl-text" style={{ WebkitLineClamp: clamp }}>
        {parts.map((p, i) => (/^@(you|here|channel|everyone)$/.test(p) ? <mark key={i} className="sl-mention">{p}</mark> : p))}
      </div>
      {m.links?.length > 0 && (
        <div className="sl-links">
          {m.links.slice(0, 2).map((l, i) => (
            <button key={i} className="sl-link" title={l.url} onClick={(e) => { e.stopPropagation(); core.openExternal(l.url); }}>
              ↗ {l.label}
            </button>
          ))}
        </div>
      )}
    </>
  );
}

function open(url: string, web?: string) {
  // slack:// opens the desktop app on that message; the web permalink is the fallback
  core.openExternal(url.startsWith("slack://") ? url : web || url);
}

function Dismiss({ id, briefing }: { id: string; briefing?: boolean }) {
  if (!briefing) return null;
  return (
    <button className="bact icon" title="Dismiss from briefing (Slack stays untouched)"
      onClick={(e) => { e.stopPropagation(); core.briefingAction(id, "dismiss"); }}>✕</button>
  );
}

function ConvRow({ c, briefing }: { c: SlackConv; briefing?: boolean }) {
  const [open_, setOpen] = useState(false);
  const last = c.messages[c.messages.length - 1];
  const multi = c.messages.length > 1;
  const key = `slack:${c.id}:${c.latest_ts}`;
  return (
    <motion.div layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: 40, height: 0, marginBottom: 0 }}
      transition={{ duration: 0.2 }} className={`sl-conv ${c.bot ? "bot" : "human"}`} onClick={() => multi && setOpen(!open_)}>
      <div className="sl-conv-head">
        <div className="sl-av-wrap">
          <Avatar src={c.avatar || last.avatar} name={c.title} group={c.kind === "group"} size={34} />
          {!c.bot && <i className="sl-dot" />}
        </div>
        <div className="sl-conv-main">
          <div className="sl-conv-top">
            <b className="sl-name">{c.title}</b>
            {c.kind === "group" && <span className="sl-tag">GROUP</span>}
            {c.bot && <span className="sl-tag app">APP</span>}
            <span className="sl-when">{slackAgo(c.latest_ts)}</span>
            <span className="sl-badge">{c.unread}</span>
          </div>
          {!open_ && (
            <div className="sl-preview">
              {c.kind === "group" && <span className="sl-who">{last.user.split(" ")[0]}: </span>}
              <Body m={last} clamp={c.bot ? 2 : 3} />
            </div>
          )}
        </div>
      </div>
      <AnimatePresence initial={false}>
        {open_ && (
          <motion.div className="sl-thread" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}>
            {c.messages.map((m) => (
              <div key={m.ts} className="sl-tmsg">
                <Avatar src={m.avatar} name={m.user} size={22} />
                <div>
                  <div className="sl-tmeta"><b>{m.user}</b> <span className="sl-when">{slackAgo(m.ts)}</span></div>
                  <Body m={m} clamp={8} />
                </div>
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
      <div className="sl-actions" onClick={(e) => e.stopPropagation()}>
        <button className="bact primary" onClick={() => open(c.url, c.web_url)} title="Open in the Slack app">
          {SLACK_MARK} Open in Slack
        </button>
        {multi && <button className="bact" onClick={() => setOpen(!open_)}>{open_ ? "▴ Less" : `▾ All ${c.messages.length}`}</button>}
        <span className="sys-spacer" />
        <Dismiss id={key} briefing={briefing} />
      </div>
    </motion.div>
  );
}

function PostRow({ p, kind, briefing }: { p: SlackPost; kind: "mention" | "post"; briefing?: boolean }) {
  return (
    <motion.div layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: 40, height: 0, marginBottom: 0 }}
      transition={{ duration: 0.2 }} className={`sl-post ${kind} ${p.broadcast ? "broadcast" : ""}`} onClick={() => open(p.url, p.permalink)}>
      <Avatar src={p.avatar} name={p.user} size={30} />
      <div className="sl-post-main">
        <div className="sl-conv-top">
          <b className="sl-name">{p.user}</b>
          <span className="sl-chan">{p.channel}</span>
          {p.broadcast && <span className="sl-tag bc">📣 ANNOUNCEMENT</span>}
          {kind === "mention" && p.unread && <span className="sl-tag new">UNREAD</span>}
          <span className="sl-when">{slackAgo(p.ts)}</span>
        </div>
        <Body m={p} clamp={kind === "mention" ? 4 : 3} />
        {kind === "post" && ((p.reactions ?? 0) > 0 || (p.replies ?? 0) > 0) && <Reactions p={p} />}
      </div>
      <div onClick={(e) => e.stopPropagation()}><Dismiss id={`slack:${p.id}`} briefing={briefing} /></div>
    </motion.div>
  );
}

/** Standard emoji as chips; the workspace's custom emoji (no image access) fold into one "+N" chip. */
function Reactions({ p }: { p: SlackPost }) {
  const known = (p.top_reactions ?? []).filter((r) => r.emoji);
  const shown = known.reduce((n, r) => n + r.count, 0);
  const rest = Math.max(0, (p.reactions ?? 0) - shown);
  const customNames = (p.top_reactions ?? []).filter((r) => !r.emoji).map((r) => `:${r.name}:`).join(" ");
  return (
    <div className="sl-reacts">
      {known.map((r, i) => <span key={i} className="sl-react" title={`:${r.name}:`}>{r.emoji} {r.count}</span>)}
      {rest > 0 && <span className="sl-react more" title={customNames || "more reactions"}>{known.length ? "+" : "✨ "}{rest}</span>}
      {(p.replies ?? 0) > 0 && <span className="sl-replies">💬 {p.replies} repl{p.replies === 1 ? "y" : "ies"}</span>}
    </div>
  );
}

function Group({ label, n, children }: { label: string; n: number; children: React.ReactNode }) {
  return (
    <div className="sl-group">
      <div className="sl-group-label">{label} <span className="sl-group-n">{n}</span></div>
      {children}
    </div>
  );
}

/** The whole Slack digest: used as a Briefing section and as a DISPLAYS card. */
export function SlackDigest({ d, briefing }: { d: SlackData; briefing?: boolean }) {
  const [showBots, setShowBots] = useState(false);
  const [allPosts, setAllPosts] = useState(false);
  const postCap = briefing && !allPosts ? 3 : 8;
  const people = d.conversations.filter((c) => !c.bot);
  const bots = d.conversations.filter((c) => c.bot);
  const botUnread = bots.reduce((n, c) => n + c.unread, 0);
  const empty = !people.length && !bots.length && !d.mentions.length && !d.channels.length;
  if (empty) return <div className="sl-empty">{SLACK_MARK} All caught up on Slack, sir.</div>;
  return (
    <div className="sl-digest">
      {people.length > 0 && (
        <Group label="DIRECT MESSAGES" n={people.reduce((n, c) => n + c.unread, 0)}>
          <AnimatePresence initial={false}>{people.map((c) => <ConvRow key={c.id + c.latest_ts} c={c} briefing={briefing} />)}</AnimatePresence>
        </Group>
      )}
      {d.mentions.length > 0 && (
        <Group label="MENTIONS" n={d.mentions.length}>
          <AnimatePresence initial={false}>{d.mentions.slice(0, 6).map((m) => <PostRow key={m.id} p={m} kind="mention" briefing={briefing} />)}</AnimatePresence>
        </Group>
      )}
      {d.channels.length > 0 && (
        <Group label="AROUND THE COMPANY" n={d.channels.length}>
          <AnimatePresence initial={false}>{d.channels.slice(0, postCap).map((p) => <PostRow key={p.id} p={p} kind="post" briefing={briefing} />)}</AnimatePresence>
          {d.channels.length > postCap && (
            <button className="brief-more" onClick={() => setAllPosts(true)}>▾ Show {Math.min(d.channels.length, 8) - postCap} more</button>
          )}
          {briefing && allPosts && d.channels.length > 3 && (
            <button className="brief-more" onClick={() => setAllPosts(false)}>▴ Show less</button>
          )}
        </Group>
      )}
      {bots.length > 0 && (
        <Group label="APPS" n={botUnread}>
          {!showBots ? (
            <button className="sl-bots" onClick={() => setShowBots(true)}>
              <span className="sl-bot-avs">{bots.slice(0, 5).map((c) => <Avatar key={c.id} src={c.avatar} name={c.title} size={20} />)}</span>
              {botUnread} unread from {bots.map((c) => c.title).join(", ")} <span className="sl-more">▾</span>
            </button>
          ) : (
            <AnimatePresence initial={false}>{bots.map((c) => <ConvRow key={c.id + c.latest_ts} c={c} briefing={briefing} />)}</AnimatePresence>
          )}
        </Group>
      )}
    </div>
  );
}

/** Briefing tab section header + digest. */
export function SlackBriefing() {
  const b = useStore((s) => s.briefing);
  const d = b.slack;
  if (!d || !d.available) return null;
  const people = d.conversations.filter((c) => !c.bot).reduce((n, c) => n + c.unread, 0);
  const count = people + d.mentions.length;
  return (
    <div className="brief-section sl-section">
      <div className="brief-sec-label">
        <span className="sl-label">{SLACK_MARK} SLACK</span>
        {count > 0 && <span className="brief-sec-count">{count}</span>}
      </div>
      {b.slackError && <div className="brief-error">Slack: {b.slackError}</div>}
      <SlackDigest d={d} briefing />
    </div>
  );
}

/** DISPLAYS card for slack_updates / slack_search results. */
export function SlackCard({ card }: { card: Card }) {
  const data: any = card.data;
  if (Array.isArray(data?.results)) {
    const rs: SlackPost[] = data.results;
    return (
      <div className="sl-digest">
        <div className="sl-search-head">{SLACK_MARK} {data.total ?? rs.length} result{(data.total ?? rs.length) === 1 ? "" : "s"} for <b>{data.query}</b></div>
        {rs.length ? rs.map((p) => <PostRow key={p.id} p={p} kind="mention" />) : <div className="sl-empty">No messages matched.</div>}
      </div>
    );
  }
  return <SlackDigest d={data as SlackData} />;
}
