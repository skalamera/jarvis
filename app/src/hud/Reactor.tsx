import { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { EffectComposer, Bloom, ChromaticAberration, Vignette } from "@react-three/postprocessing";
import { BlendFunction } from "postprocessing";
import * as THREE from "three";
import { useStore } from "../state/store";
import { viz } from "../media/viz";
import type { HudState } from "../types";

const PALETTE: Record<HudState, [string, string]> = {
  idle: ["#39d0ff", "#0b6fa8"],
  listening: ["#7af7ff", "#14b8d4"],
  thinking: ["#5ab8ff", "#2b5cff"],
  speaking: ["#8fe9ff", "#23a6ff"],
  confirm: ["#ffb020", "#ff6a00"],
  offline: ["#ff4d5e", "#5a1020"],
  sleep: ["#14476b", "#061a2c"],
};

/** Shared animated values, smoothed each frame (avoids React re-renders at 60fps). */
function useDrive() {
  const d = useRef({ level: 0, energy: 0.3, spin: 0.2, color: new THREE.Color(PALETTE.idle[0]), color2: new THREE.Color(PALETTE.idle[1]),
    music: 0, beat: 0, hue: 0.55 });
  const tmpA = useMemo(() => new THREE.Color(), []), tmpB = useMemo(() => new THREE.Color(), []);
  useFrame((_, dt) => {
    const s = useStore.getState();
    viz.tick(dt);
    // Music visualizer mode: music playing and JARVIS otherwise at rest (voice states keep their own look).
    const musicOn = viz.active && !s.asleep && !s.powerOnAt && s.hud === "idle";
    d.current.music += ((musicOn ? 1 : 0) - d.current.music) * (1 - Math.exp(-dt * 3));
    d.current.beat = viz.beat * d.current.music;
    // power-on: 0-2.6 s charge (energy and spin climb), 2.7 s flare, then settle into the normal state
    const pt = s.powerOnAt ? (Date.now() - s.powerOnAt) / 1000 : -1;
    const powering = pt >= 0 && pt < 4.2;
    const flare = powering ? (pt < 2.7 ? (pt / 2.7) ** 2 * 0.9 : Math.max(0, 1.6 - (pt - 2.7) * 1.1)) : 0;
    const target = powering ? flare * 0.8 : s.hud === "speaking" ? s.outLevel : s.hud === "listening" ? s.micLevel * 1.6 : 0;
    const k = 1 - Math.exp(-dt * 14);
    d.current.level += (Math.min(1, target) - d.current.level) * k;
    const breath = 0.05 + 0.03 * Math.sin(Date.now() / 1400);  // asleep: a slow breathing glow
    const st = s.asleep && !powering ? "sleep" : s.hud;
    const e = powering ? 0.15 + flare * 0.85 : { idle: 0.28, listening: 0.75, thinking: 0.9, speaking: 0.8, confirm: 1, offline: 0.12, sleep: breath }[st];
    d.current.energy += (e - d.current.energy) * (1 - Math.exp(-dt * (powering ? 8 : s.asleep ? 1.2 : 3)));
    const sp = powering ? 0.05 + flare * 2.6 : { idle: 0.15, listening: 0.35, thinking: 1.4, speaking: 0.45, confirm: 0.25, offline: 0.03, sleep: 0.02 }[st];
    d.current.spin += (sp - d.current.spin) * (1 - Math.exp(-dt * (powering ? 5 : 2)));
    const [a, b] = PALETTE[st];
    d.current.color.lerp(new THREE.Color(a), 1 - Math.exp(-dt * 4));
    d.current.color2.lerp(new THREE.Color(b), 1 - Math.exp(-dt * 4));
    // Fabricating an image/video in the hologram: the orb runs hot, fast and white-cyan, pulsing.
    const fg = s.forge;
    if (fg && !fg.result && !fg.error) {
      const pulse = 0.45 + 0.3 * Math.sin(Date.now() / 160) + 0.15 * Math.sin(Date.now() / 47);
      d.current.level += (pulse - d.current.level) * (1 - Math.exp(-dt * 10));
      d.current.energy += (1.1 - d.current.energy) * (1 - Math.exp(-dt * 4));
      d.current.spin += (2.4 - d.current.spin) * (1 - Math.exp(-dt * 3));
      d.current.color.lerp(tmpA.set("#c9f6ff"), 1 - Math.exp(-dt * 3));
      d.current.color2.lerp(tmpB.set("#1e8cff"), 1 - Math.exp(-dt * 3));
    }
    const m = d.current.music;
    if (m > 0.001) {
      // Hue drifts continuously and jumps a little on each beat; mids/highs push saturation and brightness.
      d.current.hue = (d.current.hue + dt * (0.035 + viz.mid * 0.08) + viz.beat * dt * 0.9) % 1;
      tmpA.setHSL(d.current.hue, 0.85, 0.55 + viz.high * 0.2 + viz.beat * 0.12);
      tmpB.setHSL((d.current.hue + 0.18) % 1, 0.9, 0.32 + viz.bass * 0.15);
      d.current.color.lerp(tmpA, m);
      d.current.color2.lerp(tmpB, m);
      d.current.level += (Math.min(1, viz.bass * 0.85 + viz.beat * 0.35) - d.current.level) * m * (1 - Math.exp(-dt * 20));
      d.current.energy += (0.55 + viz.level * 0.6 - d.current.energy) * m * (1 - Math.exp(-dt * 8));
      d.current.spin += (0.35 + viz.mid * 1.2 + viz.beat * 1.6 - d.current.spin) * m * (1 - Math.exp(-dt * 6));
    }
  });
  return d;
}

type Drive = ReturnType<typeof useDrive>;

// --------------------------------------------------------------- core glow (shader sphere)
const coreVert = /* glsl */ `
  varying vec3 vN; varying vec3 vP;
  uniform float uTime; uniform float uLevel;
  // cheap 3D noise
  float h(vec3 p){ return fract(sin(dot(p, vec3(12.9898,78.233,37.719))) * 43758.5453); }
  float n(vec3 p){ vec3 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f);
    return mix(mix(mix(h(i),h(i+vec3(1,0,0)),f.x),mix(h(i+vec3(0,1,0)),h(i+vec3(1,1,0)),f.x),f.y),
               mix(mix(h(i+vec3(0,0,1)),h(i+vec3(1,0,1)),f.x),mix(h(i+vec3(0,1,1)),h(i+vec3(1,1,1)),f.x),f.y),f.z); }
  void main(){
    vN = normalize(normalMatrix * normal);
    float disp = (n(position*3.0 + uTime*0.9) - 0.5) * (0.06 + uLevel*0.32);
    vec3 p = position + normal * disp;
    vP = p;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p,1.0);
  }`;
const coreFrag = /* glsl */ `
  varying vec3 vN; varying vec3 vP;
  uniform vec3 uColor; uniform vec3 uColor2; uniform float uEnergy; uniform float uTime;
  void main(){
    float fres = pow(1.0 - abs(dot(vN, vec3(0,0,1))), 2.2);
    float bands = 0.5 + 0.5*sin(vP.y*28.0 - uTime*3.0);
    vec3 col = mix(uColor2, uColor, fres) * (0.55 + uEnergy*0.9) + uColor * bands * 0.12;
    float a = 0.35 + fres*0.65;
    gl_FragColor = vec4(col * (1.2 + fres*1.8), a);
  }`;

function Core({ d }: { d: Drive }) {
  const mat = useRef<THREE.ShaderMaterial>(null!);
  const mesh = useRef<THREE.Mesh>(null!);
  const uniforms = useMemo(
    () => ({ uTime: { value: 0 }, uLevel: { value: 0 }, uEnergy: { value: 0.3 }, uColor: { value: new THREE.Color() }, uColor2: { value: new THREE.Color() } }),
    [],
  );
  useFrame(({ clock }) => {
    const u = mat.current.uniforms;
    u.uTime.value = clock.elapsedTime;
    u.uLevel.value = d.current.level;
    u.uEnergy.value = d.current.energy;
    u.uColor.value.copy(d.current.color);
    u.uColor2.value.copy(d.current.color2);
    const s = 0.62 + d.current.level * 0.2 + d.current.beat * 0.07 + Math.sin(clock.elapsedTime * 1.6) * 0.012;
    mesh.current.scale.setScalar(s);
    mesh.current.rotation.y = clock.elapsedTime * 0.25;
  });
  return (
    <mesh ref={mesh}>
      <icosahedronGeometry args={[1, 24]} />
      <shaderMaterial ref={mat} uniforms={uniforms} vertexShader={coreVert} fragmentShader={coreFrag}
        transparent depthWrite={false} blending={THREE.AdditiveBlending} />
    </mesh>
  );
}

// --------------------------------------------------------------- rings
function Ring({ d, r, w, segs, gap, speed, tilt = 0, opacity = 0.8, dash = false }: {
  d: Drive; r: number; w: number; segs: number; gap: number; speed: number; tilt?: number; opacity?: number; dash?: boolean;
}) {
  const g = useRef<THREE.Group>(null!);
  const mats = useRef<THREE.MeshBasicMaterial[]>([]);
  const pieces = useMemo(() => {
    const arr: { start: number; len: number }[] = [];
    const step = (Math.PI * 2) / segs;
    for (let i = 0; i < segs; i++) {
      const len = dash ? step * (0.25 + ((i * 7) % 5) * 0.12) : step - gap;
      arr.push({ start: i * step, len: Math.max(0.01, len) });
    }
    return arr;
  }, [segs, gap, dash]);
  useFrame((_, dt) => {
    g.current.rotation.z += dt * speed * (0.4 + d.current.spin);
    for (const m of mats.current) {
      if (!m) continue;
      m.color.copy(d.current.color);
      m.opacity = opacity * (0.45 + d.current.energy * 0.7);
    }
  });
  return (
    <group ref={g} rotation={[tilt, 0, 0]}>
      {pieces.map((p, i) => (
        <mesh key={i}>
          <ringGeometry args={[r - w / 2, r + w / 2, 48, 1, p.start, p.len]} />
          <meshBasicMaterial ref={(m) => { if (m) mats.current[i] = m; }} transparent depthWrite={false}
            blending={THREE.AdditiveBlending} side={THREE.DoubleSide} />
        </mesh>
      ))}
    </group>
  );
}

// --------------------------------------------------------------- tick marks + voice spectrum
function Ticks({ d, r, count }: { d: Drive; r: number; count: number }) {
  const ref = useRef<THREE.InstancedMesh>(null!);
  const mat = useRef<THREE.MeshBasicMaterial>(null!);
  const tmp = useMemo(() => new THREE.Object3D(), []);
  const phase = useMemo(() => Array.from({ length: count }, (_, i) => Math.sin(i * 12.9898) * 43758.5453 % 1), [count]);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    const lvl = d.current.level;
    for (let i = 0; i < count; i++) {
      const a = (i / count) * Math.PI * 2;
      const wave = 0.5 + 0.5 * Math.sin(a * 6 + t * 5 + phase[i] * 6);
      let len = 0.03 + lvl * 0.34 * wave * (0.6 + Math.abs(phase[i])) + (i % 5 === 0 ? 0.03 : 0);
      const m = d.current.music;
      if (m > 0.001) {
        // Spectrum ring: bins mirrored left/right so bass sits at the top and bottom of the ring.
        const half = count / 2, j = i < half ? i : count - 1 - i;
        const bin = Math.min(viz.spectrum.length - 1, Math.floor((j / half) * viz.spectrum.length));
        const spec = 0.02 + Math.pow(viz.spectrum[bin], 1.4) * 0.75 + d.current.beat * 0.05;
        len = len * (1 - m) + spec * m;
      }
      tmp.position.set(Math.cos(a) * (r + len / 2), Math.sin(a) * (r + len / 2), 0);
      tmp.rotation.z = a;
      tmp.scale.set(len, i % 5 === 0 ? 0.012 : 0.006, 1);
      tmp.updateMatrix();
      ref.current.setMatrixAt(i, tmp.matrix);
    }
    ref.current.instanceMatrix.needsUpdate = true;
    mat.current.color.copy(d.current.color);
    mat.current.opacity = 0.5 + d.current.energy * 0.5;
  });
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, count]}>
      <planeGeometry args={[1, 1]} />
      <meshBasicMaterial ref={mat} transparent depthWrite={false} blending={THREE.AdditiveBlending} />
    </instancedMesh>
  );
}

