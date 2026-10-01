/** Speech playback queue (with output level analyser for the reactor) + microphone capture. */

type Clip = { turnId: string; seq: number; buf: AudioBuffer; text: string };

export class Speaker {
  private ctx: AudioContext;
  private analyser: AnalyserNode;
  private gain: GainNode;
  private queue: Clip[] = [];
  private current: AudioBufferSourceNode | null = null;
  private data: Uint8Array<ArrayBuffer>;
  onStart: (text: string) => void = () => {};
  onIdle: () => void = () => {};
  onPlaying: (playing: boolean) => void = () => {};

  constructor() {
    this.ctx = new AudioContext();
    this.gain = this.ctx.createGain();
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    this.analyser.smoothingTimeConstant = 0.6;
    this.gain.connect(this.analyser);
    this.analyser.connect(this.ctx.destination);
    this.data = new Uint8Array(new ArrayBuffer(this.analyser.frequencyBinCount));
  }

  get playing(): boolean {
    return this.current !== null;
  }

  level(): number {
    if (!this.current) return 0;
    this.analyser.getByteTimeDomainData(this.data);
    let sum = 0;
    for (let i = 0; i < this.data.length; i++) {
      const v = (this.data[i] - 128) / 128;
      sum += v * v;
    }
    return Math.min(1, Math.sqrt(sum / this.data.length) * 3.2);
  }

  async enqueue(turnId: string, seq: number, b64: string, text: string): Promise<void> {
    if (this.ctx.state === "suspended") await this.ctx.resume();
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const buf = await this.ctx.decodeAudioData(bytes.buffer);
    this.queue.push({ turnId, seq, buf, text });
    this.queue.sort((a, b) => (a.turnId === b.turnId ? a.seq - b.seq : 0));
    if (!this.current) this.next();
  }

  private next(): void {
    const clip = this.queue.shift();
    if (!clip) {
      this.current = null;
      this.onPlaying(false);
      this.onIdle();
      return;
    }
    const src = this.ctx.createBufferSource();
    src.buffer = clip.buf;
    src.connect(this.gain);
    src.onended = () => {
      if (this.current === src) this.next();
    };
    const wasIdle = this.current === null;
    this.current = src;
    if (wasIdle) this.onPlaying(true);
    this.onStart(clip.text);
    src.start();
  }

  stop(): void {
    this.queue = [];
    const c = this.current;
    this.current = null;
    if (c) {
      try {
        c.stop();
      } catch {
        /* already stopped */
      }
      this.onPlaying(false);
    }
  }

  async unlock(): Promise<void> {
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  /** Cinematic power-on (~3.6 s, synthesized): sub rumble swelling under a rising turbine whine, relay clicks as
   *  systems come online, a filtered-noise whoosh into an impact, then a bright two-note "online" chime. */
  powerOn(): void {
    const c = this.ctx, t = c.currentTime;
    const out = c.createGain();
    out.gain.value = 0.9;
    const comp = c.createDynamicsCompressor();
    out.connect(comp).connect(c.destination);
    const env = (g: GainNode, pts: [number, number][]) => {
      g.gain.setValueAtTime(0.0001, t);
      for (const [at, v] of pts) g.gain.exponentialRampToValueAtTime(Math.max(0.0001, v), t + at);
    };
    // 1) sub rumble
    const sub = c.createOscillator(), subG = c.createGain();
    sub.type = "sine";
    sub.frequency.setValueAtTime(38, t);
    sub.frequency.exponentialRampToValueAtTime(62, t + 2.6);
    env(subG, [[0.4, 0.22], [2.4, 0.32], [2.75, 0.5], [3.6, 0.0001]]);
    sub.connect(subG).connect(out);
    sub.start(t); sub.stop(t + 3.7);
    // 2) turbine whine: two detuned saws through a sweeping lowpass
    const lp = c.createBiquadFilter();
    lp.type = "lowpass"; lp.Q.value = 6;
    lp.frequency.setValueAtTime(300, t);
    lp.frequency.exponentialRampToValueAtTime(5200, t + 2.6);
    const wG = c.createGain();
    env(wG, [[0.3, 0.012], [2.5, 0.05], [2.75, 0.0001]]);
    lp.connect(wG).connect(out);
    for (const det of [0, 7]) {
      const o = c.createOscillator();
      o.type = "sawtooth"; o.detune.value = det;
      o.frequency.setValueAtTime(90, t);
      o.frequency.exponentialRampToValueAtTime(1180, t + 2.6);
      o.connect(lp); o.start(t); o.stop(t + 2.8);
    }
    // 3) relay clicks / systems coming online
    [0.35, 0.62, 0.95, 1.22, 1.5, 1.72, 1.95, 2.12, 2.28].forEach((at, i) => {
      const o = c.createOscillator(), g = c.createGain();
      o.type = "square"; o.frequency.value = 1800 + i * 160;
      g.gain.setValueAtTime(0.0001, t + at);
      g.gain.exponentialRampToValueAtTime(0.05, t + at + 0.004);
      g.gain.exponentialRampToValueAtTime(0.0001, t + at + 0.035);
      o.connect(g).connect(out); o.start(t + at); o.stop(t + at + 0.05);
    });
    // 4) whoosh (noise through a rising bandpass) into the impact
    const len = Math.floor(c.sampleRate * 1.4);
    const nb = c.createBuffer(1, len, c.sampleRate);
    const ch = nb.getChannelData(0);
    for (let i = 0; i < len; i++) ch[i] = Math.random() * 2 - 1;
    const ns = c.createBufferSource(); ns.buffer = nb;
    const bp = c.createBiquadFilter(); bp.type = "bandpass"; bp.Q.value = 1.4;
    bp.frequency.setValueAtTime(400, t + 1.4);
    bp.frequency.exponentialRampToValueAtTime(6000, t + 2.7);
    const nG = c.createGain();
    nG.gain.setValueAtTime(0.0001, t + 1.4);
    nG.gain.exponentialRampToValueAtTime(0.16, t + 2.68);
    nG.gain.exponentialRampToValueAtTime(0.0001, t + 2.8);
    ns.connect(bp).connect(nG).connect(out); ns.start(t + 1.4); ns.stop(t + 2.85);
    // 5) impact: pitched-down thump + bright shimmer
    const k = c.createOscillator(), kG = c.createGain();
    k.type = "sine";
    k.frequency.setValueAtTime(140, t + 2.72);
    k.frequency.exponentialRampToValueAtTime(42, t + 3.2);
    kG.gain.setValueAtTime(0.0001, t + 2.72);
    kG.gain.exponentialRampToValueAtTime(0.7, t + 2.74);
    kG.gain.exponentialRampToValueAtTime(0.0001, t + 3.4);
    k.connect(kG).connect(out); k.start(t + 2.72); k.stop(t + 3.5);
    // 6) "online" chime: two bell-like notes with a soft tail
    for (const [f, at] of [[1046.5, 2.85], [1568, 3.0], [2093, 3.0]] as [number, number][]) {
      const o = c.createOscillator(), g = c.createGain();
      o.type = "sine"; o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t + at);
      g.gain.exponentialRampToValueAtTime(f > 2000 ? 0.025 : 0.08, t + at + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0001, t + at + 1.1);
      o.connect(g).connect(out); o.start(t + at); o.stop(t + at + 1.2);
    }
  }

