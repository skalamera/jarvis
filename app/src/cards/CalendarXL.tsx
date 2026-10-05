import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { core } from "../ws/core";

/** Expanded calendar: Month / Week / Day views over BOTH Google calendars, with add / edit / delete.
 *  Talks to Core's click-only cal_* ops (core.workspace), never the model. */

type Acct = "personal" | "work";
type Ev = {
  id: string; account: Acct; summary: string; start: string; end: string; all_day: boolean;
  location?: string; description?: string; hangout?: string; htmlLink?: string; attendees?: string[];
  my_status?: string | null; recurring?: boolean;
};
type View = "month" | "week" | "day";
type Draft = { id?: string; account: Acct; origAccount?: Acct; summary: string; date: string; endDate: string;
  startT: string; endT: string; all_day: boolean; location: string; description: string; attendees?: string[]; recurring?: boolean };

const ACCT_LABEL: Record<Acct, string> = { personal: "Personal", work: "Work" };
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const HOUR_PX = 48;

const pad = (n: number) => String(n).padStart(2, "0");
const ymd = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const hm = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const sameDay = (a: Date, b: Date) => ymd(a) === ymd(b);
/** Local ISO with offset, e.g. 2026-10-04T09:00:00-04:00 */
function iso(d: Date): string {
  const o = -d.getTimezoneOffset(), s = o >= 0 ? "+" : "-";
  return `${ymd(d)}T${hm(d)}:00${s}${pad(Math.floor(Math.abs(o) / 60))}:${pad(Math.abs(o) % 60)}`;
}
const parseDay = (s: string) => { const [y, m, d] = s.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };
const evStart = (e: Ev) => (e.all_day ? parseDay(e.start) : new Date(e.start));
const evEnd = (e: Ev) => (e.all_day ? parseDay(e.end) : new Date(e.end));
const fmtT = (d: Date) => d.toLocaleTimeString([], { hour: "numeric", minute: d.getMinutes() ? "2-digit" : undefined });

/** Does the event touch calendar day d? (all-day end dates are exclusive) */
function touches(e: Ev, d: Date) {
  const s = evStart(e), en = evEnd(e), d0 = startOfDay(d), d1 = addDays(d0, 1);
  if (e.all_day) return s < d1 && en > d0;
  return s < d1 && (en > d0 || +s === +en && s >= d0);
}

function range(view: View, cur: Date): [Date, Date] {
  if (view === "day") return [startOfDay(cur), addDays(startOfDay(cur), 1)];
  if (view === "week") { const s = addDays(startOfDay(cur), -cur.getDay()); return [s, addDays(s, 7)]; }
  const first = new Date(cur.getFullYear(), cur.getMonth(), 1);
  const s = addDays(first, -first.getDay());
  return [s, addDays(s, 42)];
}

