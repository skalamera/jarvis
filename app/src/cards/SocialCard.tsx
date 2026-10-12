import { useEffect, useRef, useState } from "react";
import type { Card } from "../types";

/** Instagram / Facebook / LinkedIn: the real site in a <webview> with its own persistent login ("persist:<site>"),
 *  same model as the X display. Side panel: the site's own narrow (mobile-style) layout. Maximized: the full desktop
 *  layout. A JARVIS bar on top switches sections and runs searches (with a scope where the site has one). */
type Tab = { key: string; label: string; url: string; match: RegExp };
type Scope = { key: string; label: string };
type Site = {
  name: string; logo: string; color: string; partition: string; home: string; placeholder: string;
  tabs: Tab[]; scopes: Scope[]; search: (q: string, scope: string) => string;
};

const IG = "https://www.instagram.com", FB = "https://www.facebook.com", LI = "https://www.linkedin.com";
const enc = encodeURIComponent;

export const SITES: Record<string, Site> = {
  instagram: {
    name: "Instagram", logo: "launcher/Instagram.svg", color: "#e1306c", partition: "persist:instagram", home: `${IG}/`,
    placeholder: "Search Instagram… (#tag for hashtags)",
    tabs: [
      { key: "home", label: "Feed", url: `${IG}/`, match: /instagram\.com\/?(\?|$)/ },
      { key: "explore", label: "Explore", url: `${IG}/explore/`, match: /\/explore\/?(\?|$)/ },
      { key: "reels", label: "Reels", url: `${IG}/reels/`, match: /\/reels?\// },
      { key: "messages", label: "DMs", url: `${IG}/direct/inbox/`, match: /\/direct\// },
      { key: "notifications", label: "Alerts", url: `${IG}/notifications/`, match: /\/notifications/ },
    ],
    scopes: [],
    search: (q) => (q.startsWith("#") ? `${IG}/explore/tags/${enc(q.slice(1))}/` : `${IG}/explore/search/keyword/?q=${enc(q)}`),
  },
  facebook: {
    name: "Facebook", logo: "launcher/Facebook.svg", color: "#1877f2", partition: "persist:facebook", home: `${FB}/`,
    placeholder: "Search Facebook…",
    tabs: [
      { key: "home", label: "Feed", url: `${FB}/`, match: /facebook\.com\/?(\?|$)/ },
      { key: "watch", label: "Watch", url: `${FB}/watch/`, match: /\/watch/ },
      { key: "marketplace", label: "Market", url: `${FB}/marketplace/`, match: /\/marketplace/ },
      { key: "groups", label: "Groups", url: `${FB}/groups/feed/`, match: /\/groups/ },
      { key: "messages", label: "Messages", url: `${FB}/messages/`, match: /\/messages/ },
      { key: "notifications", label: "Alerts", url: `${FB}/notifications/`, match: /\/notifications/ },
    ],
    scopes: [{ key: "top", label: "Top" }, { key: "people", label: "People" }, { key: "posts", label: "Posts" }, { key: "marketplace", label: "Market" }],
    search: (q, s) => (s === "marketplace" ? `${FB}/marketplace/search/?query=${enc(q)}` : `${FB}/search/${s || "top"}/?q=${enc(q)}`),
  },
  linkedin: {
    name: "LinkedIn", logo: "launcher/LinkedIn.svg", color: "#0a66c2", partition: "persist:linkedin", home: `${LI}/feed/`,
    placeholder: "Search LinkedIn…",
    tabs: [
      { key: "home", label: "Feed", url: `${LI}/feed/`, match: /\/feed/ },
      { key: "network", label: "Network", url: `${LI}/mynetwork/`, match: /\/mynetwork/ },
      { key: "jobs", label: "Jobs", url: `${LI}/jobs/`, match: /\/jobs/ },
      { key: "messages", label: "Messages", url: `${LI}/messaging/`, match: /\/messaging/ },
      { key: "notifications", label: "Alerts", url: `${LI}/notifications/`, match: /\/notifications/ },
    ],
    scopes: [{ key: "all", label: "All" }, { key: "people", label: "People" }, { key: "companies", label: "Cos" }, { key: "jobs", label: "Jobs" }],
    search: (q, s) => (s === "jobs" ? `${LI}/jobs/search/?keywords=${enc(q)}` : `${LI}/search/results/${s === "people" || s === "companies" ? s : "all"}/?keywords=${enc(q)}`),
  },
};

const isSearch = (u: string) => /\/search|\/explore\/(tags|search)\//.test(u);

function SocialCard({ card, site }: { card: Card; site: Site }) {
  const d = card.data || {};
  const start: string = d.url || site.home;
  const ref = useRef<any>(null);
  const [url, setUrl] = useState(start);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState<string>(d.query || "");
  const [scope, setScope] = useState<string>(d.kind || site.scopes[0]?.key || "");

  useEffect(() => {
    const w = ref.current;
    if (!w) return;
    const nav = () => { try { setUrl(w.getURL()); } catch { /* not ready */ } };
    const on = () => setLoading(true), off = () => { setLoading(false); nav(); };
    w.addEventListener("did-start-loading", on);
    w.addEventListener("did-stop-loading", off);
    w.addEventListener("did-navigate", nav);
    w.addEventListener("did-navigate-in-page", nav);
    return () => {
      w.removeEventListener("did-start-loading", on); w.removeEventListener("did-stop-loading", off);
      w.removeEventListener("did-navigate", nav); w.removeEventListener("did-navigate-in-page", nav);
    };
  }, []);
  // a later <site>_open for the same card (Jarvis re-targets it): follow the new page
  useEffect(() => {
    if (d.url && ref.current && d.url !== url) { try { ref.current.loadURL(d.url); } catch { /* */ } }
    if (d.query !== undefined) setQ(d.query || "");
    if (d.kind) setScope(d.kind);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d.url]);

  const go = (u: string) => { try { ref.current?.loadURL(u); } catch { /* */ } };
  const search = (e?: React.FormEvent, s?: string) => {
    e?.preventDefault();
    const text = q.trim();
    if (text) go(site.search(text, s ?? scope));
  };
  const act = isSearch(url) ? "search" : site.tabs.find((t) => t.match.test(url))?.key || "";
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();
  return (
    <div className="xc so" style={{ ["--brand" as any]: site.color }} onClick={stop}>
      <div className="xc-bar">
        <img className="so-logo" src={site.logo} alt={site.name} title={site.name} draggable={false} />
        <div className="xc-tabs">
          {site.tabs.map((t) => (
            <button key={t.key} className={`xc-tab ${act === t.key ? "on" : ""}`} onClick={(e) => { stop(e); go(t.url); }}>{t.label}</button>
          ))}
        </div>
        <button className="md-ic" title="Back" onClick={(e) => { stop(e); try { ref.current?.goBack(); } catch { /* */ } }}>‹</button>
        <button className="md-ic" title="Reload" onClick={(e) => { stop(e); try { ref.current?.reload(); } catch { /* */ } }}>{loading ? <span className="cx-spin" /> : "⟳"}</button>
        <a className="md-ic" href={url} target="_blank" rel="noreferrer" title="Open in browser" onClick={stop}>↗</a>
      </div>
      <form className="xc-search" onSubmit={(e) => search(e)} onClick={stop}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={site.placeholder} onKeyDown={stop} />
        {site.scopes.map((s) => (
          <button key={s.key} type="button" className={`xc-mode ${scope === s.key ? "on" : ""}`}
            onClick={() => { setScope(s.key); search(undefined, s.key); }}>{s.label}</button>
        ))}
      </form>
      <webview ref={ref} className="xc-view" src={start} partition={site.partition} allowpopups={"true" as any} />
    </div>
  );
}

export const InstagramCard = ({ card }: { card: Card }) => <SocialCard card={card} site={SITES.instagram} />;
export const FacebookCard = ({ card }: { card: Card }) => <SocialCard card={card} site={SITES.facebook} />;
export const LinkedInCard = ({ card }: { card: Card }) => <SocialCard card={card} site={SITES.linkedin} />;
