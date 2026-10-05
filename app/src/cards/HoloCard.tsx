import { createContext, useEffect, useRef, useState } from "react";

/** The card's maximize state, for bodies with their own expand control (music). One source of truth: the
 *  .holo-xl class is rendered by React here, so a re-render can never drop it while the body thinks it is big. */
export const MaxCtx = createContext<{ max: boolean; setMax: (b: boolean) => void } | null>(null);
import { motion } from "framer-motion";
import { useStore } from "../state/store";
import type { Card } from "../types";
import { WebAppCard } from "./WebAppCard";
import { CalendarCard, LaunchFilesCard, ConfirmCard, DocumentCard, DraftEditor, EmailList, EmailView, FilesCard, NoticeCard, ThreadView, acctTag } from "./Cards";
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
import { SupportBlueprintCard } from "./SupportCards";
import { CarListingsCard } from "./CarCards";
import { GarageCard, GarageDiagnosisCard, GaragePlanCard, GarageSearchCard } from "./GarageCards";
import { VideoAnalysisCard } from "./VideoCards";
import { EatsCartCard, EatsMenuCard, EatsStoresCard } from "./EatsCards";
import { TradeAlertCard, TradeDeskCard, TradeInsightsCard } from "./TradeCards";
import { BookingConfirmedCard, CarRentalsCard, FlightsCard, HotelsCard, ReservationsCard, RestaurantsCard } from "./TravelCards";
import { ArtifactCard, CodebaseCard, CodeChangesCard, CodeFileCard, GeneratingCard } from "./WorkspaceCards";
import { core } from "../ws/core";

const BODY: Record<string, (p: { card: Card }) => React.ReactElement> = {
  email_list: EmailList,
  email: EmailView,
  thread: ThreadView,
  draft: DraftEditor,
  confirm: ConfirmCard,
  calendar: CalendarCard,
  launch_files: LaunchFilesCard,
  webapp: WebAppCard,
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
  generating: GeneratingCard,
  codebase: CodebaseCard,
  code_file: CodeFileCard,
  code_changes: CodeChangesCard,
  support_blueprint: SupportBlueprintCard,
  car_listings: CarListingsCard,
  garage: GarageCard,
  eats_stores: EatsStoresCard,
  trade_desk: TradeDeskCard,
  trade_insights: TradeInsightsCard,
  trade_alert: TradeAlertCard,
  trade_order: TradeAlertCard,
  eats_menu: EatsMenuCard,
  eats_cart: EatsCartCard,
  garage_search: GarageSearchCard,
  garage_diagnosis: GarageDiagnosisCard,
  video_analysis: VideoAnalysisCard,
  garage_plan: GaragePlanCard,
  travel_flights: FlightsCard,
  travel_hotels: HotelsCard,
  travel_car_rentals: CarRentalsCard,
  travel_restaurants: RestaurantsCard,
  booking_confirmed: BookingConfirmedCard,
  travel_reservations: ReservationsCard,
};

const KIND_LABEL: Record<string, string> = {
  email_list: "MAIL", email: "MESSAGE", thread: "THREAD", draft: "DRAFT", confirm: "AUTHORIZE", calendar: "CALENDAR", launch_files: "FILES", webapp: "WEB APP",
  files: "DRIVE", document: "DOCUMENT", notice: "STATUS", "visual.chart": "ANALYSIS", "visual.stats": "TELEMETRY",
  "visual.table": "DATA", "visual.list": "INDEX", "visual.image": "IMAGE", "visual.link": "LINK", "visual.markdown": "BRIEF", "visual.map": "NAVIGATION", weather: "WEATHER", pylon_list: "PYLON", pylon_ticket: "TICKET", places: "PLACES", place: "PLACE", directions: "ROUTE", stock: "MARKETS", stock_compare: "COMPARE", market: "MARKETS", crypto: "CRYPTO", slack: "SLACK", sports_game: "SPORTS", sports_scoreboard: "SCORES", music: "MUSIC", video: "VIDEO", artifact: "FILE", codebase: "CODEBASE", code_file: "SOURCE", code_changes: "CHANGES", support_blueprint: "BLUEPRINT", car_listings: "LISTINGS", garage: "GARAGE", eats_stores: "UBER EATS", trade_desk: "TRADING DESK", trade_insights: "INTELLIGENCE", trade_alert: "MARKET ALERT", trade_order: "ORDER", trade_orders: "ORDERS", eats_menu: "MENU", eats_cart: "CART", garage_search: "RECORDS", garage_diagnosis: "DIAGNOSTICS", video_analysis: "VIDEO ANALYSIS", garage_plan: "BUILD PLAN", generating: "STUDIO", travel_flights: "FLIGHTS", travel_hotels: "HOTELS", travel_car_rentals: "RENTALS", travel_restaurants: "RESERVATIONS", booking_confirmed: "BOOKED", travel_reservations: "ITINERARY",
};

