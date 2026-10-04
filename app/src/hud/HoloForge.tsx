/** HoloForge: a generated image / video is fabricated "out of thin air" by the orb, Iron-Man style.
 *
 *  forming   : particle streams spiral out of the orb's rim into a tilted holographic projection frame, which
 *              fills with an interference-banded light cloud; corner brackets draw in, a prompt readout types
 *              out, and a progress ring counts up.
 *  assemble  : when the file lands, every particle is re-targeted to a pixel of the real result (sampled from
 *              the image, or the video's first frame) and flies there in a top-down wave, colour-shifting from
 *              cyan to the pixel's colour, so the picture condenses out of the cloud.
 *  shown     : the real media cross-fades in behind a scan-line sweep; ACCEPT / DISMISS appear.
 *  accept    : the hologram flies into the side panel and becomes the normal display card.
 *  dismiss   : it shatters back into particles that implode into the orb; the file is discarded (click-only,
 *              generated-and-unedited files only, moved to a trash dir by Core).
 *
 *  One rAF loop on a 2D canvas (additive "lighter" blending); no React re-renders per frame. */
import { useEffect, useMemo, useRef, useState } from "react";
import { useStore, type Forge } from "../state/store";
import { core } from "../ws/core";

type Phase = "forming" | "assemble" | "shown" | "dismiss" | "accept" | "error";
const N_FORM = 2600;
const ASSEMBLE_MS = 1900;

const easeIO = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function aspectOf(f: Forge): number {
  const a = f.result?.data?.artifact;
  if (a?.width && a?.height) return a.width / a.height;
  if (f.kind === "video") return f.aspect === "9:16" ? 9 / 16 : 16 / 9;
  return 1;
}

export function HoloForge() {
  const forge = useStore((s) => s.forge);
  if (!forge) return null;
  return <ForgeStage key={forge.key} />;
}

