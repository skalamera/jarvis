/** Music visualizer input. While the music card plays, capture this window's own audio (Electron answers
 *  getDisplayMedia with our webContents, playback stays audible) and analyse it: bass / mid / high bands, a
 *  64-bin log spectrum, and a beat pulse. If capture is unavailable it falls back to a simulated groove, and
 *  `live` says which one is running. The Reactor reads `viz` every frame; nothing here re-renders React. */
import { media } from "./bus";

const BINS = 64;

class Viz {
  active = false;           // music is playing -> orb is in visualizer mode
  live = false;             // true = real audio analysis, false = simulated
  bass = 0; mid = 0; high = 0; level = 0; beat = 0;
  spectrum = new Float32Array(BINS);
  private ctx: AudioContext | null = null;
  private an: AnalyserNode | null = null;
  private stream: MediaStream | null = null;
  private buf: Uint8Array<ArrayBuffer> = new Uint8Array(new ArrayBuffer(1024));
  private avg = 0;
  private prevLow = new Float32Array(14);
  private lastBeat = 0;
  private starting = false;
  private stopTimer = 0;
  private failedAt = 0;

  constructor() {
    media.subscribeNowPlaying(() => this.sync());
  }

  private sync() {
    const on = !!media.nowPlaying?.playing;
    if (on) {
      window.clearTimeout(this.stopTimer);
      this.active = true;
      if (!this.stream && !this.starting && Date.now() - this.failedAt > 60_000) void this.start();
    } else if (this.active) {
      this.active = false;
      // Keep the capture briefly across track changes / quick pauses, then release it.
      window.clearTimeout(this.stopTimer);
      this.stopTimer = window.setTimeout(() => this.stop(), 8000);
    }
  }

  private async start() {
    this.starting = true;
    try {
      const s = await navigator.mediaDevices.getDisplayMedia({ audio: true, video: true });
      s.getVideoTracks().forEach((t) => t.stop()); // audio only
      if (!s.getAudioTracks().length) throw new Error("no audio track");
      const ctx = new AudioContext();
      const an = ctx.createAnalyser();
      an.fftSize = 2048;
      an.smoothingTimeConstant = 0.72;
      ctx.createMediaStreamSource(s).connect(an); // analyser only: not routed to the speakers (no doubled sound)
      this.stream = s; this.ctx = ctx; this.an = an;
      this.buf = new Uint8Array(new ArrayBuffer(an.frequencyBinCount));
      this.live = true;
      if (!this.active) this.stopTimer = window.setTimeout(() => this.stop(), 8000);
    } catch (e) {
      console.warn("[viz] audio capture unavailable, simulating", e);
      this.failedAt = Date.now();
      this.live = false;
    } finally {
      this.starting = false;
    }
  }

  private stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
    this.stream = null; this.ctx = null; this.an = null; this.live = false;
  }

  /** Advance one frame (called from the Reactor's render loop). */
  tick(dt: number) {
    const now = performance.now();
    let bass = 0, mid = 0, high = 0;
    if (this.an && this.ctx) {
      this.an.getByteFrequencyData(this.buf);
      const hz = this.ctx.sampleRate / 2 / this.buf.length;
      const band = (lo: number, hi: number) => {
        const a = Math.max(1, Math.floor(lo / hz)), b = Math.min(this.buf.length - 1, Math.ceil(hi / hz));
        let s = 0;
        for (let i = a; i <= b; i++) s += this.buf[i];
        return s / ((b - a + 1) * 255);
      };
      bass = band(30, 160); mid = band(160, 2000); high = band(2000, 12000);
      for (let i = 0; i < BINS; i++) { // log-spaced 40 Hz .. 14 kHz
        const f0 = 40 * Math.pow(350, i / BINS), f1 = 40 * Math.pow(350, (i + 1) / BINS);
        const v = band(f0, f1);
        this.spectrum[i] += (v - this.spectrum[i]) * 0.5;
      }
    } else {
      // Simulated groove (~120 BPM) so the orb still dances when capture isn't possible.
      const t = now / 1000, ph = (t * 2) % 1;
      const kick = Math.exp(-ph * 7);
      bass = 0.25 + 0.6 * kick; mid = 0.35 + 0.15 * Math.sin(t * 3.1) + 0.1 * Math.sin(t * 7.3); high = 0.25 + 0.15 * Math.sin(t * 11.7);
      for (let i = 0; i < BINS; i++) {
        const tilt = 1 - i / BINS;
        const v = (i < 10 ? bass : i < 40 ? mid : high) * (0.55 + 0.45 * Math.sin(t * (3 + i * 0.37) + i)) * (0.5 + tilt * 0.6);
        this.spectrum[i] += (Math.max(0, v) - this.spectrum[i]) * 0.35;
      }
    }
    const k = 1 - Math.exp(-dt * 18);
    this.bass += (bass - this.bass) * k; this.mid += (mid - this.mid) * k; this.high += (high - this.high) * k;
    this.level = Math.min(1, this.bass * 0.6 + this.mid * 0.5 + this.high * 0.3);
    // Beat: onset detection on the low end. Spectral flux (rise in the bass bins since last frame) spiking well
    // above its running average, with a refractory gap. Works for loud, bass-heavy mixes where the raw level
    // never drops enough for a level threshold to trip.
    let flux = 0;
    for (let i = 0; i < 14; i++) { const v = this.spectrum[i]; flux += Math.max(0, v - this.prevLow[i]); this.prevLow[i] = v; }
    this.avg += (flux - this.avg) * (1 - Math.exp(-dt * 3));
    if (flux > this.avg * 1.8 + 0.06 && now - this.lastBeat > 230) { this.beat = 1; this.lastBeat = now; }
    this.beat *= Math.exp(-dt * 6);
  }
}

export const viz = new Viz();
(window as any).__jarvisViz = viz; // for CDP checks