  /** Power-down: a falling sweep and a soft low thud (~1 s). */
  powerDown(): void {
    const c = this.ctx, t = c.currentTime;
    const o = c.createOscillator(), g = c.createGain(), lp = c.createBiquadFilter();
    o.type = "sawtooth";
    o.frequency.setValueAtTime(620, t);
    o.frequency.exponentialRampToValueAtTime(55, t + 0.9);
    lp.type = "lowpass";
    lp.frequency.setValueAtTime(2400, t);
    lp.frequency.exponentialRampToValueAtTime(180, t + 0.9);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.05, t + 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 1.0);
    o.connect(lp).connect(g).connect(c.destination);
    o.start(t); o.stop(t + 1.05);
  }

  /** Short synthesized UI tones (no asset files). */
  chime(kind: "wake" | "done" | "alert" | "click"): void {
    const t = this.ctx.currentTime;
    const tones: Record<string, [number, number, number][]> = {
      wake: [[880, 0, 0.09], [1320, 0.07, 0.14]],
      done: [[1320, 0, 0.08], [990, 0.06, 0.12]],
      alert: [[660, 0, 0.12], [660, 0.18, 0.12]],
      click: [[2200, 0, 0.03]],
    };
    for (const [f, off, dur] of tones[kind]) {
      const o = this.ctx.createOscillator();
      const g = this.ctx.createGain();
      o.type = "sine";
      o.frequency.value = f;
      g.gain.setValueAtTime(0, t + off);
      g.gain.linearRampToValueAtTime(0.07, t + off + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, t + off + dur);
      o.connect(g).connect(this.ctx.destination);
      o.start(t + off);
      o.stop(t + off + dur + 0.02);
    }
  }
}

export class Microphone {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  onChunk: (pcm: ArrayBuffer) => void = () => {};
  error: string | null = null;

  async start(): Promise<boolean> {
    if (this.ctx) return true;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      this.ctx = new AudioContext({ sampleRate: 16000 });
      await this.ctx.audioWorklet.addModule(new URL("mic-worklet.js", document.baseURI).href);
      const src = this.ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.ctx, "mic-capture");
      this.node.port.onmessage = (e) => this.onChunk(e.data as ArrayBuffer);
      src.connect(this.node);
      // Worklet must be pulled by the graph; route through a muted gain so nothing is audible.
      const mute = this.ctx.createGain();
      mute.gain.value = 0;
      this.node.connect(mute).connect(this.ctx.destination);
      this.error = null;
      return true;
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
      this.stop();
      return false;
    }
  }

  stop(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.node?.disconnect();
    this.ctx?.close().catch(() => {});
    this.ctx = null;
    this.stream = null;
    this.node = null;
  }
}
