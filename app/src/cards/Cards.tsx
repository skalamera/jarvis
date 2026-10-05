import { MailComposer, type ComposeInit } from "./MailComposer";
import { useContext, useEffect, useRef, useState } from "react";
import { useStore } from "../state/store";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { core } from "../ws/core";
import type { Card } from "../types";
import { acctTag, bytes, dayLabel, fmtDate, fmtEventTime, initials, mimeLabel } from "./format";
import { BookingPreview } from "./TravelCards";
import { TradeOrderPreview } from "./TradeCards";
import { MaxCtx } from "./HoloCard";
import { CalendarXL } from "./CalendarXL";

const Btn = ({ children, onClick, tone = "cyan", disabled }: { children: React.ReactNode; onClick?: () => void; tone?: "cyan" | "amber" | "red" | "ghost"; disabled?: boolean }) => (
  <button className={`hbtn hbtn-${tone}`} onClick={onClick} disabled={disabled}>{children}</button>
);

/** Delete in place: first click arms it ("Confirm delete?"), second click trashes right away (no AUTHORIZE card:
 *  clicking this specific email twice is the confirmation). Recoverable from Trash; the toast offers UNDO. */
function useTrash(account: string | null | undefined, ids: string[]) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = window.setTimeout(() => setArmed(false), 4000);
    return () => window.clearTimeout(t);
  }, [armed]);
  useEffect(() => {
    if (!busy) return;
    const h = (e: Event) => {
      const d = (e as CustomEvent).detail;
      if (d?.op === "gmail_trash_now" && !d.ok) { setBusy(false); setArmed(false); }
    };
    window.addEventListener("jarvis:direct_result", h);
    return () => window.removeEventListener("jarvis:direct_result", h);
  }, [busy]);
  const click = () => {
    if (busy) return;
    if (!armed) return setArmed(true);
    setBusy(true);
    core.direct("gmail_trash_now", { account, message_ids: ids });
  };
  return { armed, busy, click, cancel: () => setArmed(false) };
}

function TrashIcon({ account, id }: { account?: string | null; id: string }) {
  const t = useTrash(account, [id]);
  return (
    <button className={`row-trash ${t.armed ? "armed" : ""}`} title={t.armed ? "Click again to move to Trash" : "Delete"}
      onClick={t.click} onMouseLeave={t.cancel} disabled={t.busy}>
      {t.busy ? "…" : t.armed ? "Delete?" : "✕"}
    </button>
  );
}

function TrashBtn({ account, id }: { account?: string | null; id: string }) {
  const t = useTrash(account, [id]);
  return (
    <>
      <button className={`hbtn hbtn-red ${t.armed ? "armed" : ""}`} onClick={t.click} disabled={t.busy}>
        {t.busy ? "Deleting…" : t.armed ? "Confirm delete" : "Trash"}
      </button>
      {t.armed && !t.busy && <button className="hbtn hbtn-ghost" onClick={t.cancel}>Keep</button>}
    </>
  );
}

