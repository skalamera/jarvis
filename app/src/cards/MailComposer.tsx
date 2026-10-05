import { useEffect, useRef, useState } from "react";
import { core } from "../ws/core";
import { useStore } from "../state/store";

/* Gmail-style composer used inside email cards (sidebar or expanded): recipient chips (To/Cc/Bcc), subject,
   rich-text editor with a formatting toolbar, attachments (picker + drag-drop), Send / Save draft / Discard.
   "AI Reply" asks JARVIS for a first draft that he can edit before sending. Nothing sends without his click. */

export type ComposeInit = {
  account: string; mode: "new" | "reply" | "reply_all" | "forward"; message_id?: string;
  to?: string[]; cc?: string[]; bcc?: string[]; subject?: string; quote?: string; from?: string;
  forward_attachments?: { id: string; filename: string; size: number; mimeType: string }[]; ai?: boolean; draft_id?: string;
};
type Att = { name: string; type: string; size: number; data: string };

const kb = (n: number) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
const toast = (text: string, error = false) => useStore.getState().toast({ text, error });

function Chips({ label, list, set, account, autoFocus }: { label: string; list: string[]; set: (l: string[]) => void; account: string; autoFocus?: boolean }) {
  const [v, setV] = useState("");
  const [sug, setSug] = useState<{ name: string; email: string }[]>([]);
  useEffect(() => {
    if (v.trim().length < 2) { setSug([]); return; }
    const t = setTimeout(() => core.workspace("mail_contacts", { account, q: v.trim() }).then((r) => r.ok && setSug(r.result || [])), 300);
    return () => clearTimeout(t);
  }, [v, account]);
  const add = (s: string) => {
    const parts = s.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean);
    if (parts.length) set([...list, ...parts.filter((p) => !list.includes(p))]);
    setV(""); setSug([]);
  };
  return (
    <div className="mc-row">
      <span className="mc-lab">{label}</span>
      <div className="mc-chips">
        {list.map((a) => (
          <span key={a} className={`mc-chip ${/@/.test(a) ? "" : "bad"}`} title={a}>
            {a.replace(/\s*<.*>/, "") || a}<button type="button" onClick={() => set(list.filter((x) => x !== a))}>×</button>
          </span>
        ))}
        <input value={v} autoFocus={autoFocus} onChange={(e) => setV(e.target.value)}
          onKeyDown={(e) => {
            if (["Enter", ",", ";", "Tab"].includes(e.key) && v.trim()) { e.preventDefault(); add(v); }
            if (e.key === "Backspace" && !v && list.length) set(list.slice(0, -1));
          }}
          onBlur={() => setTimeout(() => v.trim() && add(v), 150)} />
        {sug.length > 0 && (
          <div className="mc-sug">
            {sug.map((s) => <button type="button" key={s.email} onMouseDown={(e) => { e.preventDefault(); add(s.name ? `${s.name} <${s.email}>` : s.email); }}>
              <b>{s.name || s.email}</b> <span>{s.email}</span></button>)}
          </div>
        )}
      </div>
    </div>
  );
}

const TOOLS: [string, string, string?][] = [
  ["bold", "B"], ["italic", "I"], ["underline", "U"], ["strikeThrough", "S"], ["|", ""],
  ["insertUnorderedList", "• List"], ["insertOrderedList", "1. List"], ["outdent", "⇤"], ["indent", "⇥"], ["formatBlock", "❝", "blockquote"], ["|", ""],
  ["justifyLeft", "⯇"], ["justifyCenter", "≡"], ["justifyRight", "⯈"], ["|", ""], ["undo", "↶"], ["redo", "↷"], ["removeFormat", "⌫ Tx"],
];

