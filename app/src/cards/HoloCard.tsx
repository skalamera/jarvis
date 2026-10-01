import { motion } from "framer-motion";
import { useStore } from "../state/store";
import type { Card } from "../types";
import { CalendarCard, ConfirmCard, DocumentCard, DraftEditor, EmailList, EmailView, FilesCard, NoticeCard, ThreadView, acctTag } from "./Cards";
import { ChartCard, ImageCard, LinkCard, ListCard, StatsCard, TableCard, VisualMarkdown } from "./Visuals";
import { MapCard } from "./MapCard";
import { WeatherCard } from "./WeatherCard";
import { PylonList, PylonTicket } from "./PylonCards";
import { PlaceCard, PlacesList } from "./PlaceCards";
import { DirectionsCard } from "./DirectionsCard";
import { CryptoCard, MarketCard, StockCard, StockCompareCard } from "./MarketCards";
import { SlackCard } from "./SlackCards";
import { MusicCard, VideoCard } from "./MediaCards";
import { GameCard, ScoreboardCard } from "./SportsCards";
import { ArtifactCard, CodebaseCard, CodeChangesCard, CodeFileCard } from "./WorkspaceCards";
import { core } from "../ws/core";

const BODY: Record<string, (p: { card: Card }) => React.ReactElement> = {
  email_list: EmailList,
  email: EmailView,
  thread: ThreadView,
  draft: DraftEditor,
  confirm: ConfirmCard,
  calendar: CalendarCard,
  files: FilesCard,
  document: DocumentCard,
  notice: NoticeCard,
  "visual.chart": ChartCard,
  "visual.stats": StatsCard,
  "visual.table": TableCard,
  "visual.list": ListCard,
  "visual.image": ImageCard,
  "visual.link": LinkCard,
  "visual.markdown": VisualMarkdown,
  "visual.map": MapCard,
  weather: WeatherCard,
  pylon_list: PylonList,
  pylon_ticket: PylonTicket,
  places: PlacesList,
  place: PlaceCard,
  directions: DirectionsCard,
  stock: StockCard,
  stock_compare: StockCompareCard,
  market: MarketCard,
  slack: SlackCard,
  crypto: CryptoCard,
  sports_game: GameCard,
  sports_scoreboard: ScoreboardCard,
  music: MusicCard,
  video: VideoCard,
  artifact: ArtifactCard,
  codebase: CodebaseCard,
  code_file: CodeFileCard,
  code_changes: CodeChangesCard,
};

const KIND_LABEL: Record<string, string> = {
  email_list: "MAIL", email: "MESSAGE", thread: "THREAD", draft: "DRAFT", confirm: "AUTHORIZE", calendar: "CALENDAR",
  files: "DRIVE", document: "DOCUMENT", notice: "STATUS", "visual.chart": "ANALYSIS", "visual.stats": "TELEMETRY",
  "visual.table": "DATA", "visual.list": "INDEX", "visual.image": "IMAGE", "visual.link": "LINK", "visual.markdown": "BRIEF", "visual.map": "NAVIGATION", weather: "WEATHER", pylon_list: "PYLON", pylon_ticket: "TICKET", places: "PLACES", place: "PLACE", directions: "ROUTE", stock: "MARKETS", stock_compare: "COMPARE", market: "MARKETS", crypto: "CRYPTO", slack: "SLACK", sports_game: "SPORTS", sports_scoreboard: "SCORES", music: "MUSIC", video: "VIDEO", artifact: "FILE", codebase: "CODEBASE", code_file: "SOURCE", code_changes: "CHANGES",
};

export function HoloCard({ card, index }: { card: Card; index: number }) {
  const remove = useStore((s) => s.removeCard);
  const focus = useStore((s) => s.focusCardId);
  const setStore = useStore((s) => s.set);
  const Body = BODY[card.kind];
  if (!Body) return null;
  const confirm = card.kind === "confirm";
  const tag = acctTag(card.account);
  return (
    <motion.section
      layout
      initial={{ opacity: 0, x: 40, scale: 0.97, filter: "blur(6px)" }}
      animate={{ opacity: 1, x: 0, scale: 1, filter: "blur(0px)" }}
      exit={{ opacity: 0, x: 30, scale: 0.97, filter: "blur(6px)", transition: { duration: 0.2 } }}
      transition={{ type: "spring", stiffness: 260, damping: 28, delay: Math.min(index, 3) * 0.05 }}
      className={`holo ${confirm ? "holo-amber" : ""} ${focus === card.id ? "holo-focus" : ""} kind-${card.kind.replace(".", "-")}`}
      onClick={() => {
        setStore({ focusCardId: card.id });
        const a = card.data?.artifact;
        if (a) core.focus({ type: "file", id: a.id, filename: a.filename, kind: a.kind });  // "this" = the file he clicked
      }}
    >
      <span className="corner tl" /><span className="corner tr" /><span className="corner bl" /><span className="corner br" />
      <header className="holo-head">
        <span className="holo-kind">{KIND_LABEL[card.kind] ?? card.kind.toUpperCase()}</span>
        {tag && <span className={`acct acct-${card.account}`}>{tag}</span>}
        <span className="holo-title" title={card.title}>{card.title}</span>
        {!(confirm && (card.status ?? "pending") === "pending") && (
          <button className="holo-x" onClick={(e) => { e.stopPropagation(); remove(card.id); }} title="Dismiss">×</button>
        )}
      </header>
      <div className="holo-body"><Body card={card} /></div>
    </motion.section>
  );
}
