import { useEffect, useMemo, useRef, useState } from "react";
import { core } from "../ws/core";
import { useStore } from "../state/store";
import type { Card } from "../types";
import { fmtDate } from "./format";

/* Pylon ticket cards. Every change is a click on THIS ticket's card (click = confirmation), routed to Core's
   click-only Pylon ops (never the model). Status / team / assignee show an UNDO toast. Replies need a second
   "Confirm send" click because they email the customer. */

type Opt = { teams: { id: string; name: string }[]; users: { id: string; name: string }[];
  statuses: { slug: string; label: string; category: string }[]; linear_teams: { id: string; key: string; name: string }[]; me: string };

let OPTS: Opt | null = null;
let optsWaiters: ((o: Opt) => void)[] = [];
function useOptions(): Opt | null {
  const [o, setO] = useState<Opt | null>(OPTS);
  useEffect(() => {
    if (OPTS) return;
    const first = optsWaiters.length === 0;
    optsWaiters.push(setO);
    if (first) core.pylon("pylon_options", {}).then((r) => {
      if (r.ok) { OPTS = r.result; optsWaiters.forEach((f) => f(OPTS!)); }
      optsWaiters = [];
    });
  }, []);
  return o;
}

const STATE_TONE: Record<string, string> = {
  new: "st-new", waiting_on_you: "st-you", waiting_on_customer: "st-cust", on_hold: "st-hold", closed: "st-closed",
};
const tone = (slug: string, cat?: string) => STATE_TONE[slug] || STATE_TONE[cat || ""] || "st-hold";
const PRI: Record<string, string> = { urgent: "pri-urgent", high: "pri-high", medium: "pri-med", low: "pri-low" };

const ago = (iso?: string) => {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
};

function StatusPill({ t }: { t: any }) {
  return <span className={`pill ${tone(t.state)}`}>{t.state_label}</span>;
}

function PriPill({ p }: { p?: string }) {
  if (!p) return null;
  return <span className={`pill pri ${PRI[p] || ""}`}>{p.toUpperCase()}</span>;
}

// ---------------------------------------------------------------------------------------------------------------
// list

