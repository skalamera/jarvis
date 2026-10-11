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

  /** Cinematic power-on (~8 s, synthesized; IMPACT at 5 s, matching the HUD's PowerOn timeline):
   *  darkness and a heartbeat that quickens over a low drone, a rising riser with relay clicks as systems come
   *  online, a breath of silence, then the hit: sub drop + a deep brass "braaam" + a crack, all into a long reverb,
   *  and a shimmering "online" chime on top. */
  powerOn(): void {
    const c = this.ctx, t = c.currentTime + 0.05, IMPACT = 5.0;
    const out = c.createGain();
    out.gain.value = 0.85;
    const comp = c.createDynamicsCompressor();
    comp.threshold.value = -14; comp.ratio.value = 6; comp.attack.value = 0.003; comp.release.value = 0.25;
    out.connect(comp).connect(c.destination);
    // a long, dark reverb (generated impulse response) for the cinematic tail
    const verb = c.createConvolver();
    const irLen = Math.floor(c.sampleRate * 3.2);
    const ir = c.createBuffer(2, irLen, c.sampleRate);
    for (let chn = 0; chn < 2; chn++) {
      const d = ir.getChannelData(chn);
      for (let i = 0; i < irLen; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / irLen, 2.6);
    }
    verb.buffer = ir;
    const verbG = c.createGain(); verbG.gain.value = 0.55;
    verb.connect(verbG).connect(out);
    const send = (n: AudioNode, amt: number) => { const g = c.createGain(); g.gain.value = amt; n.connect(g).connect(verb); };
    const env = (g: GainNode, pts: [number, number][], from = 0) => {
      g.gain.setValueAtTime(0.0001, t + from);
      for (const [at, v] of pts) g.gain.exponentialRampToValueAtTime(Math.max(0.0001, v), t + at);
    };
    const noise = (secs: number) => {
      const len = Math.floor(c.sampleRate * secs), nb = c.createBuffer(1, len, c.sampleRate), ch = nb.getChannelData(0);
      for (let i = 0; i < len; i++) ch[i] = Math.random() * 2 - 1;
      const src = c.createBufferSource(); src.buffer = nb; return src;
    };

    // 1) low drone under the darkness, swelling toward the hit, then cut dead just before it
    const droneLp = c.createBiquadFilter(); droneLp.type = "lowpass"; droneLp.Q.value = 3;
    droneLp.frequency.setValueAtTime(110, t); droneLp.frequency.exponentialRampToValueAtTime(900, t + IMPACT - 0.15);
    const droneG = c.createGain();
    env(droneG, [[1.2, 0.07], [3.5, 0.14], [IMPACT - 0.2, 0.26], [IMPACT - 0.12, 0.0001]]);
    droneLp.connect(droneG).connect(out); send(droneG, 0.3);
    for (const [type, f] of [["sine", 41], ["sine", 41.6], ["sawtooth", 55], ["sawtooth", 82.4]] as [OscillatorType, number][]) {
      const o = c.createOscillator(); o.type = type; o.frequency.value = f;
      o.connect(droneLp); o.start(t); o.stop(t + IMPACT);
    }

    // 2) heartbeat: lub-dub thumps that quicken as the system wakes
    const beats = [0.45, 1.5, 2.45, 3.25, 3.85, 4.35, 4.7];
    beats.forEach((at, i) => {
      for (const [off, amp] of [[0, 0.75], [0.2 - Math.min(0.08, i * 0.012), 0.45]] as [number, number][]) {
        const o = c.createOscillator(), g = c.createGain();
        o.type = "sine";
        o.frequency.setValueAtTime(78, t + at + off);
        o.frequency.exponentialRampToValueAtTime(36, t + at + off + 0.16);
        g.gain.setValueAtTime(0.0001, t + at + off);
        g.gain.exponentialRampToValueAtTime(amp * (0.7 + i * 0.05), t + at + off + 0.012);
        g.gain.exponentialRampToValueAtTime(0.0001, t + at + off + 0.22);
        o.connect(g).connect(out); send(g, 0.15);
        o.start(t + at + off); o.stop(t + at + off + 0.25);
      }
    });

    // 3) riser: noise through a sweeping bandpass + rising detuned saws (2.0 s -> the hit)
    const rs = noise(3.2);
    const rbp = c.createBiquadFilter(); rbp.type = "bandpass"; rbp.Q.value = 2.2;
    rbp.frequency.setValueAtTime(260, t + 1.8); rbp.frequency.exponentialRampToValueAtTime(9000, t + IMPACT - 0.12);
    const rG = c.createGain();
    env(rG, [[2.6, 0.03], [IMPACT - 0.16, 0.24], [IMPACT - 0.1, 0.0001]], 1.8);
    rs.connect(rbp).connect(rG).connect(out); send(rG, 0.25);
    rs.start(t + 1.8); rs.stop(t + IMPACT);
    const sawLp = c.createBiquadFilter(); sawLp.type = "lowpass"; sawLp.Q.value = 8;
    sawLp.frequency.setValueAtTime(400, t + 2.0); sawLp.frequency.exponentialRampToValueAtTime(6500, t + IMPACT - 0.12);
    const sawG = c.createGain();
    env(sawG, [[2.8, 0.01], [IMPACT - 0.16, 0.07], [IMPACT - 0.1, 0.0001]], 2.0);
    sawLp.connect(sawG).connect(out);
    for (const det of [0, 9, -7]) {
      const o = c.createOscillator(); o.type = "sawtooth"; o.detune.value = det;
      o.frequency.setValueAtTime(110, t + 2.0); o.frequency.exponentialRampToValueAtTime(1320, t + IMPACT - 0.1);
      o.connect(sawLp); o.start(t + 2.0); o.stop(t + IMPACT);
    }

    // 4) relay clicks: systems coming online, accelerating into the hit
    let at = 2.2, gap = 0.3, i = 0;
    while (at < IMPACT - 0.2) {
      const o = c.createOscillator(), g = c.createGain();
      o.type = "square"; o.frequency.value = 1600 + (i % 6) * 210;
      g.gain.setValueAtTime(0.0001, t + at);
      g.gain.exponentialRampToValueAtTime(0.045, t + at + 0.004);
      g.gain.exponentialRampToValueAtTime(0.0001, t + at + 0.03);
      o.connect(g).connect(out); o.start(t + at); o.stop(t + at + 0.04);
      at += gap; gap = Math.max(0.05, gap * 0.86); i++;
    }

    // 5) THE HIT at IMPACT: sub drop, a deep brass "braaam" (stacked saws through an opening/closing lowpass), a crack
    const sub = c.createOscillator(), subG = c.createGain();
    sub.type = "sine";
    sub.frequency.setValueAtTime(130, t + IMPACT); sub.frequency.exponentialRampToValueAtTime(28, t + IMPACT + 1.6);
    subG.gain.setValueAtTime(0.0001, t + IMPACT);
    subG.gain.exponentialRampToValueAtTime(1.0, t + IMPACT + 0.015);
    subG.gain.exponentialRampToValueAtTime(0.0001, t + IMPACT + 2.4);
    sub.connect(subG).connect(out); sub.start(t + IMPACT); sub.stop(t + IMPACT + 2.5);
    const brLp = c.createBiquadFilter(); brLp.type = "lowpass"; brLp.Q.value = 4;
    brLp.frequency.setValueAtTime(180, t + IMPACT);
    brLp.frequency.exponentialRampToValueAtTime(1500, t + IMPACT + 0.35);
    brLp.frequency.exponentialRampToValueAtTime(380, t + IMPACT + 3.0);
    const brG = c.createGain();
    brG.gain.setValueAtTime(0.0001, t + IMPACT);
    brG.gain.exponentialRampToValueAtTime(0.32, t + IMPACT + 0.06);
    brG.gain.exponentialRampToValueAtTime(0.16, t + IMPACT + 1.2);
    brG.gain.exponentialRampToValueAtTime(0.0001, t + IMPACT + 3.3);
    brLp.connect(brG).connect(out); send(brG, 0.9);
    for (const [f, det] of [[55, 0], [55, 12], [82.4, -6], [110, 5], [110, -9]] as [number, number][]) {
      const o = c.createOscillator(); o.type = "sawtooth"; o.frequency.value = f; o.detune.value = det;
      o.connect(brLp); o.start(t + IMPACT); o.stop(t + IMPACT + 3.4);
    }
    const cr = noise(0.5), hp = c.createBiquadFilter(), crG = c.createGain();
    hp.type = "highpass"; hp.frequency.value = 1800;
    crG.gain.setValueAtTime(0.0001, t + IMPACT);
    crG.gain.exponentialRampToValueAtTime(0.35, t + IMPACT + 0.005);
    crG.gain.exponentialRampToValueAtTime(0.0001, t + IMPACT + 0.4);
    cr.connect(hp).connect(crG).connect(out); send(crG, 0.6);
    cr.start(t + IMPACT); cr.stop(t + IMPACT + 0.5);

    // 6) shimmer: bell-like "online" notes blooming out of the hit, then a soft confirmation
    for (const [f, off, amp] of [[1046.5, 0.55, 0.07], [1568, 0.7, 0.06], [2093, 0.85, 0.04], [2637, 1.0, 0.03],
                                 [880, 1.75, 0.06], [1318.5, 1.9, 0.06]] as [number, number, number][]) {
      const o = c.createOscillator(), g = c.createGain();
      o.type = "sine"; o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t + IMPACT + off);
      g.gain.exponentialRampToValueAtTime(amp, t + IMPACT + off + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0001, t + IMPACT + off + 1.6);
      o.connect(g).connect(out); send(g, 0.7);
      o.start(t + IMPACT + off); o.stop(t + IMPACT + off + 1.7);
    }
  }

  private amb: { stop: () => void } | null = null;

  /** A soft "working on it" bed while the morning briefing loads: a low, slowly breathing pad with faint data
   *  ticks. Fades in over ~1.5 s, fades out when he's about to be spoken to. */
  ambient(on: boolean): void {
    const c = this.ctx;
    if (!on) { this.amb?.stop(); this.amb = null; return; }
    if (this.amb) return;
    if (c.state === "suspended") void c.resume();
    const t = c.currentTime;
    const out = c.createGain();
    out.gain.setValueAtTime(0.0001, t);
    out.gain.exponentialRampToValueAtTime(0.5, t + 1.5);
    out.connect(c.destination);
    const lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 700; lp.Q.value = 2;
    const lfo = c.createOscillator(), lfoG = c.createGain();
    lfo.frequency.value = 0.12; lfoG.gain.value = 380;
    lfo.connect(lfoG).connect(lp.frequency);
    const padG = c.createGain(); padG.gain.value = 0.05;
    lp.connect(padG).connect(out);
    const oscs = [110, 110.6, 164.8, 220.3, 329.6].map((f, i) => {
      const o = c.createOscillator(); o.type = i < 2 ? "sine" : "triangle"; o.frequency.value = f;
      const g = c.createGain(); g.gain.value = i < 2 ? 1 : 0.35;
      o.connect(g).connect(lp); o.start(t); return o;
    });
    lfo.start(t);
    // faint, irregular data ticks
    let alive = true;
    const tick = () => {
      if (!alive) return;
      const n = c.currentTime, o = c.createOscillator(), g = c.createGain();
      o.type = "sine"; o.frequency.value = 2400 + Math.random() * 1600;
      g.gain.setValueAtTime(0.0001, n);
      g.gain.exponentialRampToValueAtTime(0.012, n + 0.004);
      g.gain.exponentialRampToValueAtTime(0.0001, n + 0.05);
      o.connect(g).connect(out); o.start(n); o.stop(n + 0.06);
      window.setTimeout(tick, 180 + Math.random() * 520);
    };
    window.setTimeout(tick, 600);
    this.amb = {
      stop: () => {
        alive = false;
        const n = c.currentTime;
        out.gain.cancelScheduledValues(n);
        out.gain.setValueAtTime(Math.max(0.0001, out.gain.value), n);
        out.gain.exponentialRampToValueAtTime(0.0001, n + 0.9);
        [...oscs, lfo].forEach((o) => o.stop(n + 1));
      },
    };
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