export function CalendarXL({ defaultAccount }: { defaultAccount?: string }) {
  const [view, setView] = useState<View>("month");
  const [cur, setCur] = useState(() => startOfDay(new Date()));
  const [show, setShow] = useState<Record<Acct, boolean>>(() => {
    try { return { personal: true, work: true, ...JSON.parse(localStorage.getItem("jarvis.calShow") || "{}") }; }
    catch { return { personal: true, work: true }; }
  });
  const [events, setEvents] = useState<Ev[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [from, to] = useMemo(() => range(view, cur), [view, cur]);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const n = ++seq.current;
    setLoading(true);
    const r = await core.workspace("cal_events", { time_min: iso(from), time_max: iso(to) });
    if (n !== seq.current) return;
    setLoading(false);
    if (!r.ok) { setErr(r.error || "Couldn't load calendars"); return; }
    setErr(Object.entries(r.result.errors || {}).map(([a, e]) => `${a}: ${e}`).join(" · "));
    setEvents(r.result.events);
  }, [from, to]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { localStorage.setItem("jarvis.calShow", JSON.stringify(show)); }, [show]);

  const visible = events.filter((e) => show[e.account]);
  const today = startOfDay(new Date());

  const step = (dir: number) => setCur((c) =>
    view === "day" ? addDays(c, dir) : view === "week" ? addDays(c, 7 * dir) : new Date(c.getFullYear(), c.getMonth() + dir, 1));
  const openDay = (d: Date) => { setCur(startOfDay(d)); setView("day"); };

  const newAt = (d: Date, hour?: number, allDay = false) => {
    const acct: Acct = show.work && !show.personal ? "work" : show.personal && !show.work ? "personal"
      : (defaultAccount === "work" ? "work" : "personal");
    const h = hour ?? Math.min(22, new Date().getHours() + 1);
    setDraft({ account: acct, summary: "", date: ymd(d), endDate: ymd(d), startT: `${pad(h)}:00`, endT: `${pad(Math.min(23, h + 1))}:00`,
      all_day: allDay, location: "", description: "" });
  };
  const edit = (e: Ev) => {
    const s = evStart(e), en = evEnd(e);
    const endDay = e.all_day ? addDays(en, -1) : en;
    setDraft({ id: e.id, account: e.account, origAccount: e.account, summary: e.summary, date: ymd(s), endDate: ymd(endDay),
      startT: e.all_day ? "09:00" : hm(s), endT: e.all_day ? "10:00" : hm(en), all_day: e.all_day,
      location: e.location || "", description: e.description || "", attendees: e.attendees, recurring: e.recurring });
  };

  const title = view === "month" ? cur.toLocaleDateString([], { month: "long", year: "numeric" })
    : view === "week" ? `${from.toLocaleDateString([], { month: "short", day: "numeric" })} – ${addDays(to, -1).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`
    : cur.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" });

  return (
    <div className="cx">
      <div className="cx-bar">
        <div className="cx-nav">
          <button className="cx-btn" onClick={() => step(-1)} title="Previous">‹</button>
          <button className="cx-btn" onClick={() => setCur(today)}>Today</button>
          <button className="cx-btn" onClick={() => step(1)} title="Next">›</button>
          <div className="cx-title">{title}</div>
          {loading && <span className="cx-spin" />}
        </div>
        <div className="cx-right">
          {(["personal", "work"] as Acct[]).map((a) => (
            <button key={a} className={`cx-tog cxa-${a} ${show[a] ? "on" : ""}`} onClick={() => setShow({ ...show, [a]: !show[a] })}>
              <i />{ACCT_LABEL[a]}
            </button>
          ))}
          <div className="cx-views">
            {(["day", "week", "month"] as View[]).map((v) => (
              <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>{v[0].toUpperCase() + v.slice(1)}</button>
            ))}
          </div>
          <button className="cx-add" onClick={() => newAt(view === "month" && !sameDay(cur, today) ? cur : view === "day" ? cur : today)}>+ New event</button>
        </div>
      </div>
      {err && <div className="cx-err">{err}</div>}

      {view === "month" && <Month cur={cur} from={from} events={visible} today={today} onDay={openDay} onEdit={edit}
        onNew={(d) => newAt(d)} />}
      {view !== "month" && <TimeGrid days={view === "week" ? [...Array(7)].map((_, i) => addDays(from, i)) : [cur]}
        events={visible} today={today} onDay={openDay} onEdit={edit} onNew={newAt} wide={view === "day"} />}

      {draft && <Editor draft={draft} setDraft={setDraft} onDone={() => { setDraft(null); load(); }} />}
    </div>
  );
}

function Chip({ e, onEdit, compact }: { e: Ev; onEdit: (e: Ev) => void; compact?: boolean }) {
  return (
    <button className={`cx-chip cxa-${e.account} ${e.all_day ? "allday" : ""} ${e.my_status === "declined" ? "declined" : ""}`}
      title={e.summary} onClick={(x) => { x.stopPropagation(); onEdit(e); }}>
      {!e.all_day && !compact && <b>{fmtT(evStart(e))}</b>}{e.summary}
    </button>
  );
}

