import { useEffect, useMemo, useState } from "react";
import { useStore } from "../state/store";
import { core } from "../ws/core";
import type { Card } from "../types";

/* Sports cards (ESPN data): GAME (score hero, linescore, leaders, win probability, team box score, player stats,
   scoring plays, playable highlight clips, recap + articles) and SCOREBOARD (all games for a league/day; click one
   for its full game card). Read-only. */

const open = (u?: string) => u && window.jarvis?.open(u);
const stop = (e: React.MouseEvent) => e.stopPropagation();

type Team = {
  id: string; abbr: string; name: string; short: string; logo: string; color: string; alt_color: string;
  score: string | null; winner: boolean; home_away: string; record: string; linescores: (string | number)[];
  hits?: number | null; errors?: number | null;
};
type Status = { state: string; completed: boolean; detail: string; short: string; clock?: string; period?: number };

function when(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function ago(iso?: string) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function secs(n?: number) {
  if (!n && n !== 0) return "";
  return `${Math.floor(n / 60)}:${String(Math.round(n % 60)).padStart(2, "0")}`;
}

function TeamLogo({ t, size = 44 }: { t: Pick<Team, "logo" | "abbr" | "color">; size?: number }) {
  const [bad, setBad] = useState(false);
  if (!t.logo || bad)
    return <span className="sp-logo sp-logo-ph" style={{ width: size, height: size, background: t.color || undefined }}>{t.abbr}</span>;
  return <img className="sp-logo" src={t.logo} alt={t.abbr} width={size} height={size} referrerPolicy="no-referrer" onError={() => setBad(true)} />;
}

function StatusPill({ s }: { s: Status }) {
  const live = s.state === "in";
  return (
    <span className={`sp-status ${live ? "live" : s.state === "pre" ? "pre" : "final"}`}>
      {live && <i />}
      {live ? s.short || s.detail : s.completed ? (s.detail || "Final") : s.short || s.detail}
    </span>
  );
}

/* ------------------------------------------------------------------ GAME */
export function GameCard({ card }: { card: Card }) {
  const d = card.data || {};
  // Live games refresh themselves (score, clock, win probability) every 20 s; pregame every 5 min.
  const state = d.status?.state;
  useEffect(() => {
    if (!d.event_id || state === "post") return;
    let gone = false;
    const t = window.setInterval(async () => {
      const r = await core.rpc("game_refresh", { league: d.league, event_id: d.event_id });
      if (gone || !r.ok || !r.result) return;
      const s = useStore.getState();
      s.set({ cards: s.cards.map((x) => (x.id === card.id ? { ...x, data: { ...x.data, ...r.result } } : x)) });
    }, state === "in" ? 20_000 : 300_000);
    return () => { gone = true; window.clearInterval(t); };
  }, [d.event_id, d.league, state, card.id]);
  const teams: Team[] = d.teams || [];
  const [away, home] = [teams[0], teams[1]];
  const tabs = useMemo(() => {
    const t: string[] = ["summary"];
    if (d.team_stats?.length) t.push("team stats");
    if (d.players?.some((p: any) => p.groups?.length)) t.push("players");
    if (d.scoring?.length) t.push("scoring");
    if (d.videos?.length) t.push("highlights");
    if (d.articles?.length || d.recap) t.push("news");
    return t;
  }, [d]);
  const [tab, setTab] = useState("summary");
  if (!away || !home) return <div className="fx-empty">No game data.</div>;
  const pre = d.status?.state === "pre";

  return (
    <div className="sp" style={{ ["--sp-a" as any]: away.color || "#39d0ff", ["--sp-h" as any]: home.color || "#39d0ff" }}>
      <div className="sp-hero">
        <HeroTeam t={away} pre={pre} />
        <div className="sp-mid">
          <StatusPill s={d.status} />
          {!pre ? (
            <div className="sp-score">
              <b className={away.winner ? "w" : home.winner ? "l" : ""}>{away.score ?? "-"}</b>
              <span>–</span>
              <b className={home.winner ? "w" : away.winner ? "l" : ""}>{home.score ?? "-"}</b>
            </div>
          ) : <div className="sp-when">{when(d.date)}</div>}
          <div className="sp-league">{d.league_label}</div>
        </div>
        <HeroTeam t={home} pre={pre} right />
      </div>

      <WinOdds d={d} away={away} home={home} />

      {!pre && d.periods?.length > 0 && <Linescore d={d} away={away} home={home} />}

      <div className="sp-meta">
        {[d.venue, d.city, d.attendance ? `${Number(d.attendance).toLocaleString()} attendance` : "", d.broadcast, !pre ? when(d.date) : ""]
          .filter(Boolean).join(" · ")}
      </div>

      {tabs.length > 1 && (
        <div className="fx-tabs sp-tabs">
          {tabs.map((t) => (
            <button key={t} className={`fx-tab ${tab === t ? "on" : ""}`} onClick={(e) => { stop(e); setTab(t); }}>
              {t}{t === "highlights" ? ` ${d.videos.length}` : ""}
            </button>
          ))}
        </div>
      )}

      {tab === "summary" && <Summary d={d} away={away} home={home} goHighlights={() => setTab("highlights")} />}
      {tab === "team stats" && <TeamStats rows={d.team_stats} away={away} home={home} />}
      {tab === "players" && <Players players={d.players} away={away} home={home} />}
      {tab === "scoring" && <Scoring plays={d.scoring} teams={teams} sport={d.sport} />}
      {tab === "highlights" && <Highlights videos={d.videos} />}
      {tab === "news" && <News recap={d.recap} articles={d.articles} />}

      <div className="fx-actions">
        <button className="hbtn sm hbtn-cyan" onClick={(e) => { stop(e); open(d.web_url); }}>ESPN game page ↗</button>
        {d.recap?.url && <button className="hbtn sm" onClick={(e) => { stop(e); open(d.recap.url); }}>Recap ↗</button>}
      </div>
    </div>
  );
}

function HeroTeam({ t, pre, right = false }: { t: Team; pre: boolean; right?: boolean }) {
  return (
    <div className={`sp-team ${right ? "sp-team-home" : ""} ${!pre && !t.winner ? "dim" : ""}`}>
      <TeamLogo t={t} size={52} />
      <div className="sp-team-n">{t.short || t.name}</div>
      <div className="sp-team-r">{t.record}{t.home_away ? ` · ${t.home_away}` : ""}</div>
    </div>
  );
}

function Linescore({ d, away, home }: { d: any; away: Team; home: Team }) {
  const periods: string[] = d.periods;
  const baseball = d.sport === "baseball";
  const row = (t: Team) => (
    <tr className={t.winner ? "w" : ""}>
      <th><TeamLogo t={t} size={16} /> {t.abbr}</th>
      {periods.map((_, i) => <td key={i}>{t.linescores[i] ?? ""}</td>)}
      <td className="tot">{t.score}</td>
      {baseball && <td className="tot2">{t.hits ?? ""}</td>}
      {baseball && <td className="tot2">{t.errors ?? ""}</td>}
    </tr>
  );
  return (
    <table className="sp-line">
      <thead>
        <tr><th />{periods.map((p) => <td key={p}>{p}</td>)}<td className="tot">{baseball ? "R" : "T"}</td>
          {baseball && <td className="tot2">H</td>}{baseball && <td className="tot2">E</td>}</tr>
      </thead>
      <tbody>{row(away)}{row(home)}</tbody>
    </table>
  );
}

function Summary({ d, away, home, goHighlights }: { d: any; away: Team; home: Team; goHighlights: () => void }) {
  const leaders: any[] = d.leaders || [];
  const clip = d.videos?.find((v: any) => v.mp4);
  return (
    <div className="sp-sum">
      {leaders.length > 0 && (
        <>
          <div className="fx-sec">GAME LEADERS</div>
          <div className="sp-leaders">
            {leaders.slice(0, 10).map((l, i) => (
              <div key={i} className="sp-leader">
                {l.headshot ? <img src={l.headshot} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.visibility = "hidden")} />
                  : <span className="sp-hs-ph">{(l.name || "?").slice(0, 1)}</span>}
                <div>
                  <label>{l.category}{l.team ? ` · ${l.team}` : ""}</label>
                  <div className="sp-leader-n">{l.name}{l.pos ? <em> {l.pos}</em> : null}</div>
                  <div className="sp-leader-v">{l.value}</div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      {d.win_prob?.length > 2 && <WinProb wp={d.win_prob} away={away} home={home} />}
      {clip && (
        <button className="sp-clip-teaser" onClick={(e) => { stop(e); goHighlights(); }}>
          {clip.thumb && <img src={clip.thumb} alt="" referrerPolicy="no-referrer" />}
          <span className="sp-play">▶</span>
          <div><label>HIGHLIGHTS · {d.videos.length}</label><div>{clip.title}</div></div>
        </button>
      )}
      {d.recap && (
        <button className="sp-recap" onClick={(e) => { stop(e); open(d.recap.url); }}>
          {d.recap.image && <img src={d.recap.image} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.display = "none")} />}
          <div>
            <label>RECAP</label>
            <div className="sp-recap-h">{d.recap.headline}</div>
            {d.recap.description && <div className="sp-recap-d">{d.recap.description}</div>}
          </div>
        </button>
      )}
    </div>
  );
}

/** Headline win chance: ESPN's pregame projection before kickoff, the live model during the game. */
function WinOdds({ d, away, home }: { d: any; away: Team; home: Team }) {
  const live: number[] = d.win_prob || [];
  const state = d.status?.state;
  if (state === "post") return null;
  const h = state === "in" && live.length ? live[live.length - 1] : d.pregame_prob;
  if (h == null) return null;
  const pre = d.pregame_prob;
  const delta = state === "in" && pre != null ? Math.round((h - pre) * 100) : 0;
  const hp = Math.round(h * 100), ap = 100 - hp;
  return (
    <div className="sp-odds">
      <div className="sp-odds-h">
        <span className={ap > hp ? "fav" : ""}>{away.abbr} {ap}%</span>
        <label>{state === "in" ? <><i className="sp-live-dot" />LIVE WIN PROBABILITY</> : "PREGAME WIN PROBABILITY"}
          {delta !== 0 && <em> · {home.abbr} {delta > 0 ? "+" : ""}{delta} since kickoff</em>}</label>
        <span className={hp > ap ? "fav" : ""}>{hp}% {home.abbr}</span>
      </div>
      <div className="sp-odds-bar"><b style={{ width: `${ap}%` }} /><s style={{ width: `${hp}%` }} /></div>
    </div>
  );
}

function WinProb({ wp, away, home }: { wp: number[]; away: Team; home: Team }) {
  const W = 400, H = 70;
  const x = (i: number) => (i / (wp.length - 1)) * W;
  const y = (v: number) => H - v * H; // home win % (1 = top)
  const line = wp.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const last = wp[wp.length - 1];
  return (
    <div className="sp-wp">
      <div className="fx-sec">WIN PROBABILITY <span className="sp-wp-now">{home.abbr} {Math.round(last * 100)}% · {away.abbr} {Math.round((1 - last) * 100)}%</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="sp-wp-svg">
        <defs>
          <linearGradient id="spwp" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--sp-h)" stopOpacity="0.55" />
            <stop offset="50%" stopColor="var(--sp-h)" stopOpacity="0.05" />
            <stop offset="50%" stopColor="var(--sp-a)" stopOpacity="0.05" />
            <stop offset="100%" stopColor="var(--sp-a)" stopOpacity="0.55" />
          </linearGradient>
        </defs>
        <path d={`${line}L${W},${H / 2}L0,${H / 2}Z`} fill="url(#spwp)" />
        <line x1="0" x2={W} y1={H / 2} y2={H / 2} className="sp-wp-mid" />
        <path d={line} className="sp-wp-line" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="sp-wp-axis"><span>{home.abbr} ▲</span><span>{away.abbr} ▼</span></div>
    </div>
  );
}

function num(v: any): number | null {
  if (v == null) return null;
  const m = String(v).match(/-?\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : null;
}

function TeamStats({ rows, away, home }: { rows: any[]; away: Team; home: Team }) {
  return (
    <div className="sp-ts">
      <div className="sp-ts-head"><span><TeamLogo t={away} size={18} /> {away.abbr}</span><span /><span>{home.abbr} <TeamLogo t={home} size={18} /></span></div>
      {rows.map((r, i) => {
        const a = num(r.away), h = num(r.home);
        const sum = a != null && h != null && a + h > 0 && !/-/.test(String(r.away)) ? a + h : null;
        return (
          <div key={i} className="sp-ts-row">
            <b className={sum && a! > h! ? "hi" : ""}>{r.away ?? "-"}</b>
            <div className="sp-ts-mid">
              <label>{r.label}</label>
              {sum ? <div className="sp-ts-bar"><i style={{ width: `${(a! / sum) * 100}%` }} /><em style={{ width: `${(h! / sum) * 100}%` }} /></div> : null}
            </div>
            <b className={sum && h! > a! ? "hi" : ""}>{r.home ?? "-"}</b>
          </div>
        );
      })}
    </div>
  );
}

function Players({ players, away, home }: { players: any[]; away: Team; home: Team }) {
  const [side, setSide] = useState(0);
  const byTeam = [players.find((p) => p.team === away.abbr) || players[0], players.find((p) => p.team === home.abbr) || players[1]];
  const cur = byTeam[side] || { groups: [] };
  return (
    <div className="sp-pl">
      <div className="sp-seg">
        {[away, home].map((t, i) => (
          <button key={t.abbr} className={side === i ? "on" : ""} onClick={(e) => { stop(e); setSide(i); }}>
            <TeamLogo t={t} size={18} /> {t.short || t.abbr}
          </button>
        ))}
      </div>
      {cur.groups.map((g: any) => (
        <div key={g.name} className="sp-grp">
          <div className="fx-sec">{(g.title || g.name).replace(/([a-z])([A-Z])/g, "$1 $2").toUpperCase()}</div>
          <div className="sp-tbl-wrap">
            <table className="sp-tbl">
              <thead><tr><th>PLAYER</th>{g.labels.map((l: string) => <td key={l}>{l}</td>)}</tr></thead>
              <tbody>
                {g.rows.map((r: any) => (
                  <tr key={r.id || r.name}>
                    <th title={r.full}>
                      {r.headshot ? <img src={r.headshot} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.visibility = "hidden")} /> : <span className="sp-hs-dot" />}
                      <span>{r.name}</span>{r.pos && <em>{r.pos}</em>}
                    </th>
                    {r.stats.map((s: string, i: number) => <td key={i}>{s}</td>)}
                  </tr>
                ))}
                {g.totals?.some((x: string) => x) && (
                  <tr className="sp-tot"><th>TEAM</th>{g.totals.map((s: string, i: number) => <td key={i}>{s}</td>)}</tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

function Scoring({ plays, teams, sport }: { plays: any[]; teams: Team[]; sport: string }) {
  const by = Object.fromEntries(teams.map((t) => [t.abbr, t]));
  const plabel = (p: number) => (sport === "baseball" ? `Inning ${p}` : sport === "soccer" ? (p === 1 ? "First half" : p === 2 ? "Second half" : `Period ${p}`)
    : sport === "hockey" ? (p <= 3 ? `Period ${p}` : "Overtime") : p <= 4 ? `Quarter ${p}` : "Overtime");
  let lastP: number | null = null;
  return (
    <div className="sp-plays">
      {plays.map((p, i) => {
        const t = by[p.team];
        const head = p.period !== lastP ? <div key={`h${i}`} className="sp-plays-p">{plabel(p.period)}</div> : null;
        lastP = p.period;
        return (
          <div key={i}>
            {head}
            <div className="sp-play-row" style={{ ["--c" as any]: t?.color || "var(--cy)" }}>
              {t ? <TeamLogo t={t} size={22} /> : <span />}
              <div className="sp-play-t">
                <div>{p.type && <b>{p.type}</b>} {p.text}</div>
                <small>{p.clock}</small>
              </div>
              <div className="sp-play-s">{p.away ?? ""}–{p.home ?? ""}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Highlights({ videos }: { videos: any[] }) {
  const [i, setI] = useState(0);
  const v = videos[i];
  return (
    <div className="sp-hl">
      {v?.mp4 ? (
        <video key={v.mp4} className="sp-video" src={v.mp4} poster={v.thumb} controls autoPlay playsInline onClick={stop} />
      ) : v ? (
        <button className="sp-clip-teaser" onClick={(e) => { stop(e); open(v.web); }}>
          {v.thumb && <img src={v.thumb} alt="" referrerPolicy="no-referrer" />}<span className="sp-play">↗</span>
          <div><label>WATCH ON ESPN</label><div>{v.title}</div></div>
        </button>
      ) : null}
      {v && <div className="sp-hl-now"><b>{v.title}</b>{v.description && v.description !== v.title ? <span>{v.description}</span> : null}</div>}
      <div className="sp-hl-list">
        {videos.map((x, j) => (
          <button key={j} className={`sp-hl-row ${j === i ? "on" : ""}`} onClick={(e) => { stop(e); setI(j); }}>
            <span className="sp-hl-th">
              {x.thumb && <img src={x.thumb} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.visibility = "hidden")} />}
              {x.duration ? <em>{secs(x.duration)}</em> : null}
            </span>
            <span className="sp-hl-t">{x.title}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function News({ recap, articles }: { recap: any; articles: any[] }) {
  const list = [...(recap ? [{ ...recap, type: "Recap" }] : []), ...(articles || []).filter((a) => a.url !== recap?.url)];
  return (
    <div className="fx-news">
      {list.map((a, i) => (
        <button key={i} className="fx-news-row" onClick={(e) => { stop(e); open(a.url); }}>
          {a.image ? <img src={a.image} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.visibility = "hidden")} /> : <span className="fx-news-ph">◆</span>}
          <div>
            <div className="fx-news-t">{a.headline}</div>
            <div className="fx-news-m">ESPN{a.type && a.type !== "Story" ? ` · ${a.type === "Media" ? "Video" : a.type}` : ""}{a.published ? ` · ${ago(a.published)}` : ""}</div>
          </div>
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ SCOREBOARD */
export function ScoreboardCard({ card }: { card: Card }) {
  const d = card.data || {};
  const games: any[] = d.games || [];
  return (
    <div className="sp-sb">
      {d.week ? <div className="sp-meta">Week {d.week}</div> : null}
      {games.map((g) => {
        const [a, h] = g.teams as Team[];
        const pre = g.status?.state === "pre";
        return (
          <button key={g.event_id} className="sp-sb-row" onClick={(e) => { stop(e); core.openGame(g.league, g.event_id); }}>
            <div className="sp-sb-teams">
              {[a, h].map((t) => t && (
                <div key={t.abbr} className={`sp-sb-t ${!pre && !t.winner && g.status?.completed ? "dim" : ""}`}>
                  <TeamLogo t={t} size={22} /><span>{t.short || t.name}</span><em>{t.record}</em><b>{pre ? "" : t.score ?? ""}</b>
                </div>
              ))}
            </div>
            <div className="sp-sb-st">
              <StatusPill s={g.status} />
              {pre && <small>{when(g.date)}</small>}
              {g.broadcast && <small>{g.broadcast}</small>}
            </div>
          </button>
        );
      })}
    </div>
  );
}
