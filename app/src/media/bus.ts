/** Media players in the HUD (YouTube embeds driven over postMessage, no external script needed).
 *  One player is "active" at a time: starting a video pauses the music and vice versa. Voice commands
 *  (pause / next / volume) go to the active player, and playback ducks while JARVIS listens or speaks. */

export type YTInfo = { state: number; time: number; duration: number; volume: number; muted: boolean; title: string };

export interface MediaPlayer {
  id: string;
  kind: "music" | "video";
  command: (func: string, args?: unknown[]) => void;
  next?: () => void;
  previous?: () => void;
  stop: () => void;
  playing: () => boolean;
}

const DUCK = 0.2; // volume multiplier while JARVIS listens / speaks

class MediaBus {
  private players = new Map<string, MediaPlayer>();
  private activeId: string | null = null;
  private ducks = new Set<string>();
  volume = 70;
  muted = false;
  onChange: (playing: boolean) => void = () => {};
  private volSubs = new Set<() => void>();

  /** Volume/mute listeners (the cards' sliders follow voice commands like "turn it down"). */
  subscribeVolume(fn: () => void): () => void {
    this.volSubs.add(fn);
    return () => this.volSubs.delete(fn);
  }

  register(p: MediaPlayer): void {
    this.players.set(p.id, p);
  }

  unregister(id: string): void {
    this.players.delete(id);
    if (this.activeId === id) {
      this.activeId = null;
      this.onChange(false);
    }
  }

  /** A player started: pause every other one and make this the target for voice commands. */
  activate(id: string): void {
    this.activeId = id;
    for (const [pid, p] of this.players) if (pid !== id && p.playing()) p.command("pauseVideo");
    this.applyVolume();
    this.onChange(true);
  }

  isActive(id: string): boolean {
    return this.activeId === id;
  }

  get active(): MediaPlayer | undefined {
    return this.activeId ? this.players.get(this.activeId) : undefined;
  }

  /** Effective volume for the active player (ducked while JARVIS is listening or talking). */
  effectiveVolume(): number {
    return Math.round(this.volume * (this.ducks.size ? DUCK : 1));
  }

  applyVolume(): void {
    for (const fn of this.volSubs) fn();
    const p = this.active;
    if (!p) return;
    p.command("setVolume", [this.effectiveVolume()]);
    p.command(this.muted ? "mute" : "unMute");
  }

  duck(reason: string, on: boolean): void {
    const before = this.ducks.size;
    if (on) this.ducks.add(reason);
    else this.ducks.delete(reason);
    if (!!before !== !!this.ducks.size) this.applyVolume();
  }

  setVolume(v: number): void {
    this.volume = Math.max(0, Math.min(100, Math.round(v)));
    this.muted = false;
    this.applyVolume();
  }

  control(action: string, level?: number): boolean {
    const p = this.active ?? [...this.players.values()].pop();
    if (!p) return false;
    switch (action) {
      case "pause": p.command("pauseVideo"); break;
      case "resume": this.activeId = p.id; p.command("playVideo"); this.applyVolume(); break;
      case "next": p.next ? p.next() : p.command("seekTo", [1e7, true]); break;
      case "previous": p.previous ? p.previous() : p.command("seekTo", [0, true]); break;
      case "stop": p.stop(); break;
      case "volume_up": this.setVolume(this.volume + 15); break;
      case "volume_down": this.setVolume(this.volume - 15); break;
      case "set_volume": if (level != null) this.setVolume(level); break;
      case "mute": this.muted = true; this.applyVolume(); break;
      case "unmute": this.muted = false; this.applyVolume(); break;
      default: return false;
    }
    return true;
  }
}

export const media = new MediaBus();

/** Build the embed URL. Music hides YouTube's own controls (the card has them); videos keep them. */
export function embedUrl(videoId: string, opts: { controls?: boolean; start?: number } = {}): string {
  const q = new URLSearchParams({
    enablejsapi: "1", autoplay: "1", playsinline: "1", rel: "0", modestbranding: "1", iv_load_policy: "3",
    controls: opts.controls ? "1" : "0", fs: opts.controls ? "1" : "0",
  });
  if (opts.start) q.set("start", String(Math.floor(opts.start)));
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?${q}`;
}

/** Talk to one embedded player: handshake, commands, and parsed state updates. Returns a detach function. */
export function attachYT(
  frame: HTMLIFrameElement,
  onInfo: (i: Partial<YTInfo>) => void,
  onEvent: (event: "ready" | "state" | "error", value?: number) => void,
): { command: (func: string, args?: unknown[]) => void; detach: () => void } {
  const post = (msg: unknown) => frame.contentWindow?.postMessage(JSON.stringify(msg), "*");
  const command = (func: string, args: unknown[] = []) => post({ event: "command", func, args, id: 1, channel: "widget" });
  let ready = false;
  const listen = () => post({ event: "listening", id: 1, channel: "widget" });
  const onMsg = (e: MessageEvent) => {
    if (e.source !== frame.contentWindow) return;
    let d: any;
    try { d = typeof e.data === "string" ? JSON.parse(e.data) : e.data; } catch { return; }
    if (!d || typeof d !== "object") return;
    if (d.event === "onReady") { ready = true; onEvent("ready"); }
    else if (d.event === "onStateChange") onEvent("state", Number(d.info));
    else if (d.event === "onError") onEvent("error", Number(d.info));
    else if (d.event === "infoDelivery" && d.info) {
      const i = d.info;
      const out: Partial<YTInfo> = {};
      if (typeof i.playerState === "number") out.state = i.playerState;
      if (typeof i.currentTime === "number") out.time = i.currentTime;
      if (typeof i.duration === "number" && i.duration > 0) out.duration = i.duration;
      if (typeof i.volume === "number") out.volume = i.volume;
      if (typeof i.muted === "boolean") out.muted = i.muted;
      if (i.videoData?.title) out.title = i.videoData.title;
      if (typeof i.playerState === "number") onEvent("state", i.playerState);
      onInfo(out);
    }
  };
  window.addEventListener("message", onMsg);
  frame.addEventListener("load", listen);
  const t = window.setInterval(() => { if (!ready) listen(); }, 400);
  return { command, detach: () => { window.removeEventListener("message", onMsg); frame.removeEventListener("load", listen); window.clearInterval(t); } };
}

export function fmtTime(s?: number | null): string {
  if (s == null || !isFinite(s)) return "0:00";
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}
