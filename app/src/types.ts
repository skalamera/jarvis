export type HudState = "idle" | "listening" | "thinking" | "speaking" | "confirm" | "offline";

export interface Card {
  id: string;
  kind: string;
  title: string;
  account: string | null;
  data: any;
  turnId?: string;
  createdAt: number;
  status?: "pending" | "executed" | "cancelled" | "failed" | "expired";
  result?: any;
}

export interface ToolEvent {
  id: string;
  turnId: string;
  tool: string;
  label: string;
  status: "start" | "done" | "error";
  preview?: string;
  duration?: number;
  at: number;
}

export interface Message {
  id: string;
  role: "user" | "jarvis" | "system";
  text: string;
  turnId?: string;
  source?: string;
  pending?: boolean;
  error?: boolean;
  elapsed?: number;
  at: number;
}

export interface Account {
  account: string;
  email: string;
}

export interface Telemetry {
  now: string;
  accounts: { account: string; email: string; unread?: number; threads_unread?: number; error?: string;
    events?: { summary: string; start: string; all_day: boolean; hangout?: string }[] }[];
}

export interface BriefMessage {
  account: string; email: string; id: string; threadId: string; from_name: string; from: string;
  subject: string; internalDate: number; unread: boolean;
}

export interface BriefItem {
  id: string;
  messages: BriefMessage[];
  new?: boolean;
  // todo
  title?: string; detail?: string; urgency?: "high" | "normal"; kind?: string; due?: string;
  // priority
  reason?: string; snippet?: string;
  // topic item
  headline?: string; summary?: string;
  reply_suggestion?: string;
}

export interface BriefData {
  todos: BriefItem[];
  priority: BriefItem[];
  topics: { id: string; title: string; emoji: string; items: BriefItem[] }[];
  generated_at: number; scanned: number; took_s: number;
}

export interface BriefState { data: BriefData | null; refreshing: boolean; error: string }

export interface Toast { id: string; text: string; undoItem?: string; error?: boolean }

declare global {
  interface Window {
    jarvis?: {
      config: () => Promise<{ coreUrl: string; httpUrl: string; token: string }>;
      onListen: (cb: () => void) => () => void;
      open: (url: string) => void;
    };
  }
}