// ------------------------------------------------------------------------ email list
export function EmailList({ card }: { card: Card }) {
  const msgs: any[] = card.data?.messages ?? [];
  const [gone, setGone] = useState<Set<string>>(new Set());
  const [q, setQ] = useState(card.data?.query && card.data.query !== "in:inbox" ? card.data.query : "");
  const [busy, setBusy] = useState(false);
  const [compose, setCompose] = useState(false);
  const acct = card.account || "personal";
  const load = async (account: string, query: string) => {
    setBusy(true);
    const r = await core.workspace("mail_inbox", { account, query: query.trim() || "in:inbox", max_results: 25 });
    setBusy(false);
    if (r.ok) {
      const s = useStore.getState();
      s.set({ cards: s.cards.map((c) => (c.id === card.id ? { ...c, account, title: query.trim() ? `${query} · ${account}` : `Inbox · ${account}`, data: { ...r.result, query: query.trim() || "in:inbox" } } : c)) });
      setGone(new Set());
    } else useStore.getState().toast({ text: r.error || "Couldn't load mail.", error: true });
  };
  return (
    <div className="list">
      <div className="ml-bar" onClick={(e) => e.stopPropagation()}>
        <div className="cx-toggles">
          {["personal", "work"].map((a) => (
            <button key={a} className={`tog ${acct === a ? "on" : ""} ml-${a}`} onClick={() => a !== acct && load(a, q)}>{a === "personal" ? "Personal" : "Work"}</button>
          ))}
        </div>
        <form className="cs-q" onSubmit={(e) => { e.preventDefault(); load(acct, q); }}>
          <span>⌕</span><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search mail (Gmail search works: from:, has:attachment…)" />
          {busy && <span className="cx-spin" />}
        </form>
        <button className="cs-go" onClick={() => load(acct, q)} title="Refresh">⟳</button>
        <button className="cs-go mc-new" onClick={() => setCompose(true)}>✎ Compose</button>
      </div>
      {compose && <MailComposer init={{ account: acct, mode: "new" }} onClose={() => setCompose(false)} />}
      {!msgs.length && <div className="muted">No messages match.</div>}
      {card.data?.result_size_estimate > msgs.length && (
        <div className="list-meta">Showing {msgs.length} of ~{Number(card.data.result_size_estimate).toLocaleString()}</div>
      )}
      {msgs.filter((m) => !gone.has(m.id)).map((m, i) => (
        <div key={m.id} className={`row email-row ${m.unread ? "unread" : ""}`} style={{ animationDelay: `${i * 45}ms` }}
          onClick={() => core.direct("gmail_read", { account: acct, message_id: m.id })}>
          <div className="avatar">{initials(m.from_name || m.from_email)}</div>
          <div className="row-main">
            <div className="row-top">
              <span className="from">{m.from_name || m.from_email}</span>
              <span className="when">{fmtDate(m.internalDate)}</span>
            </div>
            <div className="subject">{m.subject}</div>
            <div className="snippet">{m.snippet}</div>
          </div>
          <div className="row-actions" onClick={(e) => e.stopPropagation()}>
            {m.unread && <button title="Mark read" onClick={() => { core.direct("gmail_modify", { account: acct, message_ids: [m.id], mark_read: true }); m.unread = false; setGone(new Set(gone)); }}>✓</button>}
            <button title="Archive" onClick={() => { core.direct("gmail_modify", { account: acct, message_ids: [m.id], archive: true }); setGone(new Set([...gone, m.id])); }}>⇩</button>
            <TrashIcon account={acct} id={m.id} />
          </div>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------ single email
function MailBody({ account, id, text }: { account: string; id: string; text: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [h, setH] = useState(240);
  const ref = useRef<HTMLIFrameElement>(null);
  useEffect(() => { core.workspace("mail_html", { account, message_id: id }).then((r) => setHtml(r.ok ? r.result.html : "")); }, [account, id]);
  if (html === null) return <div className="email-body">{text}</div>;
  if (!html) return <div className="email-body">{text}</div>;
  return (
    <iframe ref={ref} className="mail-html" sandbox="allow-same-origin allow-popups" style={{ height: h }} title="message"
      srcDoc={`<base target="_blank"><style>html,body{margin:0;padding:12px;background:#fff;color:#202124;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;word-wrap:break-word}img{max-width:100%;height:auto}</style>${html}`}
      onLoad={() => { const d = ref.current?.contentDocument; if (d) setH(Math.min(4000, d.documentElement.scrollHeight + 8)); }} />
  );
}

export function EmailView({ card }: { card: Card }) {
  const m = card.data;
  const acct = card.account || m.account || "personal";
  const [compose, setCompose] = useState<ComposeInit | null>(null);
  const [opening, setOpening] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  const open = async (mode: "reply" | "reply_all" | "forward", ai = false) => {
    setOpening(mode + (ai ? "ai" : ""));
    const r = await core.workspace("mail_context", { account: acct, message_id: m.id, mode });
    setOpening("");
    if (!r.ok) { useStore.getState().toast({ text: r.error || "Couldn't open the composer.", error: true }); return; }
    setCompose({ ...r.result, account: acct, mode, ai });
    setTimeout(() => boxRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
  };
  const spin = (k: string) => opening === k ? <span className="cx-spin" /> : null;
  return (
    <div className="email-view">
      <div className="email-head">
        <div className="avatar lg">{initials(m.from_name || m.from_email || "?")}</div>
        <div>
          <div className="subject-lg">{m.subject}</div>
          <div className="from">{m.from_name} <span className="muted">&lt;{m.from_email}&gt;</span></div>
          <div className="muted small">to {m.to}{m.cc ? `, cc ${m.cc}` : ""} · {fmtDate(m.internalDate || m.date)}</div>
        </div>
      </div>
      <MailBody account={acct} id={m.id} text={m.body || m.snippet} />
      {m.attachments?.length > 0 && (
        <div className="chips">{m.attachments.map((a: any) => <span key={a.attachmentId} className="chip">📎 {a.filename} <span className="muted">{bytes(a.size)}</span></span>)}</div>
      )}
      {!compose && (
        <div className="card-actions mail-actions" onClick={(e) => e.stopPropagation()}>
          <Btn onClick={() => open("reply")}>{spin("reply")}↩ Reply</Btn>
          <Btn onClick={() => open("reply", true)}>{spin("replyai")}✨ AI Reply</Btn>
          <Btn tone="ghost" onClick={() => open("reply_all")}>{spin("reply_all")}↩↩ Reply all</Btn>
          <Btn tone="ghost" onClick={() => open("forward")}>{spin("forward")}↪ Forward</Btn>
          <Btn tone="ghost" onClick={() => core.direct("gmail_read_thread", { account: acct, thread_id: m.threadId })}>Full thread</Btn>
          <Btn tone="ghost" onClick={() => core.direct("gmail_modify", { account: acct, message_ids: [m.id], archive: true })}>Archive</Btn>
          <TrashBtn account={acct} id={m.id} />
        </div>
      )}
      <div ref={boxRef}>{compose && <MailComposer init={compose} onClose={() => setCompose(null)} />}</div>
    </div>
  );
}

export function ThreadView({ card }: { card: Card }) {
  const msgs: any[] = card.data?.messages ?? [];
  return (
    <div className="thread">
      {msgs.map((m, i) => (
        <details key={m.id} open={i === msgs.length - 1} className="thread-msg">
          <summary><span className="from">{m.from_name}</span><span className="when">{fmtDate(m.internalDate)}</span></summary>
          <div className="email-body">{m.body || m.snippet}</div>
        </details>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------ editable draft
export function DraftEditor({ card }: { card: Card }) {
  const d = card.data;
  const [to, setTo] = useState(d.to || "");
  const [cc, setCc] = useState(d.cc || "");
  const [subject, setSubject] = useState(d.subject || "");
  const [body, setBody] = useState(d.body || "");
  const [state, setState] = useState<"clean" | "dirty" | "saving" | "saved" | "error" | "discarded">("clean");
  useEffect(() => {
    const h = (e: Event) => {
      const det = (e as CustomEvent).detail;
      if (det.draft_id === d.draft_id) setState(det.ok ? "saved" : "error");
    };
    window.addEventListener("jarvis:draft_saved", h);
    return () => window.removeEventListener("jarvis:draft_saved", h);
  }, [d.draft_id]);
  const dirty = (f: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => { f(e.target.value); setState("dirty"); };
  const save = () => { setState("saving"); core.updateDraft({ account: card.account, draft_id: d.draft_id, to, cc, subject, body }); };
  if (state === "discarded") return <div className="muted">Draft discarded.</div>;
  return (
    <div className="draft">
      <div className="draft-badge">DRAFT · NOT SENT · {d.from}</div>
      <label>To<input value={to} onChange={dirty(setTo)} /></label>
      <label>Cc<input value={cc} onChange={dirty(setCc)} /></label>
      <label>Subject<input value={subject} onChange={dirty(setSubject)} /></label>
      <textarea value={body} onChange={dirty(setBody)} rows={Math.min(16, Math.max(6, body.split("\n").length + 1))} />
      <div className="card-actions">
        <Btn onClick={save} disabled={state === "saving" || state === "clean" || state === "saved"}>
          {state === "saving" ? "Saving…" : state === "saved" ? "Saved ✓" : state === "error" ? "Retry save" : "Save changes"}
        </Btn>
        <Btn tone="amber" disabled={state === "dirty" || state === "saving"}
          onClick={() => core.direct("gmail_send_draft", { account: card.account, draft_id: d.draft_id })}>Send…</Btn>
        <Btn tone="ghost" onClick={() => { core.direct("gmail_delete_draft", { account: card.account, draft_id: d.draft_id }); setState("discarded"); }}>Discard</Btn>
      </div>
      {state === "dirty" && <div className="muted small">Save your edits before sending.</div>}
    </div>
  );
}

// ------------------------------------------------------------------------ confirmation
const DANGER = new Set(["gmail_delete_permanently", "gmail_trash", "calendar_delete", "drive_trash"]);

export function ConfirmCard({ card }: { card: Card }) {
  const p = card.data?.preview ?? {};
  const kind: string = card.data?.kind ?? "";
  const status = card.status ?? "pending";
  const danger = DANGER.has(kind) || p.danger;
  return (
    <div className={`confirm ${danger ? "danger" : ""} status-${status}`}>
      <div className="confirm-head">
        <span className="confirm-glyph">⚠</span>
        <span>{status === "pending" ? "AUTHORIZATION REQUIRED" : status.toUpperCase()}</span>
      </div>
      <div className="confirm-title">{card.title}</div>
      {p.type === "email_send" && (
        <div className="preview-mail">
          <div><b>From</b> {p.from}</div>
          <div><b>To</b> {p.to}</div>
          {p.cc && <div><b>Cc</b> {p.cc}</div>}
          {p.bcc && <div><b>Bcc</b> {p.bcc}</div>}
          <div><b>Subject</b> {p.subject}</div>
          <div className="preview-body">{p.body}</div>
        </div>
      )}
      {(p.type === "email_trash" || p.type === "email_delete") && (
        <div className="preview-list">
          {p.sample?.map((s: any, i: number) => <div key={i}>· <b>{s.from_name}</b> — {s.subject}</div>)}
          {p.count > (p.sample?.length ?? 0) && <div className="muted">…and {p.count - p.sample.length} more</div>}
          {p.danger && <div className="danger-text">{p.danger}</div>}
        </div>
      )}
      {p.type === "calendar_invite" && (
        <div className="preview-mail">
          <div><b>Event</b> {p.summary}</div>
          <div><b>When</b> {fmtDate(p.start)} → {fmtDate(p.end)}</div>
          <div><b>Invites</b> {p.attendees?.join(", ")}</div>
          {p.location && <div><b>Where</b> {p.location}</div>}
        </div>
      )}
      {p.type === "calendar_update" && <div className="preview-mail"><div><b>{p.summary}</b>{p.old_summary && p.old_summary !== p.summary && <span className="muted"> (was {p.old_summary})</span>}</div><div>{p.old_start !== p.start && <><span className="muted" style={{ textDecoration: "line-through" }}>{fmtDate(p.old_start)}</span> → </>}{fmtDate(p.start)}</div>{p.attendees?.length > 0 && <div className="muted">{p.attendees.length} guest(s) will be notified.</div>}</div>}
      {p.type === "calendar_delete" && <div className="preview-mail"><div><b>{p.summary}</b> · {fmtDate(p.start)}</div>{p.attendees?.length > 0 && <div className="muted">Attendees will be notified.</div>}</div>}
      {p.type === "drive_share" && <div className="preview-mail"><div><b>{p.name}</b></div><div>→ {p.email} ({p.role})</div></div>}
      {p.type === "drive_trash" && <div className="preview-mail"><div><b>{p.name}</b></div></div>}
      {p.type === "command" && <pre className="preview-cmd">{p.command}</pre>}
      {p.type === "booking" && <BookingPreview p={p} />}
      {p.type === "trade" && <TradeOrderPreview p={p} />}
      {status === "pending" ? (
        <>
          <div className="card-actions">
            <Btn tone={danger ? "red" : "amber"} onClick={() => core.confirm(card.id, true)}>{p.type === "booking" ? (p.action_label || `Book · ${p.total}`) : p.type === "trade" ? (p.action_label || "Place order") : "Authorize"}</Btn>
            <Btn tone="ghost" onClick={() => core.confirm(card.id, false)}>Cancel</Btn>
          </div>
          <div className="muted small">Or say “confirm” / “cancel”.</div>
        </>
      ) : status === "failed" ? (
        <div className="danger-text">{card.result?.error}</div>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------------ calendar
export function CalendarCard({ card }: { card: Card }) {
  const mx = useContext(MaxCtx);
  if (mx?.max) return <CalendarXL defaultAccount={card.account ?? undefined} />;
  const events: any[] = card.data?.events ?? [];
  if (!events.length) return <div className="muted">Nothing scheduled.</div>;
  const groups: Record<string, any[]> = {};
  for (const e of events) (groups[dayLabel(e.start)] ||= []).push(e);
  const now = Date.now();
  return (
    <div className="cal">
      {Object.entries(groups).map(([day, evs]) => (
        <div key={day}>
          <div className="cal-day">{day}</div>
          {evs.map((e) => {
            const live = !e.all_day && new Date(e.start).getTime() <= now && new Date(e.end).getTime() > now;
            return (
              <div key={e.id} className={`cal-ev ${live ? "live" : ""} ${e.my_status === "declined" ? "declined" : ""}`}>
                <div className="cal-time">{fmtEventTime(e.start, e.all_day, e.end)}</div>
                <div className="cal-main">
                  <div className="cal-title">{e.summary}</div>
                  <div className="muted small">
                    {e.location && <span>{e.location} · </span>}
                    {e.attendees?.length > 0 && <span>{e.attendees.length} guests</span>}
                  </div>
                </div>
                {e.hangout && <button className="hbtn hbtn-cyan sm" onClick={() => core.openExternal(e.hangout)}>Join</button>}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------ workspace files (launcher)
export function LaunchFilesCard({ card }: { card: Card }) {
  const files: any[] = card.data?.files ?? [];
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("all");
  const kinds = ["all", ...Array.from(new Set(files.map((f) => f.kind)))];
  const rows = files.filter((f) => (kind === "all" || f.kind === kind) && f.filename.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="lf">
      <form className="cs-bar" onClick={(e) => e.stopPropagation()} onSubmit={(e) => e.preventDefault()}>
        <div className="cs-q"><span>⌕</span><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter files" /></div>
        <select className="cs-in" value={kind} onChange={(e) => setKind(e.target.value)}>{kinds.map((k) => <option key={k} value={k}>{k}</option>)}</select>
      </form>
      {!rows.length && <div className="muted">No files.</div>}
      <div className="list">
        {rows.map((f) => (
          <button key={f.id} className="row file-row lf-row" onClick={(e) => { e.stopPropagation(); core.launchTool("file_open", { artifact_id: f.id }); }}>
            <div className="file-type">{String(f.kind).toUpperCase().slice(0, 5)}</div>
            <div className="row-main">
              <div className="subject">{f.filename}</div>
              <div className="muted small">{bytes(f.size)} · v{f.version} · {fmtDate(new Date(f.updated_at * 1000).toISOString())}</div>
            </div>
            <span className="pl-row-go">›</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------ drive
export function FilesCard({ card }: { card: Card }) {
  const files: any[] = card.data?.files ?? [];
  if (!files.length) return <div className="muted">No files found.</div>;
  return (
    <div className="list">
      {files.map((f, i) => (
        <div key={f.id} className="row file-row" style={{ animationDelay: `${i * 40}ms` }}>
          <div className="file-type">{mimeLabel(f.mimeType)}</div>
          <div className="row-main">
            <div className="subject">{f.name}</div>
            <div className="muted small">{f.owners?.[0]?.displayName} · {fmtDate(f.modifiedTime)} {f.size ? `· ${bytes(f.size)}` : ""}</div>
          </div>
          <div className="row-actions show">
            {/google-apps\.(document|spreadsheet|presentation)|^text\//.test(f.mimeType) && (
              <button title="Read" onClick={() => core.direct("drive_read_text", { account: card.account, file_id: f.id })}>◉</button>
            )}
            {f.webViewLink && <button title="Open" onClick={() => core.openExternal(f.webViewLink)}>↗</button>}
          </div>
        </div>
      ))}
    </div>
  );
}

export function DocumentCard({ card }: { card: Card }) {
  const d = card.data;
  return (
    <div>
      <pre className="doc-text">{d.text || d.error}</pre>
      {d.truncated && <div className="muted small">… truncated</div>}
      {d.webViewLink && <div className="card-actions"><Btn tone="ghost" onClick={() => core.openExternal(d.webViewLink)}>Open in Google ↗</Btn></div>}
    </div>
  );
}

// ------------------------------------------------------------------------ notice / markdown
export function NoticeCard({ card }: { card: Card }) {
  return (
    <div className="notice">
      <span className="notice-dot" />
      <span>{card.data?.text}</span>
      {card.data?.url && <button className="hbtn hbtn-ghost sm" onClick={() => core.openExternal(card.data.url)}>Open ↗</button>}
    </div>
  );
}

export function MarkdownCard({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
        a: ({ href, children }) => <a href={href} onClick={(e) => { e.preventDefault(); if (href) core.openExternal(href); }}>{children}</a>,
      }}>{text}</ReactMarkdown>
    </div>
  );
}

export { acctTag };
