import { create } from "zustand";
import type { Account, BriefState, Card, HudState, Message, Telemetry, Toast, ToolEvent } from "../types";

interface Store {
  briefing: BriefState;
  rightTab: "briefing" | "displays";
  displaysUnseen: number;
  forgetMessages: (ids: string[]) => void;
  briefBusy: Record<string, string>;
  toasts: Toast[];
  toast: (t: Omit<Toast, "id">) => void;
  dropToast: (id: string) => void;
  connected: boolean;
  hud: HudState;
  hermesOk: boolean;
  voiceOk: boolean;
  wakeAvailable: boolean;
  micMode: string;
  micLevel: number;
  outLevel: number;
  speak: boolean;
  micEnabled: boolean;
  accounts: Account[];
  messages: Message[];
  cards: Card[];
  tools: ToolEvent[];
  focusCardId: string | null;
  caption: string;
  logOpen: boolean;
  logUnseen: number;
  telemetry: Telemetry | null;
  sessionId: string;
  /** Card shown full-size in the workbench overlay (files, codebases). */
  expandedCardId: string | null;
  /** Files uploaded but not yet sent with a message (chips above the composer). */
  attachments: { id: string; filename: string; kind: string; size: number }[];
  uploading: number;
  dropActive: boolean;
  /** The preset showcase / narrated support tour while it runs (progress HUD). */
  showcase: { active: boolean; step: number; total: number; label: string; mode?: string } | null;
  /** Sleep mode (dimmed HUD) and the moment the power-on sequence started (ms since epoch, 0 = not playing). */
  asleep: boolean;
  powerOnAt: number;
  set: (p: Partial<Store>) => void;
  addMessage: (m: Message) => void;
  upsertJarvis: (turnId: string, text: string, final?: boolean, extra?: Partial<Message>) => void;
  addCard: (c: Card) => void;
  updateCard: (id: string, p: Partial<Card>) => void;
  removeCard: (id: string) => void;
  addTool: (t: ToolEvent) => void;
  clearTurn: () => void;
}

const MAX_CARDS = 18;

export const useStore = create<Store>((set, get) => ({
  briefing: { data: null, refreshing: false, error: "" },
  rightTab: "briefing",
  displaysUnseen: 0,
  briefBusy: {},
  toasts: [],
  toast: (t) => {
    const id = Math.random().toString(36).slice(2);
    set((s) => ({ toasts: [...s.toasts.slice(-3), { ...t, id }] }));
    window.setTimeout(() => get().dropToast(id), t.undoItem || t.onUndo ? 7000 : 4000);
  },
  dropToast: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
  connected: false,
  hud: "offline",
  hermesOk: false,
  voiceOk: false,
  wakeAvailable: false,
  micMode: "off",
  micLevel: 0,
  outLevel: 0,
  speak: true,
  micEnabled: true,
  accounts: [],
  messages: [],
  cards: [],
  tools: [],
  focusCardId: null,
  caption: "",
  logOpen: localStorage.getItem("jarvis.logOpen") === "1",
  logUnseen: 0,
  telemetry: null,
  sessionId: "",
  expandedCardId: null,
  attachments: [],
  uploading: 0,
  dropActive: false,
  showcase: null,
  asleep: false,
  powerOnAt: 0,
  set: (p) => set(p),
  addMessage: (m) => set((s) => ({ messages: [...s.messages.slice(-80), m], logUnseen: s.logOpen ? 0 : s.logUnseen + 1 })),
  upsertJarvis: (turnId, text, final = false, extra = {}) =>
    set((s) => {
      const idx = s.messages.findIndex((m) => m.role === "jarvis" && m.turnId === turnId);
      const msg: Message = {
        id: idx >= 0 ? s.messages[idx].id : `j_${turnId}`,
        role: "jarvis",
        text,
        turnId,
        pending: !final,
        at: idx >= 0 ? s.messages[idx].at : Date.now(),
        ...extra,
      };
      if (idx < 0) return { messages: [...s.messages.slice(-80), msg], logUnseen: s.logOpen ? 0 : s.logUnseen + 1 };
      const copy = s.messages.slice();
      copy[idx] = msg;
      return { messages: copy };
    }),
  addCard: (c) =>
    set((s) => {
      if (s.cards.some((x) => x.id === c.id)) return {};
      const cards = [c, ...s.cards].slice(0, MAX_CARDS);
      const focusCardId = c.kind === "confirm" ? c.id : s.focusCardId;
      return { cards, rightTab: "displays", displaysUnseen: 0, focusCardId };
    }),
  forgetMessages: (ids) =>
    set((s) => {
      const gone = new Set(ids);
      const cards: Card[] = [];
      for (const c of s.cards) {
        if (c.kind === "email" && gone.has(c.data?.id)) continue;
        if (c.kind === "email_list" || c.kind === "thread") {
          const msgs = (c.data?.messages ?? []).filter((m: any) => !gone.has(m.id));
          if (!msgs.length) continue;
          if (msgs.length !== (c.data?.messages ?? []).length) { cards.push({ ...c, data: { ...c.data, messages: msgs } }); continue; }
        }
        cards.push(c);
      }
      return { cards };
    }),
  updateCard: (id, p) => set((s) => ({ cards: s.cards.map((c) => (c.id === id ? { ...c, ...p } : c)) })),
  removeCard: (id) =>
    set((s) => ({ cards: s.cards.filter((c) => c.id !== id), focusCardId: s.focusCardId === id ? null : s.focusCardId,
      expandedCardId: s.expandedCardId === id ? null : s.expandedCardId })),
  addTool: (t) =>
    set((s) => {
      if (t.status !== "start") {
        const i = [...s.tools].reverse().findIndex((x) => x.tool === t.tool && x.status === "start" && x.turnId === t.turnId);
        if (i >= 0) {
          const real = s.tools.length - 1 - i;
          const copy = s.tools.slice();
          copy[real] = { ...copy[real], status: t.status, duration: t.duration };
          return { tools: copy };
        }
      }
      return { tools: [...s.tools.slice(-30), t] };
    }),
  clearTurn: () => set({ tools: [] }),
}));

export const pendingConfirm = () => get_().cards.find((c) => c.kind === "confirm" && (c.status ?? "pending") === "pending");
const get_ = () => useStore.getState();
