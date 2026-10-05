import { useState } from "react";
import { core } from "../ws/core";
import type { Card } from "../types";

const MODE: Record<string, { embed: string; dir: string; label: string }> = {
  driving: { embed: "", dir: "driving", label: "DRIVE" },
  walking: { embed: "w", dir: "walking", label: "WALK" },
  transit: { embed: "r", dir: "transit", label: "TRANSIT" },
  bicycling: { embed: "b", dir: "bicycling", label: "BIKE" },
};

/** Build the keyless Google Maps embed URL (interactive: pan / zoom / route) and the full Maps URL. */
export function mapUrls(d: any, mode: string) {
  const enc = encodeURIComponent;
  const m = MODE[mode] ?? MODE.driving;
  if (d.destination) {
    const stops = [d.origin || "", ...(d.waypoints ?? []), d.destination];
    // saddr / daddr ("A to:B to:C") is the long-standing embeddable directions format
    const daddr = stops.slice(1).map(enc).join("+to:");
    const embed = `https://maps.google.com/maps?saddr=${enc(stops[0])}&daddr=${daddr}${m.embed ? `&dirflg=${m.embed}` : ""}&output=embed`;
    const open = `https://www.google.com/maps/dir/?api=1&origin=${enc(stops[0])}&destination=${enc(d.destination)}` +
      `${d.waypoints?.length ? `&waypoints=${enc(d.waypoints.join("|"))}` : ""}&travelmode=${m.dir}`;
    return { embed, open };
  }
  const q = d.query || d.address || [d.lat, d.lng].filter((v: any) => v != null).join(",");
  const embed = `https://maps.google.com/maps?q=${enc(q)}&z=${Number(d.zoom) || 14}&output=embed`;
  return { embed, open: `https://www.google.com/maps/search/?api=1&query=${enc(q)}` };
}

export function MapCard({ card }: { card: Card }) {
  const d0 = card.data ?? {};
  const [q, setQ] = useState(d0.query || "");
  const [shown, setShown] = useState<string | null>(null);
  const d = shown != null ? { ...d0, query: shown, address: undefined, lat: undefined, lng: undefined } : d0;
  const directions = !!d.destination;
  const [mode, setMode] = useState<string>(MODE[d.travel_mode] ? d.travel_mode : "driving");
  const [loaded, setLoaded] = useState(false);
  const { embed, open } = mapUrls(d, mode);
  return (
    <div className="map-card">
      {!d0.destination && (
        <form className="cs-bar" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); if (q.trim()) setShown(q.trim()); }}>
          <div className="cs-q"><span>⌕</span><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the map" /></div>
          <button type="submit" className="cs-go">Go</button>
        </form>
      )}
      {directions && (
        <div className="map-route">
          <div className="map-stop"><span className="map-pin a">A</span>{d.origin || "Current location"}</div>
          {(d.waypoints ?? []).map((w: string, i: number) => (
            <div key={i} className="map-stop"><span className="map-pin via">{i + 1}</span>{w}</div>
          ))}
          <div className="map-stop"><span className="map-pin b">B</span>{d.destination}</div>
        </div>
      )}
      {directions && (
        <div className="map-modes">
          {Object.entries(MODE).map(([k, v]) => (
            <button key={k} className={`tog ${mode === k ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); setLoaded(false); setMode(k); }}>{v.label}</button>
          ))}
        </div>
      )}
      <div className="map-frame">
        {!loaded && <div className="map-loading">ACQUIRING SATELLITE FEED…</div>}
        <iframe key={embed} src={embed} title={card.title} loading="lazy" referrerPolicy="no-referrer-when-downgrade"
          allowFullScreen onLoad={() => setLoaded(true)} />
      </div>
      <div className="card-actions">
        <button className="hbtn hbtn-cyan" onClick={() => core.openExternal(open)}>
          {directions ? "Open turn-by-turn ↗" : "Open in Google Maps ↗"}
        </button>
      </div>
    </div>
  );
}
