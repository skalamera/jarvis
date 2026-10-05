import { useEffect, useState } from "react";
import { radarUrl } from "../cards/WeatherCard";

/** HoloRadar: when he asks about the weather, the orb "projects" a live radar sweep over itself (like HoloForge),
    then it docks away; the full weather display still lands in the sidebar. */
export function HoloRadar() {
  const [r, setR] = useState<{ lat: number; lon: number; name: string; temp?: number; summary?: string; at: number } | null>(null);
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    const on = (e: Event) => {
      const d: any = (e as CustomEvent).detail || {};
      setLeaving(false);
      setR({ lat: d.location.lat, lon: d.location.lon, name: d.location.name || "", temp: d.current?.temp, summary: d.current?.summary || d.current?.condition, at: Date.now() });
    };
    window.addEventListener("jarvis:radar", on);
    return () => window.removeEventListener("jarvis:radar", on);
  }, []);
  useEffect(() => {
    if (!r) return;
    const t1 = setTimeout(() => setLeaving(true), 24_000);
    const t2 = setTimeout(() => setR(null), 25_200);
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setLeaving(true); };
    window.addEventListener("keydown", esc);
    return () => { clearTimeout(t1); clearTimeout(t2); window.removeEventListener("keydown", esc); };
  }, [r]);
  useEffect(() => { if (leaving) { const t = setTimeout(() => setR(null), 1100); return () => clearTimeout(t); } }, [leaving]);
  if (!r) return null;
  return (
    <div className={`hr ${leaving ? "out" : ""}`} key={r.at}>
      <div className="hr-ring" /><div className="hr-ring b" />
      <div className="hr-disc">
        <iframe src={radarUrl(r.lat, r.lon, 7)} title="Radar" />
        <div className="hr-sweep" />
        <div className="hr-scan" />
      </div>
      <div className="hr-tag">
        <b>LIVE RADAR</b> · {r.name}{r.temp != null ? ` · ${Math.round(r.temp)}°` : ""}
        <button onClick={() => setLeaving(true)} title="Dismiss">×</button>
      </div>
    </div>
  );
}
