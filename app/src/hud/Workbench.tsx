/** Full-size glass workbench for file and code displays, the drag-and-drop layer, and the composer's
 *  attachment controls. The workbench keeps the orb + captions visible so the conversation continues while
 *  he works on the file with JARVIS. */
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useStore } from "../state/store";
import { core } from "../ws/core";
import { ArtifactCard, CodebaseCard, CodeChangesCard, CodeFileCard } from "../cards/WorkspaceCards";
import { GarageCard } from "../cards/GarageCards";
import { EatsMenuCard } from "../cards/EatsCards";
import { TradeDeskCard, TradeInsightsCard } from "../cards/TradeCards";
import { SupportBlueprintCard } from "../cards/SupportCards";
import { VideoAnalysisCard } from "../cards/VideoCards";

const XL: Record<string, (p: { card: any; expanded?: boolean }) => React.ReactElement> = {
  artifact: ArtifactCard, codebase: CodebaseCard, code_file: CodeFileCard, code_changes: CodeChangesCard,
  support_blueprint: SupportBlueprintCard,
  garage: GarageCard,
  eats_menu: EatsMenuCard,
  trade_desk: TradeDeskCard,
  trade_insights: TradeInsightsCard as any,
  video_analysis: VideoAnalysisCard,
};

export function Workbench() {
  const id = useStore((s) => s.expandedCardId);
  const card = useStore((s) => s.cards.find((c) => c.id === s.expandedCardId));
  const hud = useStore((s) => s.hud);
  const caption = useStore((s) => s.caption);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && useStore.getState().expandedCardId) { e.stopImmediatePropagation(); useStore.getState().set({ expandedCardId: null }); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
  const Body = card ? XL[card.kind] : null;
  return createPortal(
    <AnimatePresence>
      {card && Body && (
        <motion.div key={id} className="wb-scrim" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => useStore.getState().set({ expandedCardId: null })}>
          <motion.div className="wb" initial={{ opacity: 0, scale: 0.96, y: 18, filter: "blur(8px)" }} animate={{ opacity: 1, scale: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, scale: 0.97, filter: "blur(6px)" }} transition={{ type: "spring", stiffness: 240, damping: 26 }} onClick={(e) => e.stopPropagation()}>
            <span className="corner tl" /><span className="corner tr" /><span className="corner bl" /><span className="corner br" />
            <header className="wb-head">
              <span className="wb-kind">{card.kind === "artifact" ? "WORKBENCH" : card.kind === "codebase" ? "CODEBASE" : card.kind === "code_changes" ? "CHANGES" : card.kind === "support_blueprint" ? "BLUEPRINT" : card.kind === "garage" ? "GARAGE" : card.kind === "eats_menu" ? "MENU" : card.kind === "trade_desk" ? "TRADING DESK" : card.kind === "trade_insights" ? "INTELLIGENCE" : card.kind === "video_analysis" ? "VIDEO ANALYSIS" : "SOURCE"}</span>
              <span className="wb-title">{card.title}</span>
              <span className={`wb-state st-${hud}`}><i />{hud === "speaking" ? "JARVIS" : hud.toUpperCase()}</span>
              <button className="holo-x" onClick={() => useStore.getState().set({ expandedCardId: null })} title="Close (Esc)">×</button>
            </header>
            <div className="wb-body"><Body card={card} expanded /></div>
            <footer className="wb-foot">
              <span className="wb-caption">{caption || (card.kind === "support_blueprint" ? "Ask about any part: “what does Hadrian do?”, “how is a feature request routed?”, “what's Draft-Reply Mode?”" : card.kind === "trade_desk" || card.kind === "trade_insights" ? "Ask: “how's my portfolio?”, “should I trim MSTR?”, “buy $50 of Bitcoin”, “alert me if ETH drops below 2,500”" : card.kind === "video_analysis" ? "Ask about it: “what error popped up?”, “what did I tap after logging in?”, “turn this into steps”, “write a bug report”" : card.kind === "eats_menu" ? "Tap + to add, or say it: “add an everything bagel with lox, toasted”, “what's good here?”, “order it”" : card.kind === "garage" ? "Ask about your CL600: “when is the oil due?”, “what fuse is the head unit?”, “plan a wheel upgrade”, or attach a photo of the problem" : "Talk to JARVIS about this: “what stands out?”, “fix the totals row”, “refactor this function”…")}</span>
              <WbInput />
            </footer>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

function WbInput() {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);
  return (
    <input ref={ref} className="wb-input" placeholder="Speak, or type to JARVIS…" onKeyDown={(e) => {
      e.stopPropagation();
      const t = (e.target as HTMLInputElement).value.trim();
      if (e.key === "Enter" && t) { core.sendText(t); (e.target as HTMLInputElement).value = ""; }
      if (e.key === "Escape") useStore.getState().set({ expandedCardId: null });
    }} />
  );
}

/** Drop files anywhere to upload; drop a folder to map it as a codebase. */
export function DropZone() {
  const active = useStore((s) => s.dropActive);
  const depth = useRef(0);
  useEffect(() => {
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const enter = (e: DragEvent) => { if (!hasFiles(e)) return; e.preventDefault(); depth.current++; useStore.getState().set({ dropActive: true }); };
    const over = (e: DragEvent) => { if (hasFiles(e)) { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = "copy"; } };
    const leave = () => { depth.current = Math.max(0, depth.current - 1); if (!depth.current) useStore.getState().set({ dropActive: false }); };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth.current = 0;
      useStore.getState().set({ dropActive: false });
      const items = Array.from(e.dataTransfer?.items ?? []);
      const files: File[] = [];
      items.forEach((it, i) => {
        const entry = it.webkitGetAsEntry?.();
        const f = e.dataTransfer?.files[i];
        if (!f) return;
        if (entry?.isDirectory) {
          const p = window.jarvis?.pathForFile?.(f);
          if (p) core.openFolder(p);
          else useStore.getState().toast({ text: "Folders can only be opened in the desktop app.", error: true });
        } else files.push(f);
      });
      if (files.length) core.upload(files);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => { window.removeEventListener("dragenter", enter); window.removeEventListener("dragover", over); window.removeEventListener("dragleave", leave); window.removeEventListener("drop", drop); };
  }, []);
  return (
    <AnimatePresence>
      {active && (
        <motion.div className="dropzone" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <div className="dropzone-box">
            <div className="dropzone-ring" />
            <div className="dropzone-title">DROP TO ANALYZE</div>
            <div className="muted">Images · PDFs · Word · spreadsheets · CSV · code — or a project folder to map it</div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

const ICON: Record<string, string> = { image: "◩", csv: "▦", xlsx: "▦", pdf: "▤", docx: "▤", code: "⟨⟩", text: "≡" };

/** Paperclip + folder buttons for the composer, and chips for files waiting to go with the next message. */
export function AttachControls() {
  const input = useRef<HTMLInputElement>(null);
  const uploading = useStore((s) => s.uploading);
  return (
    <>
      {createPortal(<input ref={input} type="file" multiple hidden className="attach-file" onChange={(e) => { const f = Array.from(e.target.files ?? []); e.target.value = ""; core.upload(f); }} />, document.body)}
      <button className="attach-btn" title="Attach files (or drop them anywhere)" onClick={() => input.current?.click()} disabled={!!uploading}>
        {uploading ? <span className="ws-spin" /> : <svg viewBox="0 0 24 24" width="17" height="17"><path fill="currentColor" d="M16.5 6.5v10a4.5 4.5 0 0 1-9 0V5a3 3 0 0 1 6 0v10.5a1.5 1.5 0 0 1-3 0V6.5H9v9a3 3 0 0 0 6 0V5a4.5 4.5 0 0 0-9 0v11.5a6 6 0 0 0 12 0v-10h-1.5Z" /></svg>}
      </button>
      {window.jarvis?.pickFolder && (
        <button className="attach-btn" title="Open a codebase" onClick={async () => { const p = await window.jarvis!.pickFolder!(); if (p) core.openFolder(p); }}>
          <svg viewBox="0 0 24 24" width="17" height="17"><path fill="currentColor" d="M8.6 16.6 4 12l4.6-4.6L7.2 6 1.2 12l6 6 1.4-1.4Zm6.8 0L20 12l-4.6-4.6L16.8 6l6 6-6 6-1.4-1.4Z" /></svg>
        </button>
      )}
    </>
  );
}

export function AttachChips() {
  const items = useStore((s) => s.attachments);
  if (!items.length) return null;
  return (
    <div className="attach-chips">
      {items.map((a) => (
        <span key={a.id} className="attach-chip" onClick={() => {
          const c = useStore.getState().cards.find((x) => x.data?.artifact?.id === a.id);
          if (c) useStore.getState().set({ expandedCardId: c.id });
        }}>
          <i>{ICON[a.kind] ?? "◇"}</i>{a.filename}
          <button onClick={(e) => { e.stopPropagation(); useStore.getState().set({ attachments: useStore.getState().attachments.filter((x) => x.id !== a.id) }); }}>×</button>
        </span>
      ))}
      <span className="muted small">Ask about {items.length > 1 ? "them" : "it"}: “summarize this”, “what stands out?”</span>
    </div>
  );
}