function ForgeStage() {
  const forge = useStore((s) => s.forge)!;
  const wrap = useRef<HTMLDivElement>(null);
  const cv = useRef<HTMLCanvasElement>(null);
  const [phase, setPhase] = useState<Phase>(forge.error ? "error" : "forming");
  const phaseRef = useRef<Phase>(phase);
  phaseRef.current = phase;
  const [aspect, setAspect] = useState(() => aspectOf(forge));
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [now, setNow] = useState(Date.now());
  const a = forge.result?.data?.artifact;
  const src = a ? core.artifactUrl(a.id, a.version) : "";
  const isVideo = forge.kind === "video" || a?.kind === "video";

  // Frame geometry (CSS px, relative to the stage), recomputed on resize / aspect change.
  const frame = useMemo(() => {
    const { w, h } = box;
    if (!w || !h) return { x: 0, y: 0, w: 0, h: 0 };
    let fh = h * 0.8, fw = fh * aspect;
    if (fw > w * 0.84) { fw = w * 0.84; fh = fw / aspect; }
    return { x: (w - fw) / 2, y: h * 0.47 - fh / 2, w: fw, h: fh };
  }, [box, aspect]);
  const frameRef = useRef(frame);
  frameRef.current = frame;

  useEffect(() => {
    const el = wrap.current!;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => { if (forge.error) setPhase("error"); }, [forge.error]);
  useEffect(() => {
    if (phase !== "forming") return;
    const t = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(t);
  }, [phase]);

  // ---------------------------------------------------------------- particle engine
  const P = useRef({
    n: 0, x: new Float32Array(0), y: new Float32Array(0), sx: new Float32Array(0), sy: new Float32Array(0),
    tx: new Float32Array(0), ty: new Float32Array(0), r: new Float32Array(0), g: new Float32Array(0), b: new Float32Array(0),
    life: new Float32Array(0), seed: new Float32Array(0), delay: new Float32Array(0), size: 1.2,
    t0: 0, mode: "form" as "form" | "assemble" | "fade" | "implode",
  });

  const alloc = (n: number) => {
    const p = P.current;
    const keep = p.n;
    const grow = (src: Float32Array) => { const d = new Float32Array(n); d.set(src.subarray(0, Math.min(keep, n))); return d; };
    p.x = grow(p.x); p.y = grow(p.y); p.sx = grow(p.sx); p.sy = grow(p.sy); p.tx = grow(p.tx); p.ty = grow(p.ty);
    p.r = grow(p.r); p.g = grow(p.g); p.b = grow(p.b); p.life = grow(p.life); p.seed = grow(p.seed); p.delay = grow(p.delay);
    p.n = n;
    return keep;
  };

  const spawnAtOrb = (i: number, w: number, h: number) => {
    const p = P.current;
    const ang = Math.random() * Math.PI * 2, rr = Math.min(w, h) * (0.1 + Math.random() * 0.04);
    p.x[i] = w / 2 + Math.cos(ang) * rr; p.y[i] = h * 0.5 + Math.sin(ang) * rr;
    p.sx[i] = p.x[i]; p.sy[i] = p.y[i];
    const f = frameRef.current;
    p.tx[i] = f.x + Math.random() * f.w; p.ty[i] = f.y + Math.random() * f.h;
    p.life[i] = 0; p.seed[i] = Math.random();
    p.r[i] = 120; p.g[i] = 225; p.b[i] = 255;
  };

  useEffect(() => {
    const c = cv.current!;
    const ctx = c.getContext("2d")!;
    let raf = 0, last = performance.now();
    const p = P.current;
    alloc(N_FORM);
    for (let i = 0; i < p.n; i++) { spawnAtOrb(i, c.clientWidth || 600, c.clientHeight || 400); p.life[i] = Math.random(); }
    const loop = (t: number) => {
      raf = requestAnimationFrame(loop);
      const dt = Math.min(0.05, (t - last) / 1000); last = t;
      const dpr = window.devicePixelRatio || 1;
      const W = c.clientWidth, H = c.clientHeight;
      if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, W, H);
      ctx.globalCompositeOperation = "lighter";
      const f = frameRef.current;
      const time = t / 1000;
      const ox = W / 2, oy = H * 0.5;

      if (p.mode === "form") {
        // Energy streams from the orb up into the frame + an interference-banded cloud inside it.
        for (let i = 0; i < p.n; i++) {
          p.life[i] += dt * (0.55 + p.seed[i] * 0.5);
          if (p.life[i] >= 1.6) { spawnAtOrb(i, W, H); continue; }
          const k = Math.min(1, p.life[i]);
          const e = easeIO(k);
          // curl: arc sideways on the way up
          const curl = Math.sin((p.seed[i] - 0.5) * 6 + time) * (1 - e) * 60;
          let x = p.sx[i] + (p.tx[i] - p.sx[i]) * e + curl;
          let y = p.sy[i] + (p.ty[i] - p.sy[i]) * e;
          if (k >= 1) { // arrived: shimmer in place
            x += Math.sin(time * 3 + p.seed[i] * 40) * 1.6; y += Math.cos(time * 2.4 + p.seed[i] * 30) * 1.6;
          }
          p.x[i] = x; p.y[i] = y;
          const band = 0.5 + 0.5 * Math.sin(y * 0.09 - time * 5);
          const fade = k < 1 ? 0.35 + k * 0.4 : Math.max(0, 1 - (p.life[i] - 1) / 0.6);
          ctx.fillStyle = `rgba(${90 + band * 120 | 0},${200 + band * 55 | 0},255,${(0.25 + band * 0.55) * fade})`;
          const s = k < 1 ? 1.1 : 1.5 + band;
          ctx.fillRect(x - s / 2, y - s / 2, s, s);
        }
      } else if (p.mode === "assemble" || p.mode === "fade") {
        const el = (t - p.t0) / ASSEMBLE_MS;
        const fadeK = p.mode === "fade" ? Math.max(0, 1 - (t - p.t0) / 700) : 1;
        if (p.mode === "fade" && fadeK <= 0) { p.n = 0; }
        for (let i = 0; i < p.n; i++) {
          const k = Math.max(0, Math.min(1, (p.mode === "fade" ? 1 : el) * 1.35 - p.delay[i]));
          const e = easeIO(k);
          const swirl = (1 - e) * Math.sin(p.seed[i] * 12 + time * 4) * 40;
          const x = p.sx[i] + (p.tx[i] - p.sx[i]) * e + swirl;
          const y = p.sy[i] + (p.ty[i] - p.sy[i]) * e;
          // colour: cyan energy -> the pixel's real colour as it lands
          const r = 110 + (p.r[i] - 110) * e, g = 225 + (p.g[i] - 225) * e, b = 255 + (p.b[i] - 255) * e;
          ctx.fillStyle = `rgba(${r | 0},${g | 0},${b | 0},${(0.45 + e * 0.55) * fadeK})`;
          const s = p.size * (0.6 + e * 0.8);
          ctx.fillRect(x - s / 2, y - s / 2, s, s);
        }
        if (p.mode === "assemble" && el >= 1.02 && phaseRef.current === "assemble") { setPhase("shown"); p.mode = "fade"; p.t0 = t; }
      } else if (p.mode === "implode") {
        const k = Math.min(1, (t - p.t0) / 1100);
        for (let i = 0; i < p.n; i++) {
          // burst outward briefly, then get sucked into the orb
          const burst = Math.sin(Math.min(1, k * 2.2) * Math.PI) * 70 * (p.seed[i] + 0.3);
          const ang = p.seed[i] * Math.PI * 2 + k * 6;
          const e = easeIO(Math.max(0, k * 1.3 - p.delay[i] * 0.3));
          const x = p.sx[i] + (ox - p.sx[i]) * e + Math.cos(ang) * burst * (1 - e);
          const y = p.sy[i] + (oy - p.sy[i]) * e + Math.sin(ang) * burst * (1 - e);
          ctx.fillStyle = `rgba(${p.r[i] | 0},${p.g[i] | 0},${p.b[i] | 0},${(1 - k) * 0.9})`;
          ctx.fillRect(x - 1, y - 1, 2, 2);
        }
        if (k >= 1) p.n = 0;
      }
      // Beam: a soft cone of light from the orb to the frame's lower edge while forming / assembling.
      if ((p.mode === "form" || p.mode === "assemble") && f.w) {
        const grad = ctx.createLinearGradient(0, oy, 0, f.y + f.h);
        grad.addColorStop(0, "rgba(57,208,255,0.16)"); grad.addColorStop(1, "rgba(57,208,255,0.02)");
        ctx.fillStyle = grad;
        ctx.beginPath(); ctx.moveTo(ox - 18, oy); ctx.lineTo(ox + 18, oy);
        ctx.lineTo(f.x + f.w, f.y + f.h * 0.98); ctx.lineTo(f.x, f.y + f.h * 0.98); ctx.closePath(); ctx.fill();
      }
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  // ---------------------------------------------------------------- result arrived: sample and assemble
  useEffect(() => {
    if (!src || phaseRef.current !== "forming") return;
    let dead = false;
    const go = (draw: CanvasImageSource | null, w: number, h: number) => {
      if (dead) return;
      const ar = w && h ? w / h : aspect;
      setAspect(ar);
      // wait one frame so the frame box has the final aspect, then target its pixels
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (dead) return;
        const f = frameRef.current, p = P.current;
        let cols = Math.round(Math.sqrt(5200 * ar)), rows = Math.round(5200 / cols);
        cols = Math.max(20, cols); rows = Math.max(20, rows);
        let px: Uint8ClampedArray | null = null;
        if (draw) {
          try {
            const oc = document.createElement("canvas"); oc.width = cols; oc.height = rows;
            const octx = oc.getContext("2d")!;
            octx.drawImage(draw, 0, 0, cols, rows);
            px = octx.getImageData(0, 0, cols, rows).data;
          } catch { px = null; } // tainted: fly in as cyan light instead
        }
        const n = cols * rows;
        const prev = p.n;
        alloc(n);
        for (let i = 0; i < n; i++) {
          const cx = i % cols, cy = (i / cols) | 0;
          if (i >= prev) { const ang = Math.random() * 6.283; p.x[i] = f.x + f.w / 2 + Math.cos(ang) * 40; p.y[i] = f.y + f.h + 30; p.seed[i] = Math.random(); }
          p.sx[i] = p.x[i]; p.sy[i] = p.y[i];
          p.tx[i] = f.x + (cx + 0.5) * (f.w / cols); p.ty[i] = f.y + (cy + 0.5) * (f.h / rows);
          if (px) { p.r[i] = px[i * 4]; p.g[i] = px[i * 4 + 1]; p.b[i] = px[i * 4 + 2]; }
          else { p.r[i] = 140; p.g[i] = 230; p.b[i] = 255; }
          p.delay[i] = (cy / rows) * 0.3 + Math.random() * 0.05; // top-down wave
        }
        p.size = Math.max(1.5, (f.w / cols) * 1.05);
        p.mode = "assemble"; p.t0 = performance.now();
        setPhase("assemble");
      }));
    };
    if (isVideo) {
      const v = document.createElement("video");
      v.crossOrigin = "anonymous"; v.muted = true; v.preload = "auto"; v.src = src;
      v.onloadeddata = () => { v.currentTime = Math.min(0.5, (v.duration || 1) / 4); };
      v.onseeked = () => go(v, v.videoWidth, v.videoHeight);
      v.onerror = () => go(null, 16, 9);
    } else {
      const im = new Image();
      im.crossOrigin = "anonymous";
      im.onload = () => go(im, im.naturalWidth, im.naturalHeight);
      im.onerror = () => { const j = new Image(); j.onload = () => go(null, j.naturalWidth, j.naturalHeight); j.src = src; };
      im.src = src;
    }
    return () => { dead = true; };
  }, [src]);

  // ---------------------------------------------------------------- actions
  const close = () => useStore.getState().set({ forge: null });
  const accept = () => {
    if (!forge.result) return;
    setPhase("accept");
    const card = forge.result;
    window.setTimeout(() => {
      useStore.getState().addCard(card);
      close();
    }, 720);
  };
  const dismiss = () => {
    const p = P.current;
    // shatter the picture into the same particle grid, then implode into the orb
    if (p.n === 0) {
      const f = frameRef.current; const cols = 70, rows = Math.max(20, Math.round(70 / aspect));
      alloc(cols * rows);
      for (let i = 0; i < p.n; i++) { p.x[i] = f.x + ((i % cols) + 0.5) * f.w / cols; p.y[i] = f.y + (((i / cols) | 0) + 0.5) * f.h / rows; p.seed[i] = Math.random(); p.delay[i] = Math.random(); if (!p.r[i]) { p.r[i] = 140; p.g[i] = 230; p.b[i] = 255; } }
    } else {
      for (let i = 0; i < p.n; i++) { p.x[i] = p.tx[i]; p.y[i] = p.ty[i]; p.delay[i] = Math.random(); }
    }
    for (let i = 0; i < p.n; i++) { p.sx[i] = p.x[i]; p.sy[i] = p.y[i]; }
    p.mode = "implode"; p.t0 = performance.now();
    setPhase("dismiss");
    if (a?.id) {
      core.workspace("artifact_discard", { artifact_id: a.id }).then((r) => {
        if (!r.ok) useStore.getState().toast({ text: r.error || "Couldn't discard it.", error: true });
      });
    }
    window.setTimeout(close, 1150);
  };

  // Voice: Core sends "keep" / "lose" while we report the hologram as waiting for an answer.
  const waiting = phase === "shown" || phase === "error";
  useEffect(() => {
    core.send({ type: "forge_state", pending: waiting });
    return () => { if (waiting) core.send({ type: "forge_state", pending: false }); };
  }, [waiting]);
  const act = useRef({ accept, dismiss, phase });
  act.current = { accept, dismiss, phase };
  useEffect(() => {
    const on = (e: Event) => {
      const { accept: ac, dismiss: di, phase: ph } = act.current;
      const v = (e as CustomEvent).detail;
      if (v === "keep" && ph === "shown") ac();
      else if (v === "lose" && (ph === "shown" || ph === "error")) di();
    };
    window.addEventListener("jarvis:forge", on);
    return () => window.removeEventListener("jarvis:forge", on);
  }, []);

  const elapsed = Math.max(0, now - forge.started);
  const pct = Math.min(96, Math.round((elapsed / forge.eta) * 90));
  const typed = forge.prompt.slice(0, Math.floor(elapsed / 28));
  const R = 22, C = 2 * Math.PI * R;
  const showMedia = phase === "shown" || phase === "accept" || phase === "dismiss";

  return (
    <div className={`forge ph-${phase}`} ref={wrap}>
      <canvas ref={cv} className="forge-cv" />
      {frame.w > 0 && (
        <div className="forge-frame" style={{ left: frame.x, top: frame.y, width: frame.w, height: frame.h }}>
          <div className="forge-tilt">
            <i className="fc tl" /><i className="fc tr" /><i className="fc bl" /><i className="fc br" />
            <div className="forge-grid" />
            {showMedia && src && (isVideo
              ? <video className="forge-media" src={src} autoPlay muted loop playsInline />
              : <img className="forge-media" src={src} alt="" draggable={false} />)}
            <div className="forge-scan" />
            <div className="forge-lines" />
            {phase === "error" && <div className="forge-err"><b>FABRICATION FAILED</b><span>{forge.error}</span></div>}
          </div>
          <div className="forge-hud top">
            <span className="fh-k">{phase === "forming" ? "FABRICATING" : phase === "assemble" ? "MATERIALIZING" : phase === "error" ? "FAULT" : "HOLOGRAM"}</span>
            <span className="fh-v">{forge.kind === "video" ? "VIDEO" : "IMAGE"}{phase === "forming" ? ` · T+${(elapsed / 1000).toFixed(0)}s` : ""}</span>
          </div>
          {phase === "forming" && (
            <>
              <svg className="forge-ring" viewBox="0 0 56 56">
                <circle cx="28" cy="28" r={R} className="bg" />
                <circle cx="28" cy="28" r={R} className="fg" strokeDasharray={C} strokeDashoffset={C * (1 - pct / 100)} />
                <text x="28" y="32">{pct}%</text>
              </svg>
              <div className="forge-hud bottom"><span className="fh-p">“{typed}<i className="caret" />”</span></div>
            </>
          )}
          {(phase === "shown" || phase === "error") && (
            <div className="forge-actions">
              {phase === "shown" && <button className="fa accept" onClick={accept}>✓ ACCEPT</button>}
              <button className="fa dismiss" onClick={dismiss}>✕ DISMISS</button>
              <span className="fa-hint">or say “{phase === "shown" ? "keep it” / “lose it" : "lose it"}”</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
