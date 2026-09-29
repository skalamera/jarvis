import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { core } from "../ws/core";
import type { Card } from "../types";
import { acctTag, bytes, dayLabel, fmtDate, fmtEventTime, initials, mimeLabel } from "./format";

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
  if (!msgs.length) return <div className="muted">No messages match.</div>;
  return (
    <div className="list">
      {card.data?.result_size_estimate > msgs.length && (
        <div className="list-meta">Showing {msgs.length} of ~{Number(card.data.result_size_estimate).toLocaleString()}</div>
      )}
      {msgs.filter((m) => !gone.has(m.id)).map((m, i) => (
        <div key={m.id} className={`row email-row ${m.unread ? "unread" : ""}`} style={{ animationDelay: `${i * 45}ms` }}
          onClick={() => core.direct("gmail_read", { account: card.account, message_id: m.id })}>
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
            {m.unread && <button title="Mark read" onClick={() => { core.direct("gmail_modify", { account: card.account, message_ids: [m.id], mark_read: true }); m.unread = false; setGone(new Set(gone)); }}>✓</button>}
            <button title="Archive" onClick={() => { core.direct("gmail_modify", { account: card.account, message_ids: [m.id], archive: true }); setGone(new Set([...gone, m.id])); }}>⇩</button>
            <TrashIcon account={card.account} id={m.id} />
          </div>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------------ single email
export function EmailView({ card }: { card: Card }) {
  const m = card.data;
  return (
    <div className="email-view">
      <div className="email-head">
        <div className="avatar lg">{initials(m.from_name || m.from_email || "?")}</div>
        <div>
          <div className="from">{m.from_name} <span className="muted">&lt;{m.from_email}&gt;</span></div>
          <div className="muted small">to {m.to}{m.cc ? `, cc ${m.cc}` : ""} · {fmtDate(m.internalDate || m.date)}</div>
        </div>
      </div>
      <div className="email-body">{m.body || m.snippet}</div>
      {m.body_truncated && <div className="muted small">… truncated</div>}
      {m.attachments?.length > 0 && (
        <div className="chips">{m.attachments.map((a: any) => <span key={a.attachmentId} className="chip">📎 {a.filename} <span className="muted">{bytes(a.size)}</span></span>)}</div>
      )}
      <div className="card-actions">
        <Btn onClick={() => core.sendText(`Draft a reply to the email from ${m.from_name || m.from_email} about "${m.subject}" (message id ${m.id}, ${card.account} account).`)}>Draft reply</Btn>
        <Btn tone="ghost" onClick={() => core.direct("gmail_read_thread", { account: card.account, thread_id: m.threadId })}>Full thread</Btn>
        <Btn tone="ghost" onClick={() => core.direct("gmail_modify", { account: card.account, message_ids: [m.id], archive: true })}>Archive</Btn>
        <TrashBtn account={card.account} id={m.id} />
      </div>
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
      {p.type === "calendar_delete" && <div className="preview-mail"><div><b>{p.summary}</b> · {fmtDate(p.start)}</div>{p.attendees?.length > 0 && <div className="muted">Attendees will be notified.</div>}</div>}
      {p.type === "drive_share" && <div className="preview-mail"><div><b>{p.name}</b></div><div>→ {p.email} ({p.role})</div></div>}
      {p.type === "drive_trash" && <div className="preview-mail"><div><b>{p.name}</b></div></div>}
      {p.type === "command" && <pre className="preview-cmd">{p.command}</pre>}
      {status === "pending" ? (
        <>
          <div className="card-actions">
            <Btn tone={danger ? "red" : "amber"} onClick={() => core.confirm(card.id, true)}>Authorize</Btn>
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
