import { useEffect, useRef, useState } from "react";
import type { Card } from "../types";

/** A real website running inside JARVIS (Electron <webview>, own persistent login per site). */
export function WebAppCard({ card }: { card: Card }) {
  const d = card.data || {};
  const ref = useRef<any>(null);
  const [url, setUrl] = useState<string>(d.url);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const w = ref.current;
    if (!w) return;
    const nav = () => { try { setUrl(w.getURL()); } catch { /* not ready */ } };
    const on = () => setLoading(true), off = () => { setLoading(false); nav(); };
    w.addEventListener("did-start-loading", on);
    w.addEventListener("did-stop-loading", off);
    w.addEventListener("did-navigate-in-page", nav);
    return () => { w.removeEventListener("did-start-loading", on); w.removeEventListener("did-stop-loading", off); w.removeEventListener("did-navigate-in-page", nav); };
  }, []);
  const go = (fn: string) => (e: React.MouseEvent) => { e.stopPropagation(); try { ref.current?.[fn](); } catch { /* */ } };
  return (
    <div className="wa" onClick={(e) => e.stopPropagation()}>
      <div className="wa-bar">
        <button className="md-ic" title="Back" onClick={go("goBack")}>‹</button>
        <button className="md-ic" title="Forward" onClick={go("goForward")}>›</button>
        <button className="md-ic" title="Reload" onClick={go("reload")}>{loading ? <span className="cx-spin" /> : "⟳"}</button>
        <button className="md-ic" title="Home" onClick={(e) => { e.stopPropagation(); ref.current?.loadURL(d.url); }}>⌂</button>
        <span className="wa-url">{url.replace(/^https:\/\/(www\.)?/, "")}</span>
        <a className="md-ic" href={url} target="_blank" rel="noreferrer" title="Open in browser">↗</a>
      </div>
      <webview ref={ref} className="wa-view" src={d.url} partition={d.partition || "persist:webapps"} allowpopups={"true" as any} />
    </div>
  );
}
