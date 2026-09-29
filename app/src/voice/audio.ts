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
