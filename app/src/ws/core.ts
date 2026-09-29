/** Single connection to JARVIS Core. Routes protocol events into the store + audio engine. */
import { useStore } from "../state/store";
import { Microphone, Speaker } from "../voice/audio";
import type { Card } from "../types";

type Cfg = { coreUrl: string; httpUrl: string; token: string };

class CoreLink {
  ws: WebSocket | null = null;
  cfg: Cfg | null = null;
  speaker: Speaker | null = null;
  mic = new Microphone();
  private retry = 0;
  private pingTimer: number | undefined;
  private levelRaf = 0;

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

  private system(text: string): void {
    useStore.getState().addMessage({ id: `s_${Date.now()}`, role: "system", text, at: Date.now() });
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
      case "state":
        s.set({ hud: m.state });
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
      case "card": {
        const c = this.card(m.card, m.turn_id);
        s.addCard(c);
        if (c.kind === "confirm") this.speaker?.chime("alert");
        break;
      }
      case "action_result":
        s.updateCard(m.action_id, { status: m.status, result: m.result });
        break;
      case "draft_saved":
        window.dispatchEvent(new CustomEvent("jarvis:draft_saved", { detail: m }));
        break;
      case "direct_result":
        window.dispatchEvent(new CustomEvent("jarvis:direct_result", { detail: m }));
        if (!m.ok) this.system(`Action failed: ${m.error}`);
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
      case "voice_error":
        this.system(`Voice: ${m.error}`);
        break;
      case "reset":
        s.set({ messages: [], cards: [], tools: [], sessionId: m.session_id, focusCardId: null });
        break;
      case "briefing":
        s.set({ briefing: { data: m.data ?? null, refreshing: !!m.refreshing, error: m.error || "" } });
        break;
      case "briefing_result": {
        const busy = { ...s.briefBusy };
        delete busy[m.item_id];
        s.set({ briefBusy: busy });
        window.dispatchEvent(new CustomEvent("jarvis:briefing_result", { detail: m }));
        if (!m.ok) s.toast({ text: m.error || "Action failed.", error: true });
        else s.toast({ text: m.text, undoItem: m.undoable ? m.item_id : undefined });
        break;
      }
    }
  }

  // ------------------------------------------------------------ actions from UI
  async sendText(text: string): Promise<void> {
    await this.speaker?.unlock();
    this.speaker?.stop();
    this.send({ type: "user_text", text, source: "text" });
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
