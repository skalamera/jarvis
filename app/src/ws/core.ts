/** Single connection to JARVIS Core. Routes protocol events into the store + audio engine. */
import { useStore } from "../state/store";
import { Microphone, Speaker } from "../voice/audio";
import { media } from "../media/bus";
import type { Card } from "../types";

const TRASH_KINDS = new Set(["gmail_trash", "gmail_delete_permanently"]);

type Cfg = { coreUrl: string; httpUrl: string; token: string };

class CoreLink {
  ws: WebSocket | null = null;
  cfg: Cfg | null = null;
  speaker: Speaker | null = null;
  mic = new Microphone();
  private retry = 0;
  private pingTimer: number | undefined;
  private levelRaf = 0;
  private pylonSeq = 0;
  private pylonWait = new Map<string, (m: any) => void>();

  async init(): Promise<void> {
    this.cfg = window.jarvis
      ? await window.jarvis.config()
      : {
          coreUrl: `ws://127.0.0.1:8765/ws`,
          httpUrl: `http://127.0.0.1:8765`,
          token: new URLSearchParams(location.search).get("token") || "",
        };
    this.speaker = new Speaker();
    this.speaker.onStart = (text) => useStore.getState().set({ caption: text });
    this.speaker.onPlaying = (playing) => {
      this.send({ type: "speaking_audio", playing });
      media.duck("speaking", playing); // lower the music/video while JARVIS talks
      if (!playing) useStore.getState().set({ caption: "" });
    };
    this.speaker.onIdle = () => this.send({ type: "playback_done" });
    this.mic.onChunk = (pcm) => {
      if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(pcm);
    };
    const tick = () => {
      const s = useStore.getState();
      const lvl = this.speaker?.level() ?? 0;
      if (Math.abs(lvl - s.outLevel) > 0.01) s.set({ outLevel: lvl });
      this.levelRaf = requestAnimationFrame(tick);
    };
    this.levelRaf = requestAnimationFrame(tick);
    this.connect();
    window.jarvis?.onListen(() => this.listenNow());
    window.jarvis?.onShowcase?.(() => this.showcase("demo"));
    window.jarvis?.onSleep?.(() => this.toggleSleep());
    this.pollTelemetry();
  }

