import { useState } from "react";
import { createPortal } from "react-dom";
import { core } from "../ws/core";
import { useStore } from "../state/store";
import type { Card } from "../types";
import { SearchBar } from "./SearchBar";

/* Google Places cards, laid out like Google's knowledge panel: header line (stars · reviews · price · type · open),
   photo mosaic, map, hours, review summary + reviews, and Call / Website / Directions / Reserve.
   Photos come as short-lived googleusercontent URLs resolved by Core (no API key in the HUD). */

const open = (u?: string) => u && window.jarvis?.open(u);

function Stars({ r, size = 13 }: { r?: number; size?: number }) {
  if (r == null) return null;
  return (
    <span className="stars" style={{ fontSize: size }} aria-label={`${r} stars`}>
      {[0, 1, 2, 3, 4].map((i) => {
        const f = Math.max(0, Math.min(1, r - i));
        return (
          <span key={i} className="star">
            <span className="star-bg">★</span>
            <span className="star-fg" style={{ width: `${f * 100}%` }}>★</span>
          </span>
        );
      })}
    </span>
  );
}

const fmtCount = (n?: number) => (n == null ? "" : n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}K` : String(n));

function OpenBadge({ h }: { h: any }) {
  if (!h?.status) return null;
  const cls = h.open_now ? "open" : "closed";
  return <span className={`pl-open ${cls}`}>{h.status}{h.detail ? <em> · {h.detail}</em> : null}</span>;
}

function HeaderLine({ p }: { p: any }) {
  return (
    <div className="pl-line">
      {p.rating != null && <><b className="pl-rating">{p.rating.toFixed(1)}</b><Stars r={p.rating} /></>}
      {p.reviews_count != null && <span className="pl-link" onClick={() => open(p.maps_url)}>{p.reviews_count.toLocaleString()} Google reviews</span>}
      <div className="pl-line2">
        {[p.price, p.type].filter(Boolean).map((x: string) => <span key={x} className="pl-sep">{x}</span>)}
        <OpenBadge h={p.hours} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------------

export function PlacesList({ card }: { card: Card }) {
  const d = card.data || {};
  const [loading, setLoading] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const search = async (q: string) => {
    setBusy(true);
    const r = await core.rpc("places_find", { query: q });
    setBusy(false);
    if (r.ok) core.patchCard(card.id, r.result, `${q || "Places"} · near ${r.result.near || "you"}`);
    else useStore.getState().toast({ text: r.error || "Search failed", error: true });
  };
  const bar = <SearchBar value={d.query === "popular places" ? "" : d.query} placeholder="Search places (coffee, gas, parks…)" busy={busy} onSearch={search} />;
  if (!d.places?.length) return <div className="pl-list">{bar}<div className="muted">Nothing found.</div></div>;
  return (
    <div className="pl-list">
      {bar}
      {d.places.map((p: any) => (
        <button key={p.id} className={`pl-row ${loading === p.id ? "loading" : ""}`}
          onClick={() => { setLoading(p.id); core.openPlace(p.id); window.setTimeout(() => setLoading(null), 6000); }}>
          <div className="pl-thumb" style={p.photo ? { backgroundImage: `url("${p.photo}")` } : undefined}>
            {!p.photo && <span>◎</span>}
          </div>
          <div className="pl-row-main">
            <div className="pl-row-name">{p.name}</div>
            <div className="pl-line small">
              {p.rating != null && <><b className="pl-rating">{p.rating.toFixed(1)}</b><Stars r={p.rating} size={11} /><span>({fmtCount(p.reviews_count)})</span></>}
              {p.price && <span>· {p.price}</span>}
            </div>
            <div className="pl-row-sub">{p.type}{p.short_address ? ` · ${p.short_address}` : ""}</div>
            <OpenBadge h={p.hours} />
          </div>
          <span className="pl-row-go">{loading === p.id ? "…" : "›"}</span>
        </button>
      ))}
      <div className="pl-attrib">Google · click a place for photos, hours &amp; reviews</div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------------------

export function PlaceCard({ card }: { card: Card }) {
  const p = card.data || {};
  const [hoursOpen, setHoursOpen] = useState(false);
  const [lightbox, setLightbox] = useState<number | null>(null);
  const [allReviews, setAllReviews] = useState(false);
  const photos: any[] = p.photos || [];
  const q = encodeURIComponent(p.lat != null ? `${p.lat},${p.lng}` : p.address || p.name);
  const mapEmbed = `https://maps.google.com/maps?q=${q}&z=15&output=embed`;
  // Directions open as a JARVIS directions card (Routes API via Core), never a browser window.
  const toast = useStore((s) => s.toast);
  const route = () => { toast({ text: `Routing to ${p.name}…` }); core.openDirections([p.name, p.address].filter(Boolean).join(", ")); };
  const reviews: any[] = allReviews ? p.reviews || [] : (p.reviews || []).slice(0, 2);

  return (
    <div className="pl-card">
      <HeaderLine p={p} />

      {photos.length > 0 && (
        <div className={`pl-mosaic n${Math.min(photos.length, 5)}`}>
          {photos.slice(0, 5).map((ph, i) => (
            <div key={i} className={`pl-ph ph${i}`} style={{ backgroundImage: `url("${ph.url}")` }} onClick={() => setLightbox(i)}>
              {i === 4 && photos.length > 5 && <span className="pl-more">+{photos.length - 5}</span>}
            </div>
          ))}
        </div>
      )}

      <div className="pl-actions">
        {p.phone && <button className="bact" onClick={() => open(`tel:${p.phone_intl || p.phone}`)} title={p.phone}>☎ Call</button>}
        {p.website && <button className="bact" onClick={() => open(p.website)}>🌐 Website</button>}
        <button className="bact primary" onClick={route}>➤ Directions</button>
        {p.reservable && <button className="bact" onClick={() => open(p.maps_url)}>🍽 Reserve</button>}
        <button className="bact icon" title="Open in Google Maps" onClick={() => open(p.maps_url)}>↗</button>
      </div>

      <div className={`pl-grid ${hoursOpen ? "hours-open" : ""}`}>
        <div className="pl-map">
          <iframe src={mapEmbed} title="map" loading="lazy" referrerPolicy="no-referrer-when-downgrade" />
          <button className="pl-map-hit" title={`Directions to ${p.name}`} onClick={route} />
          <div className="pl-addr">{p.address}{p.phone ? <><br /><span className="muted">{p.phone}</span></> : null}</div>
        </div>
        <div className="pl-hours">
          <button className="pl-box-head" onClick={() => setHoursOpen(!hoursOpen)}>HOURS <i>{hoursOpen ? "▾" : "›"}</i></button>
          <div className={`pl-hours-now ${p.hours?.open_now ? "open" : "closed"}`}>{p.hours?.status || "—"}</div>
          {p.hours?.detail && <div className="pl-hours-detail">{p.hours.detail}</div>}
          {hoursOpen && (
            <div className="pl-week">
              {(p.hours?.week || []).map((w: string, i: number) => {
                const [day, ...rest] = w.split(": ");
                return (
                  <div key={i} className={i === p.hours?.today_index ? "today" : ""}>
                    <span>{day.slice(0, 3)}</span><span>{rest.join(": ")}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {(p.review_summary || p.reviews?.length > 0) && (
        <div className="pl-reviews">
          <div className="pl-box-head static">
            REVIEWS
            <span className="pl-rev-score"><b>{p.rating?.toFixed(1)}</b><Stars r={p.rating} /><span className="muted">({fmtCount(p.reviews_count)})</span></span>
          </div>
          {p.review_summary && <p className="pl-summary">{p.review_summary}</p>}
          {reviews.map((r, i) => (
            <div key={i} className="pl-rev">
              <div className="pl-rev-head">
                {r.avatar ? <img src={r.avatar} alt="" referrerPolicy="no-referrer" /> : <span className="pl-av">{(r.author || "?")[0]}</span>}
                <b>{r.author}</b><Stars r={r.rating} size={10} /><span className="muted">{r.when}</span>
              </div>
              <div className="pl-rev-text">{r.text}</div>
            </div>
          ))}
          {(p.reviews?.length || 0) > 2 && (
            <button className="py-thread-toggle" onClick={() => setAllReviews(!allReviews)}>
              {allReviews ? "▾ FEWER REVIEWS" : `▸ ${p.reviews.length - 2} MORE REVIEWS`}
            </button>
          )}
        </div>
      )}

      {(p.overview || p.features?.length > 0) && (
        <div className="pl-about">
          {p.overview && <p>{p.overview}</p>}
          {p.features?.length > 0 && <div className="pl-feats">{p.features.map((f: string) => <span key={f} className="chip">✓ {f}</span>)}</div>}
        </div>
      )}
      <div className="pl-attrib">Photos, reviews &amp; hours from Google</div>

      {lightbox !== null && photos[lightbox] && createPortal(
        <div className="pl-lightbox" onClick={() => setLightbox(null)}>
          <img src={photos[lightbox].url} alt="" referrerPolicy="no-referrer" />
          <div className="pl-lb-bar" onClick={(e) => e.stopPropagation()}>
            <button className="bact" onClick={() => setLightbox((lightbox + photos.length - 1) % photos.length)}>‹</button>
            <span>{lightbox + 1} / {photos.length}{photos[lightbox].by ? ` · ${photos[lightbox].by}` : ""}</span>
            <button className="bact" onClick={() => setLightbox((lightbox + 1) % photos.length)}>›</button>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