export function MailComposer({ init, onClose }: { init: ComposeInit; onClose: () => void }) {
  const [to, setTo] = useState<string[]>(init.to || []);
  const [cc, setCc] = useState<string[]>(init.cc || []);
  const [bcc, setBcc] = useState<string[]>(init.bcc || []);
  const [showCc, setShowCc] = useState(!!init.cc?.length);
  const [showBcc, setShowBcc] = useState(!!init.bcc?.length);
  const [subject, setSubject] = useState(init.subject || "");
  const [atts, setAtts] = useState<Att[]>([]);
  const [fwd, setFwd] = useState(init.forward_attachments || []);
  const [busy, setBusy] = useState<"" | "send" | "save" | "ai">(init.ai ? "ai" : "");
  const [draftId, setDraftId] = useState(init.draft_id || "");
  const [steer, setSteer] = useState("");
  const [showQuote, setShowQuote] = useState(init.mode === "forward");
  const [drag, setDrag] = useState(false);
  const ed = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const aiDraft = async (s = "") => {
    if (!init.message_id) return;
    setBusy("ai");
    const r = await core.workspace("mail_ai_reply", { account: init.account, message_id: init.message_id, mode: init.mode, steer: s });
    setBusy("");
    if (r.ok && ed.current) { ed.current.innerHTML = r.result.html; ed.current.focus(); }
    else toast(r.error || "JARVIS couldn't draft that reply.", true);
  };
  useEffect(() => { if (init.ai) aiDraft(); else setTimeout(() => ed.current?.focus(), 50); }, []);

  const exec = (cmd: string, arg?: string) => { ed.current?.focus(); document.execCommand(cmd, false, arg); };
  const addFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    const read = (f: File) => new Promise<Att>((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res({ name: f.name, type: f.type || "application/octet-stream", size: f.size, data: String(fr.result).split(",")[1] || "" });
      fr.onerror = () => rej(fr.error);
      fr.readAsDataURL(f);
    });
    const got = await Promise.all(list.map(read));
    const total = [...atts, ...got].reduce((n, a) => n + a.size, 0) + fwd.reduce((n, a) => n + (a.size || 0), 0);
    if (total > 24e6) { toast("Attachments are over Gmail's 25 MB limit.", true); return; }
    setAtts([...atts, ...got]);
  };
  const html = () => (ed.current?.innerHTML || "") + (init.quote || "");
  const payload = () => ({ account: init.account, to, cc, bcc, subject, html: html(), attachments: atts, reply_to_message_id: init.message_id || "",
    mode: init.mode, forward_attachments: fwd, draft_id: draftId });
  const send = async () => {
    if (!to.length && !cc.length && !bcc.length) { toast("Add at least one recipient.", true); return; }
    const bad = [...to, ...cc, ...bcc].find((a) => !/@/.test(a));
    if (bad) { toast(`“${bad}” isn't an email address.`, true); return; }
    setBusy("send");
    const r = await core.workspace("mail_send", payload());
    setBusy("");
    if (r.ok) { toast("Sent."); onClose(); } else toast(r.error || "Send failed.", true);
  };
  const save = async () => {
    setBusy("save");
    const r = await core.workspace("mail_save_draft", payload());
    setBusy("");
    if (r.ok) { setDraftId(r.result.draft_id); toast("Draft saved to Gmail."); } else toast(r.error || "Couldn't save the draft.", true);
  };
  const discard = async () => {
    if (draftId) await core.workspace("mail_discard_draft", { account: init.account, draft_id: draftId });
    onClose();
  };
  const title = { new: "New message", reply: "Reply", reply_all: "Reply all", forward: "Forward" }[init.mode];
  return (
    <div className={`mc ${drag ? "drag" : ""}`} onClick={(e) => e.stopPropagation()}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); }}>
      <div className="mc-head"><span>{title}{init.from ? ` · from ${init.from}` : ""}</span>{draftId && <em>Draft saved</em>}<button onClick={discard} title="Discard">×</button></div>
      <Chips label="To" list={to} set={setTo} account={init.account} autoFocus={init.mode === "new" || init.mode === "forward"} />
      <div className="mc-ccb">
        {!showCc && <button onClick={() => setShowCc(true)}>Cc</button>}
        {!showBcc && <button onClick={() => setShowBcc(true)}>Bcc</button>}
      </div>
      {showCc && <Chips label="Cc" list={cc} set={setCc} account={init.account} />}
      {showBcc && <Chips label="Bcc" list={bcc} set={setBcc} account={init.account} />}
      <div className="mc-row"><span className="mc-lab">Subject</span><input className="mc-subj" value={subject} onChange={(e) => setSubject(e.target.value)} /></div>
      <div className="mc-tools" onMouseDown={(e) => e.preventDefault()}>
        <select title="Text size" defaultValue="3" onChange={(e) => exec("fontSize", e.target.value)}>
          <option value="1">Small</option><option value="3">Normal</option><option value="5">Large</option><option value="7">Huge</option>
        </select>
        <label className="mc-color" title="Text color"><span>A</span><input type="color" onChange={(e) => exec("foreColor", e.target.value)} /></label>
        <label className="mc-color hl" title="Highlight"><span>▌</span><input type="color" defaultValue="#fff59d" onChange={(e) => exec("hiliteColor", e.target.value)} /></label>
        {TOOLS.map(([c, l, a], i) => c === "|" ? <i key={i} /> :
          <button key={c} title={c} className={`mc-t-${c}`} onClick={() => exec(c, a)}>{l}</button>)}
        <button title="Link" onClick={() => { const u = prompt("Link URL"); if (u) exec("createLink", /^https?:|^mailto:/.test(u) ? u : `https://${u}`); }}>🔗</button>
        <button title="Attach files" onClick={() => fileRef.current?.click()}>📎</button>
        <input ref={fileRef} type="file" multiple hidden onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
      </div>
      <div className="mc-ed-wrap">
        <div ref={ed} className="mc-ed" contentEditable suppressContentEditableWarning data-ph={busy === "ai" ? "" : "Write your message…"} />
        {busy === "ai" && <div className="mc-ai-busy"><span className="cx-spin" /> JARVIS is drafting your reply…</div>}
      </div>
      {init.quote && (
        <div className="mc-quote">
          <button onClick={() => setShowQuote(!showQuote)} title="Show trimmed content">···</button>
          {showQuote && <iframe sandbox="" srcDoc={`<base target="_blank"><style>body{font:13px/1.45 -apple-system,sans-serif;color:#222;margin:8px}</style>${init.quote}`} title="quoted" />}
        </div>
      )}
      {(atts.length > 0 || fwd.length > 0) && (
        <div className="mc-atts">
          {fwd.map((a) => <span key={a.id} className="mc-att">📎 {a.filename} <em>{kb(a.size)}</em><button onClick={() => setFwd(fwd.filter((x) => x !== a))}>×</button></span>)}
          {atts.map((a, i) => <span key={i} className="mc-att">📎 {a.name} <em>{kb(a.size)}</em><button onClick={() => setAtts(atts.filter((x) => x !== a))}>×</button></span>)}
        </div>
      )}
      {init.message_id && init.mode !== "forward" && (
        <form className="mc-steer" onSubmit={(e) => { e.preventDefault(); aiDraft(steer); }}>
          <span>✨</span><input value={steer} onChange={(e) => setSteer(e.target.value)} placeholder="Ask JARVIS to (re)write it… e.g. “decline politely”, “shorter”" />
          <button type="submit" disabled={busy === "ai"}>{busy === "ai" ? <span className="cx-spin" /> : "AI write"}</button>
        </form>
      )}
      <div className="mc-foot">
        <button className="mc-send" onClick={send} disabled={!!busy}>{busy === "send" ? <span className="cx-spin" /> : "Send"}</button>
        <button className="mc-btn" onClick={save} disabled={!!busy}>{busy === "save" ? <span className="cx-spin" /> : "Save draft"}</button>
        <button className="mc-btn ghost" onClick={discard}>Discard</button>
        <span className="mc-hint">Drop files anywhere here to attach</span>
      </div>
    </div>
  );
}