export function HoloCard({ card, index }: { card: Card; index: number }) {
  const remove = useStore((s) => s.removeCard);
  const focus = useStore((s) => s.focusCardId);
  const setStore = useStore((s) => s.set);
  const Body = BODY[card.kind];
  // Maximize grows THIS card in place (a fixed overlay via .holo-xl) rather than re-rendering it elsewhere,
  // so live content (players, maps, forms being typed into) keeps its state. Esc or a click outside restores.
  const [max, setMax] = useState(!!card.openMax);
  useEffect(() => { if (card.openMax) setMax(true); }, [card.openMax]);
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!max) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopImmediatePropagation(); setMax(false); } };
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      // Ignore clicks inside the card and inside portaled overlays (menus, lightboxes, toasts).
      if (ref.current?.contains(t) || (t as Element).closest?.(".lightbox, .toasts, .wb-scrim, [role=dialog], [role=menu]")) return;
      setMax(false);
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("pointerdown", onDown, true);
    return () => { window.removeEventListener("keydown", onKey, true); window.removeEventListener("pointerdown", onDown, true); };
  }, [max]);
  if (!Body) return null;
  const ownExpand = card.kind === "music"; // the music card has its own expand with a dedicated wide layout
  const confirm = card.kind === "confirm";
  const tag = acctTag(card.account);
  return (
    <motion.section
      layout
      initial={{ opacity: 0, x: 40, scale: 0.97, filter: "blur(6px)" }}
      animate={{ opacity: 1, x: 0, scale: 1, filter: "blur(0px)" }}
      exit={{ opacity: 0, x: 30, scale: 0.97, filter: "blur(6px)", transition: { duration: 0.2 } }}
      transition={{ type: "spring", stiffness: 260, damping: 28, delay: Math.min(index, 3) * 0.05 }}
      ref={ref}
      data-card-id={card.id}
      className={`holo ${max ? "holo-xl" : ""} ${confirm ? "holo-amber" : ""} ${focus === card.id ? "holo-focus" : ""} kind-${card.kind.replace(".", "-")}`}
      onClick={() => {
        setStore({ focusCardId: card.id });
        const a = card.data?.artifact;
        if (a) core.focus({ type: "file", id: a.id, filename: a.filename, kind: a.kind });  // "this" = the file he clicked
      }}
    >
      <span className="corner tl" /><span className="corner tr" /><span className="corner bl" /><span className="corner br" />
      <header className="holo-head">
        {card.parent && (
          <button className="holo-back" onClick={(e) => { e.stopPropagation(); core.back(card.id, max); }}
            title={`Back to ${card.parent.title}`}>‹ BACK</button>
        )}
        <span className="holo-kind">{KIND_LABEL[card.kind] ?? card.kind.toUpperCase()}</span>
        {tag && <span className={`acct acct-${card.account}`}>{tag}</span>}
        <span className="holo-title" title={card.title}>{card.title}</span>
        {!ownExpand && (
          <button className="holo-x holo-max" onClick={(e) => { e.stopPropagation(); setMax(!max); }} title={max ? "Restore (Esc)" : "Maximize"}>{max ? "⤡" : "⤢"}</button>
        )}
        {!(confirm && (card.status ?? "pending") === "pending") && (
          <button className="holo-x" onClick={(e) => { e.stopPropagation(); remove(card.id); }} title="Dismiss">×</button>
        )}
      </header>
      <div className="holo-body"><MaxCtx.Provider value={{ max, setMax }}><Body card={card} /></MaxCtx.Provider></div>
    </motion.section>
  );
}