  private connect(): void {
    if (!this.cfg) return;
    const url = `${this.cfg.coreUrl}?token=${encodeURIComponent(this.cfg.token)}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = async () => {
      this.retry = 0;
      useStore.getState().set({ connected: true, hud: "idle" });
      if (useStore.getState().micEnabled) {
        const ok = await this.mic.start();
        if (!ok) this.system(`Microphone unavailable: ${this.mic.error}`);
      }
      this.pingTimer = window.setInterval(() => this.send({ type: "ping" }), 15000);
    };
    ws.onmessage = (e) => {
      try {
        this.handle(JSON.parse(e.data as string));
      } catch (err) {
        console.error("bad message", err);
      }
    };
    ws.onclose = () => {
      window.clearInterval(this.pingTimer);
      useStore.getState().set({ connected: false, hud: "offline" });
      this.mic.stop();
      this.speaker?.stop();
      const delay = Math.min(8000, 500 * 2 ** this.retry++);
      setTimeout(() => this.connect(), delay);
    };
  }

  send(msg: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  /** Showcase / tour stage directions: open a display full-size, switch the blueprint's tab, clear the deck. */
  private ui(m: any): void {
    const s = useStore.getState();
    if (m.op === "expand") s.set({ expandedCardId: m.card_id && s.cards.some((c) => c.id === m.card_id) ? m.card_id : null });
    else if (m.op === "expand_key") {
      const c = s.cards.find((x) => x.data?.key === m.key);
      if (c) s.set({ expandedCardId: c.id });
    } else if (m.op === "support_tab") window.dispatchEvent(new CustomEvent("jarvis:support_tab", { detail: { tab: m.tab, tier: m.tier } }));
    else if (m.op === "clear_cards") {
      for (const c of s.cards) if (c.kind !== "confirm" && c.kind !== "music" && c.kind !== "video") s.removeCard(c.id);
      s.set({ expandedCardId: null, rightTab: "displays" });
    }
  }

  /** Sleep mode: dim the HUD (⌘⇧S / ⌥⇧S). Waking from the button runs the power-on routine. */
  toggleSleep(): void {
    const s = useStore.getState();
    if (s.asleep) this.send({ type: "power_on" });
    else this.send({ type: "sleep", enabled: true });
  }

  /** The preset showcase (⌘⇧D or "what do you have for me today?"). */
  showcase(which: "demo" | "support_tour" = "demo"): void {
    this.speaker?.stop();
    this.send({ type: "showcase", which });
  }

  private system(text: string): void {
    useStore.getState().addMessage({ id: `s_${Date.now()}`, role: "system", text, at: Date.now() });
  }

  /** Remove every card showing these (now trashed) emails; with an account, offer an UNDO that restores both. */
  private removeTrashed(ids: string[], account: string | null): void {
    const s = useStore.getState();
    if (!ids.length) return;
    const before = s.cards;
    s.forgetMessages(ids);
    const after = new Map(useStore.getState().cards.map((c) => [c.id, c]));
    const changed = before.map((c, i) => ({ c, i })).filter(({ c }) => after.get(c.id) !== c);
    if (!account) return;
    s.toast({
      text: ids.length > 1 ? `${ids.length} emails moved to Trash.` : "Moved to Trash.",
      onUndo: () => {
        this.direct("gmail_restore", { account, message_ids: ids });
        const st = useStore.getState();
        const cards = st.cards.slice();
        for (const { c, i } of changed) {
          const j = cards.findIndex((x) => x.id === c.id);
          if (j >= 0) cards[j] = c;
          else cards.splice(Math.min(i, cards.length), 0, c);
        }
        st.set({ cards });
      },
    });
  }

  private card(raw: any, turnId?: string): Card {
    return { ...raw, turnId, createdAt: Date.now(), status: raw.kind === "confirm" ? raw.data?.status ?? "pending" : undefined };
  }

  private handle(m: any): void {
    const s = useStore.getState();
    switch (m.type) {
      case "hello":
        s.set({ sessionId: m.session_id, accounts: m.accounts ?? [], hermesOk: !!m.hermes, voiceOk: !!m.voice, speak: m.speak });
        break;
      case "health":
      case "pong":
        s.set({
          ...(m.hermes !== undefined ? { hermesOk: !!m.hermes } : {}),
          ...(m.voice !== undefined ? { voiceOk: !!m.voice } : {}),
        });
        break;
      case "state":
        s.set({ hud: m.state });
        media.duck("listening", m.state === "listening");
        break;
      case "mic":
        s.set({ micMode: m.mode, wakeAvailable: !!m.wake_available });
        break;
      case "mic_level":
        s.set({ micLevel: m.level });
        break;
      case "wake":
        this.speaker?.stop();
        this.speaker?.chime("wake");
        break;
      case "listening":
        this.speaker?.stop();
        s.set({ hud: "listening", caption: "" });
        break;
      case "listening_end":
        if (m.empty) s.set({ hud: "idle" });
        break;
      case "transcript":
        break; // user_message follows with the canonical text
      case "user_message":
        s.addMessage({ id: `u_${Date.now()}`, role: "user", text: m.text, source: m.source, at: Date.now() });
        break;
      case "turn_start":
        s.clearTurn();
        s.upsertJarvis(m.turn_id, "", false);
        break;
      case "assistant_delta":
        s.upsertJarvis(m.turn_id, m.text, false);
        break;
      case "assistant_final":
        s.upsertJarvis(m.turn_id, m.text, true, { error: !!m.error, elapsed: m.elapsed });
        if (!s.speak) this.speaker?.chime("done");
        break;
      case "turn_cancelled":
        s.upsertJarvis(m.turn_id, (m.text || "") + (m.text ? " …" : "(interrupted)"), true);
        break;
      case "tool":
        s.addTool({
          id: `${m.turn_id}_${m.tool}_${Date.now()}`,
          turnId: m.turn_id,
          tool: m.tool,
          label: m.label,
          status: m.status,
          preview: m.preview,
          duration: m.duration,
          at: Date.now(),
        });
        break;
      case "forge_action":
        // Spoken "keep it" / "lose it" for the center hologram.
        window.dispatchEvent(new CustomEvent("jarvis:forge", { detail: m.action }));
        break;
      case "card": {
        const c = this.card(m.card, m.turn_id);
        if (c.kind === "media_control") {
          // A voice command for the player, not a display.
          if (!media.control(c.data?.action, c.data?.level)) s.toast({ text: "Nothing is playing.", error: true });
          break;
        }
        if (c.kind === "music" || c.kind === "video") {
          // One player of each kind: a new song/video replaces the old card instead of stacking players.
          for (const x of useStore.getState().cards.filter((x) => x.kind === c.kind)) s.removeCard(x.id);
        }
        const key = c.data?.key;
        // Generated images/videos materialize in the orb first (HoloForge); he accepts or dismisses them there.
        if (c.kind === "generating" && key) {
          const g = c.data.generating || {};
          const f = useStore.getState().forge;
          s.set({ forge: { key, kind: g.kind === "video" ? "video" : "image", prompt: g.prompt || "", aspect: g.aspect,
            started: (g.started ? g.started * 1000 : f && f.key === key ? f.started : Date.now()),
            eta: (g.eta_s ?? (g.kind === "video" ? 90 : 15)) * 1000, error: g.error, result: undefined } });
          break;
        }
        const fg = useStore.getState().forge;
        if (key && fg && fg.key === key && c.kind === "artifact") {
          s.set({ forge: { ...fg, result: c } });
          break;
        }
        const same = key ? useStore.getState().cards.find((x) => x.data?.key === key) : undefined;
        if (same) {
          // The same file / project re-rendered after an edit: refresh that display in place, don't stack copies.
          s.set({ cards: useStore.getState().cards.map((x) => (x.id === same.id ? { ...c, id: same.id } : x)),
            focusCardId: same.id, rightTab: "displays" });
          break;
        }
        s.addCard(c);
        if (c.kind === "draft" && c.turnId) {
          // The draft is the answer: fold away the lookups this same turn made to write it
          // (inbox searches, the thread it read, the drafts list) so only the draft(s) remain.
          const scaffolding = (x: Card) =>
            x.turnId === c.turnId && (x.kind === "email_list" || x.kind === "thread" || x.kind === "email" ||
              (x.kind === "visual.list" && x.title === "Drafts"));
          for (const x of useStore.getState().cards.filter(scaffolding)) s.removeCard(x.id);
        }
        if (c.kind === "confirm") this.speaker?.chime("alert");
        break;
      }
      case "action_result": {
        s.updateCard(m.action_id, { status: m.status, result: m.result });
        const c = s.cards.find((x) => x.id === m.action_id);
        if (m.status === "executed" && c && TRASH_KINDS.has(c.data?.kind)) {
          // Deleted via voice/AUTHORIZE: the emails are gone, so drop their cards and fold the AUTHORIZE card away.
          this.removeTrashed(c.data?.message_ids ?? [], null);
          window.setTimeout(() => useStore.getState().removeCard(m.action_id), 1400);
        }
        break;
      }
      case "draft_saved":
        window.dispatchEvent(new CustomEvent("jarvis:draft_saved", { detail: m }));
        break;
      case "direct_result":
        window.dispatchEvent(new CustomEvent("jarvis:direct_result", { detail: m }));
        if (!m.ok) {
          this.system(`Action failed: ${m.error}`);
          if (m.op === "gmail_trash_now") s.toast({ text: `Delete failed: ${m.error}`, error: true });
        } else if (m.op === "gmail_trash_now") {
          const ids: string[] = m.result?.message_ids ?? [];
          const account: string = m.result?.account ?? m.args?.account;
          this.removeTrashed(ids, account);
        } else if (m.op === "gmail_restore") {
          s.toast({ text: "Restored to inbox." });
        }
        break;
      case "speech":
        if (m.audio) this.speaker?.enqueue(m.turn_id, m.seq, m.audio, m.text);
        break;
      case "stop_audio":
        this.speaker?.stop();
        break;
      case "speak":
        s.set({ speak: m.enabled });
        break;
      case "system_line":
        this.system(m.text);
        break;
      case "system_caption":
        s.set({ caption: m.text });
        window.setTimeout(() => { if (useStore.getState().caption === m.text) useStore.getState().set({ caption: "" }); }, 6000);
        break;
      case "showcase":
        s.set({ showcase: m.active ? { active: true, step: m.step ?? 0, total: m.total ?? 0, label: m.label ?? "", mode: m.mode } : null });
        if (!m.active) window.setTimeout(() => { if (!useStore.getState().showcase) useStore.getState().set({ expandedCardId: null }); }, 2500);
        break;
      case "ui":
        this.ui(m);
        break;
      case "sleep":
        if (m.asleep && !s.asleep) this.speaker?.powerDown();
        s.set({ asleep: !!m.asleep, ...(m.asleep ? { expandedCardId: null, powerOnAt: 0 } : {}) });
        if (m.asleep) media.control("pause");
        break;
      case "power_on":
        this.speaker?.powerOn();
        const at = Date.now();
        s.set({ asleep: false, powerOnAt: at, expandedCardId: null });
        window.setTimeout(() => { if (useStore.getState().powerOnAt === at) useStore.getState().set({ powerOnAt: 0 }); }, (m.ms ?? 4000) + 300);
        break;
      case "voice_error":
        this.system(`Voice: ${m.error}`);
        break;
      case "reset":
        s.set({ messages: [], cards: [], tools: [], sessionId: m.session_id, focusCardId: null });
        break;
      case "briefing":
        s.set({ briefing: { data: m.data ?? null, refreshing: !!m.refreshing, error: m.error || "", slack: m.slack ?? null, slackError: m.slack_error || "" } });
        break;
      case "toast":
        useStore.getState().toast({ text: m.text, error: !!m.error });
        break;
      case "rpc_result":
      case "workspace_result":
      case "pylon_result": {
        const w = this.pylonWait.get(m.req);
        if (w) { this.pylonWait.delete(m.req); w(m); }
        break;
      }
      case "briefing_result": {
        const busy = { ...s.briefBusy };
        delete busy[m.item_id];
        s.set({ briefBusy: busy });
        window.dispatchEvent(new CustomEvent("jarvis:briefing_result", { detail: m }));
        if (!m.ok) s.toast({ text: m.error || "Action failed.", error: true });
        else s.toast({ text: m.text, undoItem: m.undoable ? m.item_id : undefined });
        if (m.ok && m.trashed_ids?.length) s.forgetMessages(m.trashed_ids);
        break;
      }
    }
  }

  // ------------------------------------------------------------ actions from UI
  async sendText(text: string): Promise<void> {
    await this.speaker?.unlock();
    this.speaker?.stop();
    this.send({ type: "user_text", text, source: "text" });
    useStore.getState().set({ attachments: [] });
  }

  /** Upload files dropped / picked / pasted in the HUD. Core keeps sandboxed copies and shows a display for each;
   *  the next message (typed or spoken) is about them. */
  async upload(files: File[]): Promise<void> {
    if (!this.cfg || !files.length) return;
    const s = useStore.getState();
    const fd = new FormData();
    for (const f of files) fd.append("files", f, f.name || "pasted.png");
    s.set({ uploading: files.length });
    try {
      const r = await fetch(`${this.cfg.httpUrl}/upload?token=${encodeURIComponent(this.cfg.token)}`, { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.statusText);
      for (const e of j.errors ?? []) s.toast({ text: e, error: true });
      if (j.files?.length) {
        this.send({ type: "attach", files: j.files });
        useStore.getState().set({ attachments: [...useStore.getState().attachments, ...j.files], rightTab: "displays" });
      }
    } catch (e) {
      s.toast({ text: `Upload failed: ${e instanceof Error ? e.message : e}`, error: true });
    } finally {
      useStore.getState().set({ uploading: 0 });
    }
  }

  /** Map a local project folder (dropped or picked) and make it the conversation's active project. */
  async openFolder(path: string): Promise<void> {
    const r = await this.workspace("code_open_folder", { path });
    if (!r.ok) useStore.getState().toast({ text: r.error || "Couldn't open that folder.", error: true });
  }

  /** Tell Core which file / project is on screen, so "this" in the next message refers to it. */
  focus(f: Record<string, unknown> | null): void {
    this.send({ type: "focus", focus: f });
  }

  /** Click-only file / code card op (allow-listed in Core; every write is versioned and undoable). */
  workspace(op: string, args: Record<string, unknown>): Promise<{ ok: boolean; result?: any; error?: string }> {
    const req = `w${++this.pylonSeq}`;
    return new Promise((resolve) => {
      const timer = window.setTimeout(() => { this.pylonWait.delete(req); resolve({ ok: false, error: "Timed out waiting for Core." }); }, 120_000);
      this.pylonWait.set(req, (m) => { window.clearTimeout(timer); resolve(m); });
      this.send({ type: "workspace", op, args, req });
    });
  }

  garageFileUrl(id: string): string {
    if (!this.cfg) return "";
    return `${this.cfg.httpUrl}/garage/file/${encodeURIComponent(id)}?token=${encodeURIComponent(this.cfg.token)}`;
  }
  videoUrl(id: string): string {
    if (!this.cfg) return "";
    return `${this.cfg.httpUrl}/artifact/${id}/raw?proxy=1&token=${encodeURIComponent(this.cfg.token)}`;
  }
  videoThumbUrl(id: string, i: number): string {
    if (!this.cfg) return "";
    return `${this.cfg.httpUrl}/artifact/${id}/thumb/${i}?token=${encodeURIComponent(this.cfg.token)}`;
  }
  artifactUrl(id: string, version: number): string {
    if (!this.cfg) return "";
    return `${this.cfg.httpUrl}/artifact/${id}/raw?v=${version}&token=${encodeURIComponent(this.cfg.token)}`;
  }
  async listenNow(): Promise<void> {
    await this.speaker?.unlock();
    this.speaker?.stop();
    if (!useStore.getState().micEnabled) await this.setMic(true);
    this.send({ type: "listen_now" });
  }
  async pttStart(): Promise<void> {
    await this.speaker?.unlock();
    this.speaker?.stop();
    this.send({ type: "ptt_start" });
  }
  pttEnd(): void {
    this.send({ type: "ptt_end" });
  }
  cancel(): void {
    this.speaker?.stop();
    this.send({ type: "cancel" });
  }
  confirm(id: string, ok: boolean): void {
    this.speaker?.chime("click");
    this.send({ type: ok ? "confirm" : "reject", action_id: id, source: "click" });
  }
  setSpeak(enabled: boolean): void {
    if (!enabled) this.speaker?.stop();
    this.send({ type: "speak", enabled });
  }
  async setMic(enabled: boolean): Promise<void> {
    useStore.getState().set({ micEnabled: enabled });
    if (enabled) {
      const ok = await this.mic.start();
      if (!ok) this.system(`Microphone unavailable: ${this.mic.error}`);
    } else this.mic.stop();
    this.send({ type: "mic", enabled });
  }
  reset(): void {
    this.speaker?.stop();
    this.send({ type: "reset" });
  }
  direct(op: string, args: Record<string, unknown>): void {
    this.send({ type: "direct", op, args });
  }
  briefingRefresh(): void {
    this.send({ type: "briefing_refresh" });
  }
  /** Click-only Pylon op (or options / AI draft). Resolves with Core's pylon_result for this request. */
  pylon(op: string, args: Record<string, unknown>): Promise<{ ok: boolean; result?: any; error?: string }> {
    const req = `p${++this.pylonSeq}`;
    return new Promise((resolve) => {
      const timer = window.setTimeout(() => { this.pylonWait.delete(req); resolve({ ok: false, error: "Timed out waiting for Core." }); }, 120_000);
      this.pylonWait.set(req, (m) => { window.clearTimeout(timer); resolve(m); });
      this.send({ type: "pylon", op, args, req });
    });
  }

  /** Read-only HUD request (allow-listed in Core), e.g. recompute directions for another travel mode. */
  rpc(op: string, args: Record<string, unknown>): Promise<{ ok: boolean; result?: any; error?: string }> {
    const req = `r${++this.pylonSeq}`;
    return new Promise((resolve) => {
      const timer = window.setTimeout(() => { this.pylonWait.delete(req); resolve({ ok: false, error: "Timed out waiting for Core." }); }, 60_000);
      this.pylonWait.set(req, (m) => { window.clearTimeout(timer); resolve(m); });
      this.send({ type: "rpc", op, args, req });
    });
  }

  /** Click on a place row: Core fetches the full Google card and pushes it through the feed. */
  openPlace(placeId: string): void {
    this.send({ type: "place_open", place_id: placeId });
  }

  /** Click on a game in a scoreboard card: Core builds the full game card and pushes it. */
  openGame(league: string, eventId: string): void {
    this.send({ type: "game_open", league, event_id: eventId });
  }

  /** Click on a ticker anywhere in a market card: Core builds the full stock / crypto card and pushes it. */
  /** Route to a place inside the HUD: Core computes it and pushes a directions card. */
  openDirections(destination: string): void {
    this.send({ type: "directions_open", destination });
  }

  openMarket(symbol: string, kind: "stock" | "crypto" = "stock"): void {
    this.send({ type: "market_open", symbol, kind });
  }

  briefingAction(itemId: string, action: string, extra: Record<string, unknown> = {}): void {
    const s = useStore.getState();
    if (action !== "undo") s.set({ briefBusy: { ...s.briefBusy, [itemId]: action } });
    this.send({ type: "briefing_action", item_id: itemId, action, ...extra });
  }
  updateDraft(p: Record<string, unknown>): void {
    this.send({ type: "draft_update", ...p });
  }
  openExternal(url: string): void {
    if (window.jarvis) window.jarvis.open(url);
    else window.open(url, "_blank", "noopener");
  }

  private async pollTelemetry(): Promise<void> {
    const load = async () => {
      if (!this.cfg) return;
      try {
        const r = await fetch(`${this.cfg.httpUrl}/telemetry?token=${encodeURIComponent(this.cfg.token)}`);
        if (r.ok) useStore.getState().set({ telemetry: await r.json() });
      } catch {
        /* core offline */
      }
    };
    await load();
    window.setInterval(load, 60_000);
  }
}

export const core = new CoreLink();