export function PylonList({ card }: { card: Card }) {
  const d = card.data || {};
  const [tickets, setTickets] = useState<any[]>(d.tickets || []);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => setTickets(d.tickets || []), [card.data]);
  if (!tickets.length) return <div className="muted">No tickets match.</div>;
  const update = (t: any) => setTickets((xs) => xs.map((x) => (x.id === t.id ? { ...x, ...t } : x)));
  return (
    <div className="pylon-list">
      {d.total_matched > tickets.length && (
        <div className="list-meta">Showing {tickets.length} most recent of {d.total_matched} · click a ticket to act on it</div>
      )}
      {tickets.map((t) => (
        <div key={t.id} className={`py-row ${open === t.id ? "open" : ""}`}>
          <div className="py-row-head" onClick={() => setOpen(open === t.id ? null : t.id)}>
            <span className="py-num">#{t.number}</span>
            <div className="py-main">
              <div className="py-title">{t.title}</div>
              <div className="py-meta">
                <span className="py-acct">{t.account?.name || "No account"}</span>
                {t.requester?.name && <span>· {t.requester.name}</span>}
                <span>· {ago(t.latest_message_time || t.updated_at)}</span>
              </div>
            </div>
            <div className="py-pills">
              <PriPill p={t.priority} />
              <StatusPill t={t} />
            </div>
          </div>
          {open === t.id && <TicketBody t={t} onChange={update} compact />}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// single ticket

export function PylonTicket({ card }: { card: Card }) {
  const [t, setT] = useState<any>(card.data || {});
  useEffect(() => setT(card.data || {}), [card.data]);
  return (
    <div className="pylon-ticket">
      <div className="py-head">
        <div className="py-head-l">
          <div className="py-meta">
            <span className="py-acct">{t.account?.name || "No account"}</span>
            {t.requester?.name && <span>· {t.requester.name}</span>}
            <span>· opened {fmtDate(t.created_at)}</span>
          </div>
        </div>
        <div className="py-pills"><PriPill p={t.priority} /><StatusPill t={t} /></div>
      </div>
      <TicketBody t={t} onChange={(n) => setT((x: any) => ({ ...x, ...n }))} />
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// shared body: facts, thread, actions

type Panel = null | "reply" | "note" | "snooze" | "linear" | "link";

function TicketBody({ t, onChange, compact }: { t: any; onChange: (t: any) => void; compact?: boolean }) {
  const o = useOptions();
  const [busy, setBusy] = useState<string>("");
  const [panel, setPanel] = useState<Panel>(null);
  const [msgs, setMsgs] = useState<any[] | null>(t.messages ?? null);
  const [showThread, setShowThread] = useState(!compact);
  const [linked, setLinked] = useState<any[]>(t.external_issues || []);
  const toast = useStore((s) => s.toast);

  useEffect(() => { if (t.messages) setMsgs(t.messages); }, [t.messages]);
  useEffect(() => {
    if (showThread && msgs === null) core.pylon("pylon_thread", { issue_id: t.id }).then((r) => setMsgs(r.ok ? r.result.messages : []));
  }, [showThread]);

  const run = async (op: string, args: any, label: string, after?: (r: any) => void) => {
    setBusy(label);
    const r = await core.pylon(op, { issue_id: t.id, ...args });
    setBusy("");
    if (!r.ok) { toast({ text: r.error || "Pylon action failed.", error: true }); return false; }
    const res = r.result || {};
    if (res.ticket) onChange(res.ticket);
    after?.(res);
    const undo = res.undo;
    toast({ text: res.text || "Done.", onUndo: undo ? () => { core.pylon(undo.op, undo.args).then((u) => {
      if (u.ok && u.result?.ticket) onChange(u.result.ticket);
      toast(u.ok ? { text: "Undone." } : { text: u.error || "Undo failed.", error: true });
    }); } : undefined });
    return true;
  };

  return (
    <div className="py-body">
      {!compact && t.snippet && !msgs?.length && <div className="py-snippet">{t.snippet}</div>}
      <div className="py-facts">
        <Pick label="STATUS" value={t.state} disabled={!o || !!busy}
          options={(o?.statuses || []).map((s) => ({ v: s.slug, l: s.label }))}
          onPick={(v) => run("pylon_set_status", { state: v }, "status")} />
        <Pick label="TEAM" value={t.team?.id || ""} disabled={!o || !!busy}
          options={[{ v: "", l: "No team" }, ...(o?.teams || []).map((x) => ({ v: x.id, l: x.name }))]}
          onPick={(v) => run("pylon_set_team", { team_id: v }, "team")} />
        <Pick label="ASSIGNEE" value={t.assignee?.id || ""} disabled={!o || !!busy} search
          options={[{ v: "", l: "Unassigned" }, ...(o?.users || []).map((x) => ({ v: x.id, l: x.id === o?.me ? `${x.name} (me)` : x.name }))]}
          onPick={(v) => run("pylon_assign", { user_id: v }, "assignee")} />
      </div>
      {linked.length > 0 && (
        <div className="py-linked">
          {linked.map((l: any) => (
            <button key={l.id} className="chip link" onClick={() => l.link && window.jarvis?.open(l.link)}>🔗 {l.identifier || l.source} {l.title ? `· ${l.title}` : ""}</button>
          ))}
        </div>
      )}
      <div className="py-actions">
        <button className="bact primary" disabled={!!busy} onClick={() => setPanel(panel === "reply" ? null : "reply")}>↩ Reply</button>
        <button className="bact" disabled={!!busy} onClick={() => setPanel(panel === "note" ? null : "note")}>✎ Note</button>
        <button className="bact" disabled={!!busy} onClick={() => setPanel(panel === "snooze" ? null : "snooze")}>⏰ Snooze</button>
        <button className="bact" disabled={!!busy} onClick={() => setPanel(panel === "linear" ? null : "linear")}>＋ Linear</button>
        <button className="bact" disabled={!!busy} onClick={() => setPanel(panel === "link" ? null : "link")}>🔗 Link</button>
        {compact && <button className="bact icon" title="Open as its own card" onClick={() => core.pylon("pylon_open", { number: t.number })}>⤢</button>}
        <button className="bact icon" title="Open in Pylon" onClick={() => window.jarvis?.open(t.link)}>↗</button>
        {busy && <span className="py-busy">working…</span>}
      </div>
      {panel === "reply" && <ReplyBox t={t} run={run} close={() => setPanel(null)} />}
      {panel === "note" && <NoteBox run={run} close={() => setPanel(null)} />}
      {panel === "snooze" && <SnoozeBox run={run} close={() => setPanel(null)} />}
      {panel === "linear" && <LinearBox t={t} o={o} run={run} close={() => setPanel(null)} onLinked={(li) => setLinked((x) => [...x, { ...li, link: li.url }])} />}
      {panel === "link" && <LinkBox run={run} close={() => setPanel(null)} onLinked={(li) => setLinked((x) => [...x, { ...li, link: li.url }])} />}
      <button className="py-thread-toggle" onClick={() => setShowThread(!showThread)}>
        {showThread ? "▾" : "▸"} THREAD {msgs ? `(${msgs.length})` : ""}
      </button>
      {showThread && (
        <div className="py-thread">
          {msgs === null && <div className="muted small">Loading…</div>}
          {msgs?.map((m) => (
            <div key={m.id} className={`py-msg ${m.private ? "private" : m.from_customer ? "customer" : "team"}`}>
              <div className="py-msg-head">
                <b>{m.author || (m.from_customer ? "Customer" : "Support")}</b>
                {m.private && <span className="pill st-hold">INTERNAL</span>}
                <span className="muted">{fmtDate(m.at)}</span>
              </div>
              <div className="py-msg-text">{m.text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------------
// controls

function Pick({ label, value, options, onPick, disabled, search }: {
  label: string; value: string; options: { v: string; l: string }[]; onPick: (v: string) => void; disabled?: boolean; search?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", h);
    return () => window.removeEventListener("mousedown", h);
  }, [open]);
  const cur = options.find((x) => x.v === value)?.l || (value ? "…" : options[0]?.l);
  const shown = useMemo(() => options.filter((x) => !q || x.l.toLowerCase().includes(q.toLowerCase())), [options, q]);
  return (
    <div className={`py-pick ${open ? "open" : ""}`} ref={ref}>
      <span className="py-pick-l">{label}</span>
      <button className="py-pick-v" disabled={disabled} title={cur} onClick={() => { setOpen(!open); setQ(""); }}><span>{cur}</span> <i>▾</i></button>
      {open && (
        <div className="py-menu">
          {search && <input autoFocus placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />}
          <div className="py-menu-list">
            {shown.map((x) => (
              <button key={x.v || "_none"} className={x.v === value ? "on" : ""} onClick={() => { setOpen(false); if (x.v !== value) onPick(x.v); }}>{x.l}</button>
            ))}
            {!shown.length && <div className="muted small">No match</div>}
          </div>
        </div>
      )}
    </div>
  );
}

type Run = (op: string, args: any, label: string, after?: (r: any) => void) => Promise<boolean>;

function useDraft(issueId: string, kind: "reply" | "linear") {
  const [loading, setLoading] = useState(false);
  const get = async (steer = "") => {
    setLoading(true);
    const r = await core.pylon("pylon_draft", { issue_id: issueId, kind, steer });
    setLoading(false);
    if (!r.ok) useStore.getState().toast({ text: r.error || "Draft failed.", error: true });
    return r.ok ? r.result : null;
  };
  return { loading, get };
}

function ReplyBox({ t, run, close }: { t: any; run: Run; close: () => void }) {
  const [text, setText] = useState("");
  const [to, setTo] = useState<string>(t.requester?.email || "");
  const [steer, setSteer] = useState("");
  const [armed, setArmed] = useState(false);
  const d = useDraft(t.id, "reply");
  const draft = async () => { const r = await d.get(steer); if (r) { setText(r.reply); if (r.to?.[0]) setTo(r.to[0]); } };
  useEffect(() => { draft(); }, []);
  useEffect(() => { setArmed(false); }, [text, to]);
  const emDash = text.includes("—");
  return (
    <div className="py-panel">
      <div className="py-panel-head">REPLY TO CUSTOMER <span className="muted">as Stephen · emails the customer</span></div>
      <label className="py-field"><span>To</span><input value={to} onChange={(e) => setTo(e.target.value)} placeholder="customer@…" /></label>
      <textarea rows={9} value={d.loading && !text ? "Drafting a reply…" : text} disabled={d.loading && !text} onChange={(e) => setText(e.target.value)} />
      <div className="py-steer">
        <input value={steer} onChange={(e) => setSteer(e.target.value)} placeholder="Tell JARVIS how to change the draft (optional)" onKeyDown={(e) => e.key === "Enter" && draft()} />
        <button className="bact" disabled={d.loading} onClick={draft}>{d.loading ? "Drafting…" : "↻ Redraft"}</button>
      </div>
      {emDash && <div className="py-warn">Contains an em dash; replace it before sending.</div>}
      <div className="py-panel-actions">
        <button className={`bact ${armed ? "danger armed" : "primary"}`} disabled={!text.trim() || !to.trim() || emDash || d.loading}
          onClick={() => { if (!armed) return setArmed(true); run("pylon_reply", { text, to_emails: [to.trim()] }, "reply", close); }}>
          {armed ? "Confirm send" : "Send reply"}
        </button>
        <button className="bact" onClick={close}>Cancel</button>
      </div>
    </div>
  );
}

function NoteBox({ run, close }: { run: Run; close: () => void }) {
  const [text, setText] = useState("");
  return (
    <div className="py-panel">
      <div className="py-panel-head">INTERNAL NOTE <span className="muted">not visible to the customer</span></div>
      <textarea rows={4} autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="Note for the team…" />
      <div className="py-panel-actions">
        <button className="bact primary" disabled={!text.trim()} onClick={() => run("pylon_note", { text }, "note", close)}>Add note</button>
        <button className="bact" onClick={close}>Cancel</button>
      </div>
    </div>
  );
}

function SnoozeBox({ run, close }: { run: Run; close: () => void }) {
  const at = (h: number, dayOffset = 0, weekday?: number) => {
    const d = new Date();
    if (weekday !== undefined) d.setDate(d.getDate() + ((weekday - d.getDay() + 7) % 7 || 7));
    else d.setDate(d.getDate() + dayOffset);
    d.setHours(h, 0, 0, 0);
    return d;
  };
  const inHours = (n: number) => new Date(Date.now() + n * 3600_000);
  const presets: [string, Date][] = [
    ["3 hours", inHours(3)], ["Tomorrow 9 AM", at(9, 1)], ["In 2 days", at(9, 2)], ["Next Monday 9 AM", at(9, 0, 1)], ["In 1 week", at(9, 7)],
  ];
  const [custom, setCustom] = useState("");
  const go = (d: Date) => run("pylon_snooze", { until: d.toISOString() }, "snooze", close);
  return (
    <div className="py-panel">
      <div className="py-panel-head">SNOOZE UNTIL</div>
      <div className="py-chips">
        {presets.map(([l, d]) => <button key={l} className="bact" onClick={() => go(d)} title={d.toLocaleString()}>{l}</button>)}
      </div>
      <div className="py-steer">
        <input type="datetime-local" value={custom} onChange={(e) => setCustom(e.target.value)} />
        <button className="bact primary" disabled={!custom} onClick={() => go(new Date(custom))}>Snooze</button>
        <button className="bact" onClick={close}>Cancel</button>
      </div>
    </div>
  );
}

function LinearBox({ t, o, run, close, onLinked }: { t: any; o: Opt | null; run: Run; close: () => void; onLinked: (li: any) => void }) {
  const [team, setTeam] = useState("HAD");
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [kind, setKind] = useState("");
  const [steer, setSteer] = useState("");
  const d = useDraft(t.id, "linear");
  const draft = async () => { const r = await d.get(steer); if (r) { setTeam(r.team_key); setTitle(r.title); setDesc(r.description); setKind(r.linear_kind); } };
  useEffect(() => { draft(); }, []);
  const teams = o?.linear_teams?.length ? o.linear_teams : [{ id: "", key: "HAD", name: "Bugs" }, { id: "", key: "PROD", name: "Feature Requests" }];
  return (
    <div className="py-panel">
      <div className="py-panel-head">NEW LINEAR TICKET <span className="muted">{kind === "bug" ? "🐞 bug" : kind === "feature_request" ? "✨ feature request" : ""} · linked to #{t.number}</span></div>
      <div className="py-row2">
        <label className="py-field"><span>Team</span>
          <select value={team} onChange={(e) => setTeam(e.target.value)}>
            {teams.map((x) => <option key={x.key} value={x.key}>{x.key} · {x.name}</option>)}
          </select>
        </label>
        <label className="py-field grow"><span>Title</span><input value={d.loading && !title ? "Drafting…" : title} onChange={(e) => setTitle(e.target.value)} /></label>
      </div>
      <textarea rows={10} value={d.loading && !desc ? "Drafting from the thread…" : desc} disabled={d.loading && !desc} onChange={(e) => setDesc(e.target.value)} />
      <div className="py-steer">
        <input value={steer} onChange={(e) => setSteer(e.target.value)} placeholder="Tell JARVIS how to change the draft (optional)" onKeyDown={(e) => e.key === "Enter" && draft()} />
        <button className="bact" disabled={d.loading} onClick={draft}>{d.loading ? "Drafting…" : "↻ Redraft"}</button>
      </div>
      <div className="py-hint">Linking moves the ticket to On Hold (Pylon automation).</div>
      <div className="py-panel-actions">
        <button className="bact primary" disabled={!title.trim() || d.loading}
          onClick={() => run("pylon_linear_create", { team_key: team, title, description: desc }, "linear", (r) => { if (r.linear) onLinked(r.linear); close(); })}>
          Create &amp; link
        </button>
        <button className="bact" onClick={close}>Cancel</button>
      </div>
    </div>
  );
}

function LinkBox({ run, close, onLinked }: { run: Run; close: () => void; onLinked: (li: any) => void }) {
  const [key, setKey] = useState("");
  const ok = /^[A-Za-z][A-Za-z0-9]{1,9}-\d+$/.test(key.trim());
  return (
    <div className="py-panel">
      <div className="py-panel-head">LINK EXISTING LINEAR TICKET</div>
      <div className="py-steer">
        <input autoFocus value={key} onChange={(e) => setKey(e.target.value)} placeholder="HAD-1234" onKeyDown={(e) => e.key === "Enter" && ok && run("pylon_linear_link", { key }, "link", (r) => { if (r.linear) onLinked(r.linear); close(); })} />
        <button className="bact primary" disabled={!ok} onClick={() => run("pylon_linear_link", { key }, "link", (r) => { if (r.linear) onLinked(r.linear); close(); })}>Link</button>
        <button className="bact" onClick={close}>Cancel</button>
      </div>
      <div className="py-hint">Linking moves the ticket to On Hold (Pylon automation).</div>
    </div>
  );
}
