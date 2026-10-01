/** Interactive displays for uploaded files (image / sheet / document / code) and codebases (architecture map,
 *  code viewer, diffs with undo). Every edit goes through Core's allow-listed `workspace` ops, which save a new
 *  version (files) or snapshot the old content (code), so everything here is undoable. */
import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../state/store";
import { core } from "../ws/core";
import type { Card } from "../types";
import { MarkdownCard } from "./Cards";

// ------------------------------------------------------------------ shared bits
const fmtBytes = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
const fmtNum = (n: number) => (Math.abs(n) >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : Math.abs(n) >= 1e4 ? `${(n / 1e3).toFixed(1)}K` : `${+n.toFixed(2)}`);
const ago = (ts: number) => {
  const s = Date.now() / 1000 - ts;
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;
};
const colName = (i: number) => {
  let s = "";
  for (let n = i + 1; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
};
const LANG_COLOR: Record<string, string> = {
  Python: "#4fa3ff", TypeScript: "#39d0ff", JavaScript: "#ffd84d", CSS: "#b388ff", HTML: "#ff8a5c", Rust: "#ff9e64",
  Go: "#5ce1e6", Java: "#ffb020", Swift: "#ff6b4a", Shell: "#3dffb0", SQL: "#e6c07b", Ruby: "#ff5c7a", JSON: "#8fa3b8",
  Markdown: "#9fb4c8", YAML: "#9fb4c8", Other: "#6f8597",
};
const langColor = (l: string) => LANG_COLOR[l] ?? "#7af7ff";

function GlassBtn({ children, onClick, tone = "cyan", disabled, title, on }: {
  children: React.ReactNode; onClick?: () => void; tone?: "cyan" | "amber" | "red" | "ghost"; disabled?: boolean; title?: string; on?: boolean;
}) {
  return (
    <button className={`gbtn gbtn-${tone} ${on ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); onClick?.(); }} disabled={disabled} title={title}>
      {children}
    </button>
  );
}

/** Run a workspace op; refresh this card from the result (ops that change a file return its fresh card data). */
function useOp(card: Card) {
  const [busy, setBusy] = useState("");
  const run = async (op: string, args: Record<string, unknown>, label = op, apply = true) => {
    setBusy(label);
    const r = await core.workspace(op, args);
    setBusy("");
    const s = useStore.getState();
    if (!r.ok) { s.toast({ text: r.error || "That didn't work.", error: true }); return null; }
    if (apply && r.result && (r.result.artifact || r.result.modules || r.result.changes)) {
      const { text, ...data } = r.result;
      const title = data.artifact ? `${data.artifact.filename} · v${data.artifact.version}` : card.title;
      s.updateCard(card.id, { data: { ...card.data, ...data }, title });
    }
    if (r.result?.text) s.toast({ text: r.result.text });
    return r.result;
  };
  return { busy, run };
}

// ------------------------------------------------------------------ syntax highlighting (small, dependency-free)
const KW = new Set(("if else elif for while do return function def class import from export default const let var new " +
  "try except catch finally raise throw async await yield with as in of not and or is None null undefined true false " +
  "True False self this super interface type enum extends implements public private protected static fn pub struct impl " +
  "mut match use mod package func go chan defer switch case break continue lambda pass global nonlocal readonly void " +
  "int float str bool string number boolean any where select insert update delete create table").split(" "));
const TOKEN = /(\/\/.*$|#(?![\w-]*[{:]).*$|\/\*.*?\*\/|"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(\b\d[\d_]*(?:\.\d+)?(?:e[+-]?\d+)?\b)|(@[\w.]+)|([A-Za-z_$][\w$]*)(?=\s*\()|([A-Za-z_$][\w$]*)/gm;

function Highlight({ line, lang }: { line: string; lang: string }) {
  const out: React.ReactNode[] = [];
  let last = 0, k = 0;
  const hashComment = ["py", "sh", "bash", "zsh", "rb", "yaml", "yml", "toml", "r"].includes(lang);
  for (const m of line.matchAll(TOKEN)) {
    const i = m.index ?? 0;
    if (i > last) out.push(line.slice(last, i));
    const [tok, str, num, deco, call, word] = m;
    let cls = "";
    if (str) cls = str.startsWith("//") || str.startsWith("/*") ? "tk-com" : str.startsWith("#") ? (hashComment ? "tk-com" : "") : "tk-str";
    else if (num) cls = "tk-num";
    else if (deco) cls = "tk-deco";
    else if (call) cls = KW.has(call) ? "tk-kw" : "tk-fn";
    else if (word) cls = KW.has(word) ? "tk-kw" : /^[A-Z]/.test(word) ? "tk-type" : "";
    out.push(cls ? <span key={k++} className={cls}>{tok}</span> : tok);
    last = i + tok.length;
  }
  if (last < line.length) out.push(line.slice(last));
  return <>{out}</>;
}

export function CodeView({ text, lang, maxLines = 4000, start = 1 }: { text: string; lang: string; maxLines?: number; start?: number }) {
  const lines = useMemo(() => text.split("\n").slice(0, maxLines), [text, maxLines]);
  const w = String(start + lines.length).length;
  return (
    <div className="ws-code">
      {lines.map((ln, i) => (
        <div key={i} className="ws-ln">
          <span className="ws-no" style={{ width: `${w + 1}ch` }}>{start + i}</span>
          <span className="ws-tx"><Highlight line={ln} lang={lang} /></span>
        </div>
      ))}
      {text.split("\n").length > maxLines && <div className="muted small ws-more">… {text.split("\n").length - maxLines} more lines</div>}
    </div>
  );
}

function DiffView({ diff }: { diff: string }) {
  return (
    <div className="ws-code ws-diff">
      {diff.split("\n").map((ln, i) => {
        const cls = ln.startsWith("+++") || ln.startsWith("---") ? "df-file" : ln.startsWith("@@") ? "df-hunk" : ln.startsWith("+") ? "df-add" : ln.startsWith("-") ? "df-del" : "";
        return <div key={i} className={`ws-ln ${cls}`}><span className="ws-tx">{ln || " "}</span></div>;
      })}
    </div>
  );
}

// ------------------------------------------------------------------ uploaded files
const KIND_ICON: Record<string, string> = { image: "◩", csv: "▦", xlsx: "▦", pdf: "▤", docx: "▤", code: "⟨⟩", text: "≡", binary: "◇" };

export function ArtifactCard({ card, expanded = false }: { card: Card; expanded?: boolean }) {
  const a = card.data?.artifact;
  const v = card.data?.view ?? {};
  const { busy, run } = useOp(card);
  const [showVersions, setShowVersions] = useState(false);
  if (!a) return <div className="muted">File unavailable.</div>;
  const id = a.id;
  return (
    <div className={`ws ${expanded ? "ws-xl" : ""}`}>
      <div className="ws-bar">
        <span className="ws-icon">{KIND_ICON[a.kind] ?? "◇"}</span>
        <span className="ws-meta">{a.kind.toUpperCase()} · {fmtBytes(a.size)}{v.pages ? ` · ${v.pages} pages` : ""}{v.lines ? ` · ${v.lines} lines` : ""}{v.width ? ` · ${v.width}×${v.height}` : ""}</span>
        <button className={`ws-ver ${showVersions ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); setShowVersions(!showVersions); }} title="Version history">v{a.version}</button>
        <span className="ws-spacer" />
        {!expanded && <GlassBtn tone="ghost" title="Open full size" onClick={() => useStore.getState().set({ expandedCardId: card.id })}>⤢</GlassBtn>}
        <GlassBtn tone="ghost" disabled={a.version < 2 || !!busy} title="Undo the last change" onClick={() => run("artifact_revert", { artifact_id: id }, "undo")}>↶ Undo</GlassBtn>
        <GlassBtn tone="ghost" disabled={!!busy} title="Save a copy to Downloads" onClick={() => run("artifact_export", { artifact_id: id }, "save", false)}>⤓ Save</GlassBtn>
      </div>
      {showVersions && (
        <div className="ws-versions">
          {[...a.versions].reverse().map((x: any) => (
            <div key={x.n} className={`ws-vrow ${x.n === a.version ? "cur" : ""}`}>
              <span className="ws-vn">v{x.n}</span><span className="ws-vnote">{x.note}</span>
              <span className={`ws-vsrc src-${x.source}`}>{x.source === "model" ? "JARVIS" : x.source === "artifact_click" ? "YOU" : x.source.toUpperCase()}</span>
              <span className="muted small">{ago(x.ts)}</span>
            </div>
          ))}
        </div>
      )}
      {busy && <div className="ws-busy"><span className="ws-spin" />{busy}…</div>}
      {v.type === "image" && <ImageView card={card} run={run} busy={!!busy} expanded={expanded} />}
      {v.type === "sheet" && <SheetView card={card} run={run} expanded={expanded} />}
      {(v.type === "text") && <TextView card={card} run={run} expanded={expanded} />}
      {v.type === "document" && (
        <div className={`ws-doc ${expanded ? "xl" : ""}`}>
          {a.ext === ".md" ? <MarkdownCard text={v.text} /> : <pre className="doc-text">{v.text}</pre>}
          {v.truncated && <div className="muted small">… truncated for display; JARVIS reads the whole file.</div>}
        </div>
      )}
      {v.type === "error" && <div className="ws-err">Couldn't read this file: {v.error}</div>}
      {v.type === "binary" && <div className="muted">Binary file ({fmtBytes(a.size)}). JARVIS can describe it but there's nothing to preview.</div>}
    </div>
  );
}

type Run = (op: string, args: Record<string, unknown>, label?: string, apply?: boolean) => Promise<any>;

function ImageView({ card, run, busy, expanded }: { card: Card; run: Run; busy: boolean; expanded: boolean }) {
  const a = card.data.artifact;
  const [zoom, setZoom] = useState(false);
  const op = (ops: any[], label: string) => run("artifact_image_op", { artifact_id: a.id, ops }, label);
  return (
    <div className="ws-img">
      <div className={`ws-img-stage ${expanded ? "xl" : ""}`} onClick={(e) => { e.stopPropagation(); setZoom(!zoom); }}>
        <img src={core.artifactUrl(a.id, a.version)} alt={a.filename} className={zoom ? "zoom" : ""} draggable={false} />
        <div className="ws-img-scan" />
      </div>
      <div className="ws-tools">
        <GlassBtn tone="ghost" disabled={busy} title="Rotate left" onClick={() => op([{ op: "rotate", degrees: -90 }], "rotating")}>⟲</GlassBtn>
        <GlassBtn tone="ghost" disabled={busy} title="Rotate right" onClick={() => op([{ op: "rotate", degrees: 90 }], "rotating")}>⟳</GlassBtn>
        <GlassBtn tone="ghost" disabled={busy} title="Flip horizontally" onClick={() => op([{ op: "flip", direction: "horizontal" }], "flipping")}>⇋</GlassBtn>
        <GlassBtn tone="ghost" disabled={busy} title="Brighter" onClick={() => op([{ op: "brightness", factor: 1.15 }], "brightening")}>☀+</GlassBtn>
        <GlassBtn tone="ghost" disabled={busy} title="More contrast" onClick={() => op([{ op: "contrast", factor: 1.15 }], "contrast")}>◐+</GlassBtn>
        <GlassBtn tone="ghost" disabled={busy} title="Sharpen" onClick={() => op([{ op: "sharpen" }], "sharpening")}>◆</GlassBtn>
        <GlassBtn tone="ghost" disabled={busy} title="Black and white" onClick={() => op([{ op: "grayscale" }], "grayscale")}>B/W</GlassBtn>
        <span className="ws-spacer" />
        <span className="muted small">Ask JARVIS for anything else: crop, resize, annotate…</span>
      </div>
    </div>
  );
}

function SheetView({ card, run, expanded }: { card: Card; run: Run; expanded: boolean }) {
  const a = card.data.artifact;
  const sheets: any[] = card.data.view.sheets ?? [];
  const [si, setSi] = useState(0);
  const [sel, setSel] = useState<[number, number] | null>(null);
  const [edit, setEdit] = useState<{ r: number; c: number; v: string } | null>(null);
  const [limit, setLimit] = useState(expanded ? 400 : 60);
  const sh = sheets[Math.min(si, sheets.length - 1)];
  if (!sh) return <div className="muted">Empty spreadsheet.</div>;
  const grid: string[][] = sh.grid;
  const width = Math.max(1, ...grid.slice(0, limit).map((r) => r.length));
  const numericCols = new Set((sh.stats ?? []).map((s: any) => s.col));
  const commit = async () => {
    if (!edit) return;
    const old = grid[edit.r]?.[edit.c] ?? "";
    const e = edit;
    setEdit(null);
    if (e.v === old) return;
    await run("artifact_sheet_set", { artifact_id: a.id, sheet: a.kind === "xlsx" ? sh.name : "", edits: [{ row: e.r, col: e.c, value: e.v }] }, "saving");
  };
  const selVal = sel ? (sh.formulas?.[`${sel[0]},${sel[1]}`] ?? grid[sel[0]]?.[sel[1]] ?? "") : "";
  return (
    <div className="ws-sheet">
      {sheets.length > 1 && (
        <div className="ws-tabs">{sheets.map((s, i) => <button key={s.name} className={`ws-tab ${i === si ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); setSi(i); setSel(null); }}>{s.name}</button>)}</div>
      )}
      {(sh.stats ?? []).length > 0 && (
        <div className="ws-stats">
          {sh.stats.slice(0, expanded ? 12 : 4).map((s: any) => (
            <div key={s.col} className="ws-stat" title={`${s.count} values · min ${s.min} · max ${s.max}`}>
              <div className="ws-stat-name">{s.name}</div>
              <div className="ws-stat-val">Σ {fmtNum(s.sum)}</div>
              <div className="ws-stat-sub">avg {fmtNum(s.avg)} · {fmtNum(s.min)}–{fmtNum(s.max)}</div>
            </div>
          ))}
        </div>
      )}
      <div className="ws-fx"><span className="ws-fx-ref">{sel ? `${colName(sel[1])}${sel[0] + 1}` : "—"}</span><span className="ws-fx-val">{selVal}</span></div>
      <div className={`ws-grid-wrap ${expanded ? "xl" : ""}`}>
        <table className="ws-grid">
          <thead>
            <tr><th className="ws-rh" />{Array.from({ length: width }, (_, c) => <th key={c}>{colName(c)}</th>)}</tr>
          </thead>
          <tbody>
            {grid.slice(0, limit).map((row, r) => (
              <tr key={r} className={r === 0 ? "ws-head-row" : ""}>
                <td className="ws-rh">{r + 1}</td>
                {Array.from({ length: width }, (_, c) => {
                  const isSel = sel && sel[0] === r && sel[1] === c;
                  const val = row[c] ?? "";
                  return (
                    <td key={c} className={`${isSel ? "sel" : ""} ${numericCols.has(c) && r > 0 ? "num" : ""} ${sh.formulas?.[`${r},${c}`] ? "fx" : ""}`}
                      onClick={(e) => { e.stopPropagation(); setSel([r, c]); }}
                      onDoubleClick={(e) => { e.stopPropagation(); setEdit({ r, c, v: sh.formulas?.[`${r},${c}`] ?? val }); }}>
                      {edit && edit.r === r && edit.c === c ? (
                        <input autoFocus value={edit.v} onChange={(e) => setEdit({ ...edit, v: e.target.value })} onBlur={commit}
                          onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEdit(null); e.stopPropagation(); }} />
                      ) : val}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="ws-foot">
        <span className="muted small">{sh.total_rows.toLocaleString()} rows × {sh.total_cols} cols · double-click a cell to edit</span>
        {grid.length > limit && <GlassBtn tone="ghost" onClick={() => setLimit(limit + 300)}>Show more rows</GlassBtn>}
      </div>
    </div>
  );
}

function TextView({ card, run, expanded }: { card: Card; run: Run; expanded: boolean }) {
  const a = card.data.artifact;
  const v = card.data.view;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(v.text);
  useEffect(() => { if (!editing) setDraft(v.text); }, [v.text, editing]);
  const lang = (v.language || "text").toLowerCase();
  if (a.ext === ".md" && !editing) {
    return (
      <div>
        <div className={`ws-doc ${expanded ? "xl" : ""}`}><MarkdownCard text={v.text} /></div>
        <div className="ws-tools"><GlassBtn tone="ghost" onClick={() => setEditing(true)}>✎ Edit</GlassBtn></div>
      </div>
    );
  }
  return (
    <div>
      {editing ? (
        <textarea className={`ws-edit ${expanded ? "xl" : ""}`} value={draft} spellCheck={false} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation();
            if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); run("artifact_save_text", { artifact_id: a.id, content: draft }, "saving").then((r) => r && setEditing(false)); }
          }} onClick={(e) => e.stopPropagation()} />
      ) : (
        <div className={`ws-code-wrap ${expanded ? "xl" : ""}`}><CodeView text={v.text} lang={lang} maxLines={expanded ? 20000 : 600} /></div>
      )}
      <div className="ws-tools">
        {editing ? (
          <>
            <GlassBtn onClick={() => run("artifact_save_text", { artifact_id: a.id, content: draft }, "saving").then((r) => r && setEditing(false))} disabled={draft === v.text}>Save ⌘S</GlassBtn>
            <GlassBtn tone="ghost" onClick={() => { setDraft(v.text); setEditing(false); }}>Cancel</GlassBtn>
          </>
        ) : (
          <GlassBtn tone="ghost" onClick={() => setEditing(true)}>✎ Edit</GlassBtn>
        )}
        <span className="ws-spacer" />
        {v.truncated && <span className="muted small">Display truncated; JARVIS reads the whole file.</span>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ codebase map
type Mod = { id: string; name: string; file_count: number; loc: number; lang: string; files: any[] };

/** Layout: each connected cluster of modules gets its own columns (importers left, dependencies right); clusters
 *  sit side by side and modules with no internal imports go in a compact strip underneath. */
function layout(mods: Mod[], edges: { source: string; target: string; weight: number }[], W: number) {
  const ids = new Set(mods.map((m) => m.id));
  const es = edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  const nbr: Record<string, Set<string>> = {};
  mods.forEach((m) => (nbr[m.id] = new Set()));
  es.forEach((e) => { nbr[e.source].add(e.target); nbr[e.target].add(e.source); });
  const seen = new Set<string>();
  const comps: Mod[][] = [];
  const loners: Mod[] = [];
  for (const m of [...mods].sort((a, b) => b.loc - a.loc)) {
    if (seen.has(m.id)) continue;
    if (!nbr[m.id].size) { seen.add(m.id); loners.push(m); continue; }
    const comp: Mod[] = [], stack = [m.id];
    while (stack.length) {
      const id = stack.pop()!;
      if (seen.has(id)) continue;
      seen.add(id);
      comp.push(mods.find((x) => x.id === id)!);
      nbr[id].forEach((n) => !seen.has(n) && stack.push(n));
    }
    comps.push(comp);
  }
  const maxLoc = Math.max(1, ...mods.map((m) => m.loc));
  const radius = (m: Mod) => 9 + 17 * Math.sqrt(m.loc / maxLoc);
  const layered = comps.map((comp) => {
    const layer: Record<string, number> = {};
    comp.forEach((m) => (layer[m.id] = 0));
    for (let pass = 0; pass < comp.length; pass++) {  // longest path, capped so cycles stop growing
      let changed = false;
      for (const e of es) if (e.source in layer && layer[e.target] < layer[e.source] + 1 && layer[e.source] + 1 < 5) { layer[e.target] = layer[e.source] + 1; changed = true; }
      if (!changed) break;
    }
    const cols: Mod[][] = [];
    comp.forEach((m) => (cols[layer[m.id]] ||= []).push(m));
    return cols.filter(Boolean);
  });
  const totalCols = layered.reduce((a, c) => a + c.length, 0) + Math.max(0, layered.length - 1) * 0.6;
  const colW = Math.min(230, (W - 120) / Math.max(1, totalCols));
  const rowH = 78;
  const tallest = Math.max(1, ...layered.flatMap((c) => c.map((col) => col.length)));
  const graphH = layered.length ? tallest * rowH + 30 : 0;
  const pos: Record<string, { x: number; y: number; r: number }> = {};
  const usedW = totalCols * colW;
  let x = (W - usedW) / 2 + colW / 2;
  layered.forEach((cols) => {
    cols.forEach((col) => {
      col.sort((a, b) => b.loc - a.loc).forEach((m, i) => {
        pos[m.id] = { x, y: 20 + graphH / 2 + (i - (col.length - 1) / 2) * rowH, r: radius(m) };
      });
      x += colW;
    });
    x += colW * 0.6;
  });
  const perRow = Math.max(1, Math.floor((W - 60) / 150));
  const loneTop = graphH + (layered.length ? 50 : 30);
  loners.forEach((m, i) => {
    const row = Math.floor(i / perRow), inRow = Math.min(perRow, loners.length - row * perRow);
    pos[m.id] = { x: W / 2 + ((i % perRow) - (inRow - 1) / 2) * 150, y: loneTop + row * 70, r: Math.min(14, radius(m)) };
  });
  const H = loners.length ? loneTop + Math.ceil(loners.length / perRow) * 70 : graphH + 40;
  return { pos, es, H, loneTop: loners.length && layered.length ? loneTop - 32 : 0 };
}

function ArchGraph({ mods, edges, notes, sel, onSel }: { mods: Mod[]; edges: any[]; notes: any; sel: string | null; onSel: (id: string | null) => void }) {
  const W = 1000;
  const { pos, es, H, loneTop } = useMemo(() => layout(mods, edges, W), [mods, edges]);
  const [hover, setHover] = useState<string | null>(null);
  const focus = hover || sel;
  const linked = new Set<string>(focus ? es.filter((e) => e.source === focus || e.target === focus).flatMap((e) => [e.source, e.target]) : []);
  const maxW = Math.max(1, ...es.map((e) => e.weight));
  const short = (n: string) => (n.length > 22 ? "…" + n.slice(-21) : n);
  return (
    <div className="cb-graph">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
        <defs>
          <radialGradient id="cbNode" cx="35%" cy="30%"><stop offset="0%" stopColor="#fff" stopOpacity="0.55" /><stop offset="100%" stopColor="#fff" stopOpacity="0" /></radialGradient>
          <marker id="cbArrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="rgba(57,208,255,0.6)" /></marker>
        </defs>
        {loneTop > 0 && (
          <g className="cb-sep"><line x1={60} x2={W - 60} y1={loneTop} y2={loneTop} /><text x={W / 2} y={loneTop - 6} textAnchor="middle">STANDALONE</text></g>
        )}
        {es.map((e, i) => {
          const a = pos[e.source], b = pos[e.target];
          if (!a || !b) return null;
          const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
          const x1 = a.x + (dx / len) * a.r, y1 = a.y + (dy / len) * a.r, x2 = b.x - (dx / len) * (b.r + 4), y2 = b.y - (dy / len) * (b.r + 4);
          const mx = (x1 + x2) / 2, my = (y1 + y2) / 2 - Math.min(60, Math.abs(dx) * 0.08 + 12);
          const hot = focus && (e.source === focus || e.target === focus);
          return <path key={i} d={`M${x1},${y1} Q${mx},${my} ${x2},${y2}`} className={`cb-edge ${hot ? "hot" : focus ? "dim" : ""}`}
            strokeWidth={0.8 + 2.6 * (e.weight / maxW)} markerEnd="url(#cbArrow)" style={{ animationDelay: `${(i % 9) * 0.3}s` }} />;
        })}
        {mods.map((m) => {
          const p = pos[m.id];
          if (!p) return null;
          const dim = focus && focus !== m.id && !linked.has(m.id);
          return (
            <g key={m.id} className={`cb-node ${sel === m.id ? "sel" : ""} ${dim ? "dim" : ""}`} transform={`translate(${p.x},${p.y})`}
              onMouseEnter={() => setHover(m.id)} onMouseLeave={() => setHover(null)} onClick={(e) => { e.stopPropagation(); onSel(sel === m.id ? null : m.id); }}>
              <circle r={p.r + 6} className="cb-halo" style={{ stroke: langColor(m.lang) }} />
              <circle r={p.r} fill={langColor(m.lang)} fillOpacity={0.22} stroke={langColor(m.lang)} strokeWidth={1.5} />
              <circle r={p.r} fill="url(#cbNode)" />
              <text y={p.r + 15} textAnchor="middle" className="cb-label">{short(m.name)}</text>
              <text y={p.r + 27} textAnchor="middle" className="cb-sub">{m.file_count} files · {fmtNum(m.loc)} loc</text>
              <title>{m.name}{notes?.modules?.[m.name] ? ` — ${notes.modules[m.name]}` : ""}</title>
            </g>
          );
        })}
      </svg>
      <div className="cb-legend"><span>importer</span><span className="cb-legend-arrow">⟶</span><span>dependency</span><span className="muted"> · size = lines of code · click a node</span></div>
    </div>
  );
}

export function CodebaseCard({ card, expanded = false }: { card: Card; expanded?: boolean }) {
  const d = card.data ?? {};
  const mods: Mod[] = d.modules ?? [];
  const notes = d.notes ?? {};
  const [sel, setSel] = useState<string | null>(null);
  const [file, setFile] = useState<{ path: string; text: string; lines: number } | null>(null);
  const [tab, setTab] = useState<"map" | "modules" | "hot">("map");
  const { busy, run } = useOp(card);
  const selMod = mods.find((m) => m.id === sel);
  const open = async (path: string) => {
    const r = await run("code_file", { path: d.root, file: path }, "opening", false);
    if (r) setFile({ path, text: r.text, lines: r.lines });
  };
  useEffect(() => { core.focus({ type: "project", root: d.root, name: d.name }); }, [d.root, d.name]);
  return (
    <div className={`cb ${expanded ? "cb-xl" : ""}`}>
      <div className="ws-bar">
        <span className="ws-icon">⌬</span>
        <span className="ws-meta">{d.branch ? `⎇ ${d.branch} · ` : ""}{d.root?.replace(/^\/Users\/[^/]+/, "~")}</span>
        <span className="ws-spacer" />
        {!expanded && <GlassBtn tone="ghost" title="Open full size" onClick={() => useStore.getState().set({ expandedCardId: card.id })}>⤢</GlassBtn>}
        <GlassBtn tone="ghost" disabled={!!busy} title="Rescan the project" onClick={() => run("code_refresh", { path: d.root }, "rescanning")}>↻</GlassBtn>
      </div>
      {busy && <div className="ws-busy"><span className="ws-spin" />{busy}…</div>}
      {notes.summary ? (
        <div className="cb-summary">{notes.summary}</div>
      ) : (
        <div className="cb-summary muted">Ask “what does this project do?” and JARVIS will annotate the map.</div>
      )}
      <div className="cb-tiles">
        <div className="cb-tile"><b>{(d.total_files ?? 0).toLocaleString()}</b><span>files</span></div>
        <div className="cb-tile"><b>{fmtNum(d.total_loc ?? 0)}</b><span>lines of code</span></div>
        <div className="cb-tile"><b>{mods.length}</b><span>modules</span></div>
        <div className="cb-tile"><b>{(d.entry_points ?? []).length}</b><span>entry points</span></div>
      </div>
      <div className="cb-langbar">
        {(d.languages ?? []).map((l: any) => <span key={l.lang} style={{ width: `${l.pct}%`, background: langColor(l.lang) }} title={`${l.lang} ${l.pct}% · ${l.loc.toLocaleString()} loc`} />)}
      </div>
      <div className="cb-langs">
        {(d.languages ?? []).slice(0, 6).map((l: any) => <span key={l.lang}><i style={{ background: langColor(l.lang) }} />{l.lang} {l.pct}%</span>)}
      </div>
      <div className="ws-tabs">
        {(["map", "modules", "hot"] as const).map((t) => (
          <button key={t} className={`ws-tab ${tab === t ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); setTab(t); }}>
            {t === "map" ? "ARCHITECTURE" : t === "modules" ? "MODULES" : "KEY FILES"}
          </button>
        ))}
      </div>
      {tab === "map" && (
        <>
          <ArchGraph mods={mods.slice(0, 28)} edges={d.edges ?? []} notes={notes} sel={sel} onSel={setSel} />
          {(notes.architecture ?? []).length > 0 && (
            <ol className="cb-flow">{notes.architecture.map((x: string, i: number) => <li key={i}>{x}</li>)}</ol>
          )}
        </>
      )}
      {tab === "modules" && (
        <div className="cb-mods">
          {mods.map((m) => (
            <button key={m.id} className={`cb-mod ${sel === m.id ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); setSel(sel === m.id ? null : m.id); }}>
              <i style={{ background: langColor(m.lang) }} />
              <span className="cb-mod-name">{m.name}</span>
              <span className="cb-mod-note">{notes.modules?.[m.name] ?? ""}</span>
              <span className="muted small">{m.file_count} · {fmtNum(m.loc)}</span>
            </button>
          ))}
        </div>
      )}
      {tab === "hot" && (
        <div className="cb-hot">
          <div className="cb-hot-col"><div className="cb-h">ENTRY POINTS</div>{(d.entry_points ?? []).map((f: string) => <button key={f} className="cb-file" onClick={(e) => { e.stopPropagation(); open(f); }}>{f}</button>)}</div>
          <div className="cb-hot-col"><div className="cb-h">MOST IMPORTED</div>{(d.hubs ?? []).map((h: any) => <button key={h.path} className="cb-file" onClick={(e) => { e.stopPropagation(); open(h.path); }}>{h.path}<span className="muted"> ×{h.imported_by}</span></button>)}</div>
          <div className="cb-hot-col"><div className="cb-h">LARGEST</div>{(d.largest ?? []).map((h: any) => <button key={h.path} className="cb-file" onClick={(e) => { e.stopPropagation(); open(h.path); }}>{h.path}<span className="muted"> {fmtNum(h.loc)}</span></button>)}</div>
        </div>
      )}
      {selMod && (
        <div className="cb-detail">
          <div className="cb-detail-head"><i style={{ background: langColor(selMod.lang) }} /><b>{selMod.name}</b><span className="muted small"> {selMod.file_count} files · {fmtNum(selMod.loc)} loc</span></div>
          {notes.modules?.[selMod.name] && <div className="cb-detail-note">{notes.modules[selMod.name]}</div>}
          <div className="cb-files">
            {selMod.files.slice(0, expanded ? 80 : 14).map((f: any) => (
              <button key={f.path} className="cb-frow" onClick={(e) => { e.stopPropagation(); open(f.path); }} title={(f.symbols ?? []).join(", ")}>
                <span className="cb-fname">{f.name}</span>
                <span className="cb-fsym">{(f.symbols ?? []).slice(0, 3).map((s: string) => s.split(" ").pop()).join(" · ")}</span>
                <span className="muted small">{f.loc}{f.imported_by ? ` · ←${f.imported_by}` : ""}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {file && (
        <div className="cb-viewer">
          <div className="cb-viewer-head"><span>{file.path}</span><span className="muted small">{file.lines} lines</span><span className="ws-spacer" />
            <GlassBtn tone="ghost" onClick={() => core.sendText(`Explain ${file.path} in ${d.name}`)}>Explain</GlassBtn>
            <GlassBtn tone="ghost" onClick={() => setFile(null)}>×</GlassBtn></div>
          <div className={`ws-code-wrap ${expanded ? "xl" : ""}`}><CodeView text={file.text} lang={file.path.split(".").pop() || ""} maxLines={expanded ? 6000 : 400} /></div>
        </div>
      )}
      {notes.how_to_run && <div className="cb-run"><span className="cb-h">RUN</span><code>{notes.how_to_run}</code></div>}
      {(d.recent_commits ?? []).length > 0 && expanded && (
        <div className="cb-commits"><div className="cb-h">RECENT COMMITS</div>{d.recent_commits.map((c: string) => <div key={c} className="cb-commit"><code>{c.slice(0, 7)}</code> {c.slice(8)}</div>)}</div>
      )}
    </div>
  );
}

export function CodeFileCard({ card, expanded = false }: { card: Card; expanded?: boolean }) {
  const d = card.data ?? {};
  return (
    <div className="ws">
      <div className="ws-bar">
        <span className="ws-icon">⟨⟩</span><span className="ws-meta">{d.lines} lines · {d.root?.split("/").pop()}</span><span className="ws-spacer" />
        {!expanded && <GlassBtn tone="ghost" title="Open full size" onClick={() => useStore.getState().set({ expandedCardId: card.id })}>⤢</GlassBtn>}
      </div>
      <div className={`ws-code-wrap ${expanded ? "xl" : ""}`}><CodeView text={d.text ?? ""} lang={d.language ?? ""} maxLines={expanded ? 20000 : 500} /></div>
    </div>
  );
}

export function CodeChangesCard({ card, expanded = false }: { card: Card; expanded?: boolean }) {
  const d = card.data ?? {};
  const changes: any[] = d.changes ?? [];
  const [openId, setOpenId] = useState<string | null>(changes[0]?.id ?? null);
  const { busy, run } = useOp(card);
  const prevFirst = useRef(changes[0]?.id);
  useEffect(() => { if (changes[0]?.id !== prevFirst.current) { prevFirst.current = changes[0]?.id; setOpenId(changes[0]?.id ?? null); } }, [changes]);
  if (!changes.length) return <div className="muted">No changes yet.</div>;
  const totalAdd = changes.filter((c) => c.status === "applied").reduce((a, c) => a + c.added, 0);
  const totalDel = changes.filter((c) => c.status === "applied").reduce((a, c) => a + c.removed, 0);
  return (
    <div className="ws">
      <div className="ws-bar">
        <span className="ws-icon">±</span><span className="ws-meta">{changes.length} edit{changes.length === 1 ? "" : "s"} · <span className="df-a">+{totalAdd}</span> <span className="df-d">−{totalDel}</span></span>
        <span className="ws-spacer" />
        {!expanded && <GlassBtn tone="ghost" title="Open full size" onClick={() => useStore.getState().set({ expandedCardId: card.id })}>⤢</GlassBtn>}
      </div>
      {busy && <div className="ws-busy"><span className="ws-spin" />{busy}…</div>}
      {changes.map((c) => (
        <div key={c.id} className={`cc ${c.status}`}>
          <button className="cc-head" onClick={(e) => { e.stopPropagation(); setOpenId(openId === c.id ? null : c.id); }}>
            <span className="cc-caret">{openId === c.id ? "▾" : "▸"}</span>
            <span className="cc-file">{c.file}</span>
            {c.created && <span className="cc-tag">NEW</span>}
            {c.status === "undone" && <span className="cc-tag undone">UNDONE</span>}
            <span className="df-a">+{c.added}</span><span className="df-d">−{c.removed}</span>
            <span className="muted small">{ago(c.ts)}</span>
          </button>
          {c.note && <div className="cc-note">{c.note}</div>}
          {openId === c.id && (
            <>
              <div className={`ws-code-wrap ${expanded ? "xl" : ""}`}><DiffView diff={c.diff} /></div>
              {c.status === "applied" && (
                <div className="ws-tools"><GlassBtn tone="amber" disabled={!!busy} onClick={() => run("code_undo", { change_id: c.id }, "reverting")}>↶ Undo this change</GlassBtn></div>
              )}
            </>
          )}
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ full-size workbench overlay
export const EXPANDABLE = new Set(["artifact", "codebase", "code_file", "code_changes"]);
