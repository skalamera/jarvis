import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useStore } from "../state/store";
import { core } from "../ws/core";
import { fmtDate } from "../cards/format";
import type { BriefItem, BriefMessage } from "../types";

const KIND_ICON: Record<string, string> = { reply: "↩", pay: "$", review: "◉", schedule: "◷", deadline: "⚑", task: "▸" };

function gmailUrl(m: BriefMessage) {
  return `https://mail.google.com/mail/?authuser=${encodeURIComponent(m.email)}#all/${m.threadId}`;
}

/** "2026-08-12" / "07/27/2026" / "Oct 1, 2026" -> "AUG 12" (year only if not this year); free text passes through. */
function fmtDue(s: string) {
  const t = s.trim();
  const d = /^\d{4}-\d{2}-\d{2}$/.test(t) ? new Date(`${t}T12:00:00`) : new Date(t);
  if (isNaN(d.getTime()) || !/\d/.test(t)) return t.toUpperCase();
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: sameYear ? undefined : "numeric" }).toUpperCase();
}

function ago(ts?: number) {
  if (!ts) return "";
  const s = (Date.now() - ts * 1000) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return fmtDate(ts * 1000);
}

/** Reply composer: prefilled from the model's suggestion; Send or Save as Gmail draft. */
function ReplyBox({ item, onClose }: { item: BriefItem; onClose: () => void }) {
  const latest = [...item.messages].sort((a, b) => b.internalDate - a.internalDate)[0];
  const [body, setBody] = useState(item.reply_suggestion || "");
  const [replyAll, setReplyAll] = useState(false);
  const busy = useStore((s) => s.briefBusy[item.id]);
  return (
    <div className="brief-reply" onClick={(e) => e.stopPropagation()}>
      <div className="brief-reply-to">
        To <b>{latest.from_name}</b> · <span className="muted">Re: {latest.subject.replace(/^re:\s*/i, "")}</span>
      </div>
      <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={5} autoFocus placeholder="Write a reply…" />
      <div className="brief-reply-bar">
        <label className="brief-check"><input type="checkbox" checked={replyAll} onChange={(e) => setReplyAll(e.target.checked)} /> Reply all</label>
        <span className="sys-spacer" />
        <button className="hbtn hbtn-ghost sm" onClick={onClose}>Cancel</button>
        <button className="hbtn hbtn-ghost sm" disabled={!body.trim() || !!busy}
          onClick={() => core.briefingAction(item.id, "reply_draft", { body, reply_all: replyAll })}>Save draft</button>
        <button className="hbtn hbtn-cyan sm" disabled={!body.trim() || !!busy}
          onClick={() => core.briefingAction(item.id, "reply_send", { body, reply_all: replyAll })}>
          {busy === "reply_send" ? "Sending…" : "Send ➤"}
        </button>
      </div>
    </div>
  );
}

function Actions({ item, onReply, replyFirst }: { item: BriefItem; onReply: () => void; replyFirst?: boolean }) {
  const [armed, setArmed] = useState(false); // trash needs a second click
  const busy = useStore((s) => s.briefBusy[item.id]);
  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(false), 3000);
    return () => window.clearTimeout(t);
  }, [armed]);
  const act = (a: string) => (e: React.MouseEvent) => { e.stopPropagation(); core.briefingAction(item.id, a); };
  return (
    <div className="brief-actions" onClick={(e) => e.stopPropagation()}>
      <button className={`bact ${replyFirst ? "primary" : ""}`} title="Reply" onClick={onReply} disabled={!!busy}>↩ Reply</button>
      <button className="bact" title="Open in Gmail" onClick={() => core.openExternal(gmailUrl(item.messages[0]))}>✉ View</button>
      <button className="bact" title="Mark done: archive the email(s)" onClick={act("archive")} disabled={!!busy}>✓ Done</button>
      <button className="bact" title="Mark as read, keep in inbox" onClick={act("mark_read")} disabled={!!busy}>◌ Read</button>
      <button className={`bact danger ${armed ? "armed" : ""}`} title={armed ? "Click again to move to Trash" : "Delete (move to Trash)"}
        disabled={!!busy} onClick={(e) => { e.stopPropagation(); if (armed) core.briefingAction(item.id, "trash"); else setArmed(true); }}>
        {armed ? "Confirm delete?" : "🗑 Delete"}
      </button>
      <button className="bact icon" title="Dismiss from briefing (Gmail untouched)" onClick={act("dismiss")} disabled={!!busy}>✕</button>
    </div>
  );
}

function MsgMeta({ item }: { item: BriefItem }) {
  const m = item.messages[0];
  return (
    <div className="brief-meta">
      <span className={`acct acct-${m.account}`}>{m.account.toUpperCase()}</span>
      <span className="brief-from">{m.from_name}</span>
      {item.messages.length > 1 && <span className="brief-count">+{item.messages.length - 1}</span>}
      {item.due && <span className="brief-due">DUE {fmtDue(item.due)}</span>}
      <span className="brief-when">{fmtDate(Math.max(...item.messages.map((x) => x.internalDate)))}</span>
    </div>
  );
}