// --------------------------------------------------------------- orbiting particles
function Particles({ d, n = 900 }: { d: Drive; n?: number }) {
  const pts = useRef<THREE.Points>(null!);
  const mat = useRef<THREE.PointsMaterial>(null!);
  const geo = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const r = 1.3 + Math.pow(Math.random(), 2) * 2.4;
      const a = Math.random() * Math.PI * 2;
      pos.set([Math.cos(a) * r, Math.sin(a) * r, (Math.random() - 0.5) * 0.6], i * 3);
    }
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return g;
  }, [n]);
  useFrame((_, dt) => {
    pts.current.rotation.z -= dt * 0.05 * (0.5 + d.current.spin);
    pts.current.scale.setScalar(1 + d.current.beat * 0.06);
    mat.current.color.copy(d.current.color);
    mat.current.opacity = 0.25 + d.current.energy * 0.45;
  });
  return (
    <points ref={pts} geometry={geo}>
      <pointsMaterial ref={mat} size={0.018} sizeAttenuation transparent depthWrite={false} blending={THREE.AdditiveBlending} />
    </points>
  );
}

// --------------------------------------------------------------- thinking scanner arc
function Scanner({ d }: { d: Drive }) {
  const g = useRef<THREE.Group>(null!);
  const m = useRef<THREE.MeshBasicMaterial>(null!);
  useFrame((_, dt) => {
    const thinking = useStore.getState().hud === "thinking";
    g.current.rotation.z -= dt * (thinking ? 4.2 : 0.6);
    m.current.color.copy(d.current.color);
    const target = thinking ? 0.95 : 0.12;
    m.current.opacity += (target - m.current.opacity) * (1 - Math.exp(-dt * 5));
  });
  return (
    <group ref={g}>
      <mesh>
        <ringGeometry args={[1.02, 1.1, 64, 1, 0, Math.PI * 0.35]} />
        <meshBasicMaterial ref={m} transparent opacity={0.1} depthWrite={false} blending={THREE.AdditiveBlending} side={THREE.DoubleSide} />
      </mesh>
    </group>
  );
}