function Month({ cur, from, events, today, onDay, onEdit, onNew }: {
  cur: Date; from: Date; events: Ev[]; today: Date; onDay: (d: Date) => void; onEdit: (e: Ev) => void; onNew: (d: Date) => void;
}) {
  const days = [...Array(42)].map((_, i) => addDays(from, i));
  return (
    <div className="cx-month">
      {DOW.map((d) => <div key={d} className="cx-dow">{d}</div>)}
      {days.map((d) => {
        const evs = events.filter((e) => touches(e, d)).sort((a, b) => +b.all_day - +a.all_day || +evStart(a) - +evStart(b));
        const out = d.getMonth() !== cur.getMonth();
        return (
          <div key={ymd(d)} className={`cx-cell ${out ? "out" : ""} ${sameDay(d, today) ? "today" : ""}`} onDoubleClick={() => onNew(d)}>
            <button className="cx-num" onClick={() => onDay(d)} title="Open day view">{d.getDate()}</button>
            <button className="cx-cell-add" onClick={() => onNew(d)} title="Add event">+</button>
            <div className="cx-cell-evs">
              {evs.slice(0, 4).map((e) => <Chip key={e.account + e.id} e={e} onEdit={onEdit} />)}
              {evs.length > 4 && <button className="cx-more" onClick={() => onDay(d)}>+{evs.length - 4} more</button>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Lay overlapping timed events side by side. */
function columns(evs: Ev[]) {
  const sorted = [...evs].sort((a, b) => +evStart(a) - +evStart(b) || +evEnd(b) - +evEnd(a));
  const out: { e: Ev; col: number; cols: number }[] = [];
  let group: { e: Ev; col: number; cols: number }[] = [], groupEnd = 0;
  const flush = () => { const n = Math.max(1, ...group.map((g) => g.col + 1)); group.forEach((g) => (g.cols = n)); out.push(...group); group = []; };
  for (const e of sorted) {
    const s = +evStart(e);
    if (group.length && s >= groupEnd) flush();
    const used = new Set(group.filter((g) => +evEnd(g.e) > s).map((g) => g.col));
    let col = 0; while (used.has(col)) col++;
    group.push({ e, col, cols: 1 });
    groupEnd = Math.max(groupEnd, +evEnd(e));
  }
  flush();
  return out;
}

function TimeGrid({ days, events, today, onDay, onEdit, onNew, wide }: {
  days: Date[]; events: Ev[]; today: Date; onDay: (d: Date) => void; onEdit: (e: Ev) => void;
  onNew: (d: Date, hour?: number, allDay?: boolean) => void; wide?: boolean;
}) {
  const body = useRef<HTMLDivElement>(null);
  const [now, setNow] = useState(new Date());
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 60_000); return () => clearInterval(t); }, []);
  useEffect(() => {
    const first = events.filter((e) => !e.all_day && days.some((d) => touches(e, d))).map((e) => evStart(e).getHours());
    const h = Math.max(0, Math.min(first.length ? Math.min(...first) : 8, 8) - 1);
    if (body.current) body.current.scrollTop = Math.max(0, h * HOUR_PX - 12);
  }, [days.map(ymd).join()]); // eslint-disable-line react-hooks/exhaustive-deps
  const cols = `56px repeat(${days.length}, minmax(0, 1fr))`;
  return (
    <div className={`cx-tg ${wide ? "wide" : ""}`}>
      <div className="cx-tg-head" style={{ gridTemplateColumns: cols }}>
        <div />
        {days.map((d) => (
          <button key={ymd(d)} className={`cx-tg-day ${sameDay(d, today) ? "today" : ""}`} onClick={() => onDay(d)} title="Open day view">
            <span>{DOW[d.getDay()]}</span><b>{d.getDate()}</b>
          </button>
        ))}
      </div>
      <div className="cx-tg-all" style={{ gridTemplateColumns: cols }}>
        <div className="cx-tg-lbl">all-day</div>
        {days.map((d) => (
          <div key={ymd(d)} className="cx-tg-allcell" onDoubleClick={() => onNew(d, undefined, true)}>
            {events.filter((e) => e.all_day && touches(e, d)).map((e) => <Chip key={e.account + e.id} e={e} onEdit={onEdit} compact />)}
          </div>
        ))}
      </div>
      <div className="cx-tg-body" ref={body}>
        <div className="cx-tg-grid" style={{ gridTemplateColumns: cols, height: 24 * HOUR_PX }}>
          <div className="cx-tg-hours">
            {[...Array(24)].map((_, h) => <div key={h} style={{ top: h * HOUR_PX }}>{h === 0 ? "" : fmtT(new Date(2000, 0, 1, h))}</div>)}
          </div>
          {days.map((d) => {
            const d0 = +startOfDay(d), d1 = +addDays(d, 1);
            const evs = events.filter((e) => !e.all_day && touches(e, d));
            return (
              <div key={ymd(d)} className="cx-tg-col"
                onClick={(x) => {
                  if ((x.target as HTMLElement).closest(".cx-blk")) return;
                  const y = x.clientY - (x.currentTarget as HTMLElement).getBoundingClientRect().top;
                  onNew(d, Math.max(0, Math.min(23, Math.floor(y / HOUR_PX))));
                }}>
                {[...Array(24)].map((_, h) => <div key={h} className="cx-tg-line" style={{ top: h * HOUR_PX }} />)}
                {sameDay(d, now) && <div className="cx-now" style={{ top: ((+now - d0) / 3.6e6) * HOUR_PX }} />}
                {columns(evs).map(({ e, col, cols: n }) => {
                  const s = Math.max(+evStart(e), d0), en = Math.min(Math.max(+evEnd(e), s + 15 * 6e4), d1);
                  const top = ((s - d0) / 3.6e6) * HOUR_PX, h = Math.max(20, ((en - s) / 3.6e6) * HOUR_PX - 2);
                  return (
                    <button key={e.account + e.id} className={`cx-blk cxa-${e.account} ${e.my_status === "declined" ? "declined" : ""}`}
                      style={{ top, height: h, left: `calc(${(col / n) * 100}% + 2px)`, width: `calc(${100 / n}% - 4px)` }}
                      onClick={() => onEdit(e)} title={e.summary}>
                      <b>{e.summary}</b>
                      {h > 30 && <span>{fmtT(evStart(e))} – {fmtT(evEnd(e))}{wide && e.location ? ` · ${e.location}` : ""}</span>}
                      {wide && h > 60 && e.description && <em>{e.description.replace(/<[^>]+>/g, " ").slice(0, 160)}</em>}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Editor({ draft, setDraft, onDone }: { draft: Draft; setDraft: (d: Draft | null) => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [confirmDel, setConfirmDel] = useState(false);
  const d = draft, set = (p: Partial<Draft>) => setDraft({ ...d, ...p });
  const guests = (d.attendees || []).length > 0;

  const save = async () => {
    setErr("");
    let start: string, end: string;
    if (d.all_day) {
      start = d.date; end = ymd(addDays(parseDay(d.endDate < d.date ? d.date : d.endDate), 1));
    } else {
      const s = new Date(`${d.date}T${d.startT}`), e = new Date(`${d.endDate}T${d.endT}`);
      if (!(e > s)) { setErr("End must be after start."); return; }
      start = iso(s); end = iso(e);
    }
    setBusy(true);
    const base = { summary: d.summary.trim(), start, end, all_day: d.all_day, location: d.location, description: d.description };
    const r = d.id
      ? await core.workspace("cal_update", { account: d.origAccount, event_id: d.id, ...base, new_account: d.account !== d.origAccount ? d.account : "" })
      : await core.workspace("cal_create", { account: d.account, ...base });
    setBusy(false);
    if (!r.ok) { setErr(r.error || "Couldn't save"); return; }
    onDone();
  };
  const del = async () => {
    if (!confirmDel) { setConfirmDel(true); return; }
    setBusy(true);
    const r = await core.workspace("cal_delete", { account: d.origAccount, event_id: d.id });
    setBusy(false);
    if (!r.ok) { setErr(r.error || "Couldn't delete"); return; }
    onDone();
  };
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setDraft(null); } };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [setDraft]);

  return (
    <div className="cx-scrim" role="dialog" onMouseDown={(e) => e.target === e.currentTarget && setDraft(null)}>
      <div className={`cx-ed cxa-${d.account}`}>
        <div className="cx-ed-h">{d.id ? "Edit event" : "New event"}</div>
        <input className="cx-in cx-ed-title" autoFocus placeholder="Add title" value={d.summary}
          onChange={(e) => set({ summary: e.target.value })} onKeyDown={(e) => e.key === "Enter" && save()} />
        <div className="cx-ed-accts">
          {(["personal", "work"] as Acct[]).map((a) => (
            <button key={a} className={`cx-tog cxa-${a} ${d.account === a ? "on" : ""}`} disabled={!!d.id && guests && a !== d.origAccount}
              onClick={() => set({ account: a })}><i />{ACCT_LABEL[a]}</button>
          ))}
          <label className="cx-check"><input type="checkbox" checked={d.all_day} onChange={(e) => set({ all_day: e.target.checked })} /> All day</label>
        </div>
        <div className="cx-ed-row">
          <input className="cx-in" type="date" value={d.date} onChange={(e) => set({ date: e.target.value, endDate: e.target.value > d.endDate ? e.target.value : d.endDate })} />
          {!d.all_day && <input className="cx-in" type="time" value={d.startT} onChange={(e) => {
            const [h, m] = e.target.value.split(":").map(Number), [sh, sm] = d.startT.split(":").map(Number), [eh, em] = d.endT.split(":").map(Number);
            const dur = eh * 60 + em - (sh * 60 + sm), t = Math.min(23 * 60 + 59, h * 60 + m + Math.max(15, dur));
            set({ startT: e.target.value, endT: `${pad(Math.floor(t / 60))}:${pad(t % 60)}` });
          }} />}
          <span className="muted">to</span>
          {!d.all_day && <input className="cx-in" type="time" value={d.endT} onChange={(e) => set({ endT: e.target.value })} />}
          <input className="cx-in" type="date" value={d.endDate} min={d.date} onChange={(e) => set({ endDate: e.target.value })} />
        </div>
        <input className="cx-in" placeholder="Location" value={d.location} onChange={(e) => set({ location: e.target.value })} />
        <textarea className="cx-in" rows={3} placeholder="Description" value={d.description} onChange={(e) => set({ description: e.target.value })} />
        {guests && <div className="muted small">Guests ({d.attendees!.length}): {d.attendees!.slice(0, 6).join(", ")}{d.attendees!.length > 6 ? "…" : ""}. Changes notify them.</div>}
        {d.recurring && <div className="muted small">Recurring event: changes apply to this occurrence only.</div>}
        {err && <div className="cx-err">{err}</div>}
        <div className="cx-ed-actions">
          {d.id && <button className={`cx-btn danger ${confirmDel ? "armed" : ""}`} disabled={busy} onClick={del}>
            {confirmDel ? (guests ? "Delete & notify guests?" : "Confirm delete") : "Delete"}</button>}
          <span style={{ flex: 1 }} />
          <button className="cx-btn" onClick={() => setDraft(null)}>Cancel</button>
          <button className="cx-btn primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}
