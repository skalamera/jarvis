import { create } from "zustand";
import type { Account, BriefState, Card, HudState, Message, Telemetry, Toast, ToolEvent } from "../types";

interface Store {
  briefing: BriefState;
  rightTab: "briefing" | "displays";
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
  telemetry: Telemetry | null;
  sessionId: string;
  set: (p: Partial<Store>) => void;
  addMessage: (m: Message) => void;
  upsertJarvis: (turnId: string, text: string, final?: boolean, extra?: Partial<Message>) => void;
  addCard: (c: Card) => void;
  updateCard: (id: string, p: Partial<Card>) => void;
  removeCard: (id: string) => void;
  addTool: (t: ToolEvent) => void;
  clearTurn: () => void;
}

const MAX_CARDS = 14;

export const useStore = create<Store>((set, get) => ({
  briefing: { data: null, refreshing: false, error: "" },
  rightTab: "briefing",
  briefBusy: {},
  toasts: [],
  toast: (t) => {
    const id = Math.random().toString(36).slice(2);
    set((s) => ({ toasts: [...s.toasts.slice(-3), { ...t, id }] }));
    window.setTimeout(() => get().dropToast(id), t.undoItem ? 7000 : 4000);
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
  telemetry: null,
  sessionId: "",
  set: (p) => set(p),
  addMessage: (m) => set((s) => ({ messages: [...s.messages.slice(-80), m] })),
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
      if (idx < 0) return { messages: [...s.messages.slice(-80), msg] };
      const copy = s.messages.slice();
      copy[idx] = msg;
      return { messages: copy };
    }),
  addCard: (c) =>
    set((s) => {
      if (s.cards.some((x) => x.id === c.id)) return {};
      const cards = [c, ...s.cards].slice(0, MAX_CARDS);
      return { cards, rightTab: "displays", focusCardId: c.kind === "confirm" ? c.id : s.focusCardId };
    }),
  updateCard: (id, p) => set((s) => ({ cards: s.cards.map((c) => (c.id === id ? { ...c, ...p } : c)) })),
  removeCard: (id) =>
    set((s) => ({ cards: s.cards.filter((c) => c.id !== id), focusCardId: s.focusCardId === id ? null : s.focusCardId })),
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