function Scene() {
  const d = useDrive();
  const root = useRef<THREE.Group>(null!);
  useFrame(({ clock, pointer }) => {
    const t = clock.elapsedTime;
    root.current.rotation.x = -0.18 + pointer.y * 0.06 + Math.sin(t * 0.3) * 0.02;
    root.current.rotation.y = pointer.x * 0.1 + Math.sin(t * 0.21) * 0.03;
    root.current.scale.setScalar(1 + d.current.beat * 0.025);
  });
  return (
    <group ref={root}>
      <Particles d={d} />
      <Core d={d} />
      <Scanner d={d} />
      <Ring d={d} r={0.82} w={0.014} segs={1} gap={0} speed={0.2} opacity={0.9} />
      <Ring d={d} r={0.93} w={0.05} segs={10} gap={0.12} speed={-0.6} opacity={0.75} />
      <Ring d={d} r={1.2} w={0.012} segs={3} gap={0.4} speed={0.35} opacity={0.8} />
      <Ticks d={d} r={1.28} count={144} />
      <Ring d={d} r={1.75} w={0.008} segs={60} gap={0} speed={-0.12} opacity={0.5} dash />
      <Ring d={d} r={1.9} w={0.03} segs={4} gap={1.1} speed={0.18} opacity={0.45} />
      <Ring d={d} r={2.25} w={0.004} segs={1} gap={0} speed={0} opacity={0.35} />
      <Ring d={d} r={1.55} w={0.006} segs={2} gap={2.2} speed={-0.9} tilt={1.2} opacity={0.5} />
      <Ring d={d} r={1.62} w={0.006} segs={2} gap={2.6} speed={0.7} tilt={-1.05} opacity={0.4} />
    </group>
  );
}

export function Reactor() {
  return (
    <Canvas dpr={[1, 2]} camera={{ position: [0, 0, 5.4], fov: 50 }} gl={{ antialias: true, alpha: true, powerPreference: "high-performance" }}>
      <Scene />
      <EffectComposer multisampling={0}>
        <Bloom intensity={1.35} luminanceThreshold={0.05} luminanceSmoothing={0.35} mipmapBlur radius={0.72} />
        <ChromaticAberration blendFunction={BlendFunction.NORMAL} offset={new THREE.Vector2(0.0007, 0.0007)} radialModulation={false} modulationOffset={0} />
        <Vignette eskil={false} offset={0.2} darkness={0.75} />
      </EffectComposer>
    </Canvas>
  );
}
