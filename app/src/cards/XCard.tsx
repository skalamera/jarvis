import { useEffect, useRef, useState } from "react";
import type { Card } from "../types";

/** X (formerly Twitter): the real x.com in a <webview> with its own persistent login ("persist:x").
 *  Side panel: X's own narrow (mobile-style) layout. Maximized: the full desktop layout. A JARVIS bar on top
 *  switches between Home / Trending / Notifications / Messages and runs searches (Latest or Top). */
const TABS: { key: string; label: string; url: string }[] = [
  { key: "home", label: "Home", url: "https://x.com/home" },
  { key: "trending", label: "Trending", url: "https://x.com/explore/tabs/trending" },
  { key: "notifications", label: "Alerts", url: "https://x.com/notifications" },
  { key: "messages", label: "DMs", url: "https://x.com/messages" },
];

const tabFor = (u: string) =>
  /\/search\?/.test(u) ? "search" : /\/explore/.test(u) ? "trending" : /\/notifications/.test(u) ? "notifications"
    : /\/messages/.test(u) ? "messages" : /\/home/.test(u) ? "home" : "";

export function XCard({ card }: { card: Card }) {
  const d = card.data || {};
  const start: string = d.url || "https://x.com/home";
  const ref = useRef<any>(null);
  const [url, setUrl] = useState(start);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState<string>(d.query || "");
  const [latest, setLatest] = useState<boolean>(d.latest !== false);

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
  // a later x_open for the same card (Jarvis re-targets it): follow the new page
  useEffect(() => {
    if (d.url && ref.current && d.url !== url) { try { ref.current.loadURL(d.url); } catch { /* */ } }
    if (d.query !== undefined) setQ(d.query || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d.url]);

  const go = (u: string) => { try { ref.current?.loadURL(u); } catch { /* */ } };
  const search = (e?: React.FormEvent, top?: boolean) => {
    e?.preventDefault();
    const text = q.trim();
    if (!text) return;
    const lt = top === undefined ? latest : !top;
    go(`https://x.com/search?q=${encodeURIComponent(text)}&src=typed_query${lt ? "&f=live" : ""}`);
  };
  const act = tabFor(url);
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();
  return (
    <div className="xc" onClick={stop}>
      <div className="xc-bar">
        <span className="xc-logo" title="X">𝕏</span>
        <div className="xc-tabs">
          {TABS.map((t) => (
            <button key={t.key} className={`xc-tab ${act === t.key ? "on" : ""}`} onClick={(e) => { stop(e); go(t.url); }}>{t.label}</button>
          ))}
        </div>
        <button className="md-ic" title="Back" onClick={(e) => { stop(e); try { ref.current?.goBack(); } catch { /* */ } }}>‹</button>
        <button className="md-ic" title="Reload" onClick={(e) => { stop(e); try { ref.current?.reload(); } catch { /* */ } }}>{loading ? <span className="cx-spin" /> : "⟳"}</button>
        <a className="md-ic" href={url} target="_blank" rel="noreferrer" title="Open in browser" onClick={stop}>↗</a>
      </div>
      <form className="xc-search" onSubmit={(e) => search(e)} onClick={stop}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search X…" onKeyDown={stop} />
        <button type="button" className={`xc-mode ${latest ? "on" : ""}`} onClick={() => { setLatest(true); if (q.trim()) search(undefined, false); }}>Latest</button>
        <button type="button" className={`xc-mode ${!latest ? "on" : ""}`} onClick={() => { setLatest(false); if (q.trim()) search(undefined, true); }}>Top</button>
      </form>
      <webview ref={ref} className="xc-view" src={start} partition="persist:x" allowpopups={"true" as any} />
    </div>
  );
}
