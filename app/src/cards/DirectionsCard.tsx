import { useEffect, useState } from "react";
import { core } from "../ws/core";
import type { Card } from "../types";

/* Turn-by-turn directions card (Google Routes): ETA with live traffic, alternatives, mode switch, route map,
   numbered steps with maneuver arrows, transit legs with line colors. Mode / avoid toggles recompute in place. */

const MODES: [string, string, string][] = [
  ["driving", "DRIVE", "🚗"], ["transit", "TRANSIT", "🚆"], ["walking", "WALK", "🚶"], ["bicycling", "BIKE", "🚲"],
];
// Always send an explicit mode: with none, Google picks its own (walking for short trips) and the map
// disagrees with the selected Drive tab.
const EMBED_FLAG: Record<string, string> = { driving: "d", walking: "w", transit: "r", bicycling: "b" };
const DIR_MODE: Record<string, string> = { driving: "driving", walking: "walking", transit: "transit", bicycling: "bicycling" };

const ARROW: Record<string, string> = {
  TURN_LEFT: "↰", TURN_RIGHT: "↱", TURN_SLIGHT_LEFT: "↖", TURN_SLIGHT_RIGHT: "↗", TURN_SHARP_LEFT: "↲", TURN_SHARP_RIGHT: "↳",
  UTURN_LEFT: "↶", UTURN_RIGHT: "↷", STRAIGHT: "↑", NAME_CHANGE: "↑", DEPART: "●", MERGE: "⤴", RAMP_LEFT: "↖", RAMP_RIGHT: "↗",
  FORK_LEFT: "↖", FORK_RIGHT: "↗", ROUNDABOUT_LEFT: "⟲", ROUNDABOUT_RIGHT: "⟳", FERRY: "⛴", FERRY_TRAIN: "⛴",
};