function Row({ item, variant }: { item: BriefItem; variant: "todo" | "priority" | "topic" }) {
  const [replying, setReplying] = useState(false);
  const busy = useStore((s) => s.briefBusy[item.id]);
  const title = variant === "todo" ? item.title : variant === "priority" ? item.messages[0].subject : item.headline;
  const text = variant === "todo" ? item.detail : variant === "priority" ? item.reason : item.summary;
  useEffect(() => {
    const h = (e: Event) => { const d = (e as CustomEvent).detail; if (d.item_id === item.id && d.ok) setReplying(false); };
    window.addEventListener("jarvis:briefing_result", h);
    return () => window.removeEventListener("jarvis:briefing_result", h);
  }, [item.id]);
  return (
    <motion.div layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: 40, height: 0, marginBottom: 0, paddingTop: 0, paddingBottom: 0 }}
      transition={{ duration: 0.22 }} className={`brief-row brief-${variant} ${item.urgency === "high" ? "urgent" : ""} ${busy ? "busy" : ""}`}>
      <div className="brief-top">
        {variant === "todo" && <span className={`brief-kind k-${item.kind}`}>{KIND_ICON[item.kind ?? "task"] ?? "▸"}</span>}
        <div className="brief-title">
          {title}
          {item.new && <span className="brief-new">NEW</span>}
        </div>
      </div>
      <div className="brief-text">{text}</div>
      <MsgMeta item={item} />
      {replying ? <ReplyBox item={item} onClose={() => setReplying(false)} />
        : <Actions item={item} onReply={() => setReplying(true)} replyFirst={!!item.reply_suggestion || item.kind === "reply" || variant === "priority"} />}
    </motion.div>
  );
}

function Section({ label, count, children }: { label: string; count: number; children?: React.ReactNode }) {
  return (
    <div className="brief-section">
      <div className="brief-sec-label">{label} <span className="brief-sec-count">{count}</span></div>
      {children}
    </div>
  );
}

export function Briefing() {
  const b = useStore((s) => s.briefing);
  const [showAll, setShowAll] = useState(false);
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 30_000); return () => clearInterval(t); }, []);
  const d = b.data;
  const todos = d?.todos ?? [];
  const prio = d?.priority ?? [];
  const topics = (d?.topics ?? []).filter((t) => t.items.length);
  const shownTodos = showAll ? todos : todos.slice(0, 4);
  const nTopicItems = topics.reduce((n, t) => n + t.items.length, 0);

  return (
    <div className="briefing">
      <div className="brief-hero">
        {d ? (
          <div className="brief-headline">
            <span className="hl">{todos.length}</span> to-do{todos.length === 1 ? "" : "s"}
            {" · "}<span className="hl">{prio.length}</span> priority
            {" · "}<span className="hl">{topics.length}</span> topic{topics.length === 1 ? "" : "s"}
          </div>
        ) : (
          <div className="brief-headline muted">{b.refreshing ? "Analysing your inboxes…" : "No briefing yet."}</div>
        )}
        <button className={`brief-refresh ${b.refreshing ? "spin" : ""}`} onClick={() => core.briefingRefresh()} disabled={b.refreshing}
          title="Re-scan both inboxes">
          {b.refreshing ? "SCANNING" : d ? `UPDATED ${ago(d.generated_at).toUpperCase()}` : "SCAN"} <span className="ri">⟳</span>
        </button>
      </div>
      {b.error && <div className="brief-error">Briefing failed: {b.error}</div>}
      {b.refreshing && !d && <div className="brief-scan"><i /><i /><i /></div>}

      {todos.length > 0 && (
        <Section label="SUGGESTED TO-DOS" count={todos.length}>
          <AnimatePresence initial={false}>{shownTodos.map((t) => <Row key={t.id} item={t} variant="todo" />)}</AnimatePresence>
          {todos.length > 4 && (
            <button className="brief-more" onClick={() => setShowAll(!showAll)}>
              {showAll ? "▴ Show less" : `▾ Show ${todos.length - 4} more`}
            </button>
          )}
        </Section>
      )}

      {prio.length > 0 && (
        <Section label="PRIORITY" count={prio.length}>
          <AnimatePresence initial={false}>{prio.map((p) => <Row key={p.id} item={p} variant="priority" />)}</AnimatePresence>
        </Section>
      )}

      {topics.length > 0 && (
        <Section label="CATCH UP" count={nTopicItems}>
          {topics.map((tp) => (
            <div key={tp.id} className="brief-topic">
              <div className="brief-topic-head"><span className="brief-emoji">{tp.emoji}</span>{tp.title}</div>
              <AnimatePresence initial={false}>{tp.items.map((it) => <Row key={it.id} item={it} variant="topic" />)}</AnimatePresence>
            </div>
          ))}
        </Section>
      )}

      {d && !todos.length && !prio.length && !topics.length && !b.refreshing && (
        <div className="cards-empty">Inbox zero on everything that matters, sir.</div>
      )}
      {d && <div className="brief-foot">Scanned {d.scanned} recent emails across both inboxes · AI triage can make mistakes</div>}
    </div>
  );
}