function arrive(sec: number) {
  const d = new Date(Date.now() + sec * 1000);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function DirectionsCard({ card }: { card: Card }) {
  const [d, setD] = useState<any>(card.data || {});
  const [sel, setSel] = useState(0);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [showAll, setShowAll] = useState(false);
  useEffect(() => { setD(card.data || {}); setSel(0); }, [card.data]);

  const recompute = async (patch: Record<string, unknown>) => {
    const args = { destination: d.destination, origin: d.origin_is_here ? "" : d.origin, mode: d.mode,
      avoid_tolls: !!d.avoid_tolls, avoid_highways: !!d.avoid_highways, ...patch };
    setBusy(String(patch.mode || "opts")); setErr("");
    const r = await core.rpc("directions_mode", args);
    setBusy("");
    if (r.ok) { setD(r.result); setSel(0); } else setErr(r.error || "No route.");
  };

  const routes: any[] = d.routes || [];
  const rt = routes[sel] || routes[0];
  const origin = d.origin_is_here ? d.origin_latlng : d.origin;
  const enc = encodeURIComponent;
  const embed = `https://maps.google.com/maps?saddr=${enc(origin || "")}&daddr=${enc(d.destination || "")}` +
    `${EMBED_FLAG[d.mode] ? `&dirflg=${EMBED_FLAG[d.mode]}` : ""}${d.mode === "driving" ? "&layer=t" : ""}&output=embed`;
  const openUrl = `https://www.google.com/maps/dir/?api=1&origin=${enc(origin || "")}&destination=${enc(d.destination || "")}&travelmode=${DIR_MODE[d.mode] || "driving"}`;
  const steps: any[] = rt?.steps || [];
  const shown = showAll || steps.length <= 10 ? steps : steps.slice(0, 8);

  return (
    <div className="dir-card">
      <div className="dir-ends">
        <div className="map-stop"><span className="map-pin a">A</span>{d.origin_is_here ? `Current location (${d.origin})` : d.origin}</div>
        <div className="map-stop"><span className="map-pin b">B</span>{d.destination}</div>
      </div>

      <div className="dir-modes">
        {MODES.map(([k, label, ico]) => (
          <button key={k} className={`tog ${d.mode === k ? "on" : ""}`} disabled={!!busy} onClick={() => d.mode !== k && recompute({ mode: k })}>
            {busy === k ? "…" : ico} {label}
          </button>
        ))}
      </div>

      {err && <div className="py-warn">{err}</div>}
      {rt && (
        <div className="dir-eta">
          <div className="dir-eta-main">
            <span className={`dir-time tr-${rt.traffic || "none"}`}>{rt.duration}</span>
            <span className="dir-dist">{rt.distance}</span>
          </div>
          <div className="dir-eta-sub">
            Arrive ~{arrive(rt.duration_s)}
            {rt.summary && <> · via {rt.summary}</>}
            {rt.delay_min > 0 && <> · <b className={`tr-${rt.traffic}`}>+{rt.delay_min} min traffic</b></>}
            {d.mode === "driving" && rt.delay_min === 0 && rt.traffic && <> · <span className="tr-light">No traffic delays</span></>}
            {rt.tolls && <> · Tolls</>}
          </div>
        </div>
      )}

      {routes.length > 1 && (
        <div className="dir-alts">
          {routes.map((r, i) => (
            <button key={i} className={`dir-alt ${i === sel ? "on" : ""}`} onClick={() => setSel(i)}>
              <b className={`tr-${r.traffic || "none"}`}>{r.duration}</b>
              <span>{r.summary || `Route ${i + 1}`}</span>
              <em>{r.distance}</em>
            </button>
          ))}
        </div>
      )}

      {d.mode === "driving" && (
        <div className="dir-opts">
          <label><input type="checkbox" checked={!!d.avoid_tolls} disabled={!!busy} onChange={(e) => recompute({ avoid_tolls: e.target.checked })} /> Avoid tolls</label>
          <label><input type="checkbox" checked={!!d.avoid_highways} disabled={!!busy} onChange={(e) => recompute({ avoid_highways: e.target.checked })} /> Avoid highways</label>
        </div>
      )}

      <div className="map-frame dir-map">
        <iframe key={embed} src={embed} title="route" loading="lazy" referrerPolicy="no-referrer-when-downgrade" allowFullScreen />
      </div>

      <div className="dir-steps">
        {shown.map((s, i) =>
          s.transit ? (
            <div key={i} className="dir-step transit">
              <span className="dir-line" style={{ background: s.transit.color || "var(--cy)", color: s.transit.text_color || "#000" }}>{s.transit.line || s.transit.vehicle}</span>
              <div className="dir-step-main">
                <div><b>{s.transit.vehicle || "Transit"}</b> toward {s.transit.headsign}</div>
                <div className="muted small">{s.transit.depart} {s.transit.from} → {s.transit.arrive} {s.transit.to}{s.transit.stops ? ` · ${s.transit.stops} stops` : ""}</div>
              </div>
              <span className="dir-step-d">{s.duration}</span>
            </div>
          ) : (
            <div key={i} className="dir-step">
              <span className="dir-arrow">{ARROW[s.maneuver] || (s.mode === "WALK" && d.mode === "transit" ? "🚶" : "↑")}</span>
              <div className="dir-step-main">{s.text}</div>
              <span className="dir-step-d">{s.distance}</span>
            </div>
          ),
        )}
        {steps.length > 10 && (
          <button className="py-thread-toggle" onClick={() => setShowAll(!showAll)}>
            {showAll ? "▾ FEWER STEPS" : `▸ ALL ${steps.length} STEPS`}
          </button>
        )}
        <div className="dir-step arrive"><span className="dir-arrow">◉</span><div className="dir-step-main">Arrive at {d.destination}</div></div>
      </div>

      <div className="card-actions">
        <button className="hbtn hbtn-cyan" onClick={() => core.openExternal(openUrl)}>Open in Google Maps ↗</button>
      </div>
    </div>
  );
}
