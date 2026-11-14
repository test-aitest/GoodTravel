"use client";

import { useGSAP } from "@gsap/react";
import Image from "next/image";
import { gsap } from "gsap";
import { useEffect, useReducer, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";
import { motionAllowed } from "@/components/Motion";
import { followJob } from "@/console/jobsClient";
import { approve } from "@/lib/approvalsClient";
import { startTripBooking, startTripSearch } from "@/lib/tripsClient";
import { talkAboutTrip, type TripTalkContext, type TripTalkDecision } from "@/lib/tripTalkClient";
import { loadKnowledge, rememberTrip } from "@/lib/knowledgeClient";
import { emptyGraph, featuresOf, rank, seatPreference, type KnowledgeGraph, type RankedDestination } from "@/lib/tripKnowledge";
import { serifFamily } from "./fonts";
import { HoldTicket } from "./HoldTicket";
import { Label, WrittenLine } from "./Script";
import { SeatMap } from "./SeatMap";
import { Sphere } from "./Sphere";
import { destinations, highlights, money, playbackOptions, playbackSeats, ridePhotos, tripDate, tripFrom, type DestinationId, type Seat, type SeatSuggestion, type TripOption } from "./travelData";
import { initialTravelState, travelReducer, type TravelAction, type TravelState } from "./travelState";
import { speak, useVoice } from "./voice";
import { GuideVideo } from "./GuideVideo";
import { loadInsight, type Insight } from "./insight";

export type TravelMode = "live" | "playback";

const fill: CSSProperties = { position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" };
const shade = (amount: number): CSSProperties => ({ position: "absolute", inset: 0, background: `rgba(2,6,23,${amount})`, pointerEvents: "none" });
const passenger = "Haruka Mori";

function Backdrop({ photo, video, blur = 0, dim = 0.3 }: { photo: string; video?: string; blur?: number; dim?: number }) {
  const look: CSSProperties = { ...fill, filter: blur ? `blur(${blur}px)` : undefined, transform: blur ? "scale(1.08)" : undefined };
  return (
    <>
      {video ? (
        <video src={`${video}.mp4`} poster={photo} autoPlay muted loop playsInline aria-hidden="true" style={look} />
      ) : (
        <Image src={photo} alt="" fill priority sizes="100vw" style={look} />
      )}
      <div style={shade(dim)} />
    </>
  );
}

export function TopBar({ right }: { right: string }) {
  return <Label style={{ position: "absolute", right: 40, top: 32, zIndex: 5 }}>{right}</Label>;
}

function Headline({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <h1 style={{ position: "absolute", margin: 0, fontSize: 170, lineHeight: 0.85, zIndex: 4, pointerEvents: "none", ...style }}>
      <WrittenLine>{children}</WrittenLine>
    </h1>
  );
}

function Opening() {
  return (
    <>
      <Backdrop photo={ridePhotos.flight} video="/travel/video/plane-clouds" dim={0.3} />
      <TopBar right="" />
      <h1 aria-label="Every trip begins long before you board." style={{ position: "absolute", left: 80, right: 80, top: "16vh", margin: 0, fontSize: "min(150px, 12vw)", lineHeight: 1.02, fontWeight: 400 }}>
        <WrittenLine delay={0.3}>Every trip begins</WrittenLine>
        <WrittenLine delay={1.9} style={{ paddingLeft: "11%" }}>
          long before
        </WrittenLine>
        <WrittenLine delay={3.4} style={{ paddingLeft: "25%" }}>
          you board.
        </WrittenLine>
      </h1>
      <Label style={{ position: "absolute", left: 0, right: 0, bottom: 128, textAlign: "center", letterSpacing: "0.3em", color: "#E2E8F0" }}>Tap the sky to talk</Label>
    </>
  );
}

function Where({ ranked, onPick }: { ranked: readonly RankedDestination[]; onPick: (id: DestinationId) => void }) {
  const [hovered, setHovered] = useState<DestinationId | null>(null);
  const lines: Record<DestinationId, string> = { kyoto: "Kyoto · Rail · 2h 15", seoul: "Seoul · Air · 2h 35", singapore: "Singapore · Air · 7h 30" };
  const order = ranked.length > 0 ? ranked.map((entry) => destinations.find((destination) => destination.id === entry.destination)!).filter(Boolean) : destinations;
  const why = new Map(ranked.map((entry) => [entry.destination, entry.reasons.map((reason) => reason.label).join(" · ")]));
  return (
    <>
      <div style={{ position: "absolute", inset: 0, display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 3, background: "#020617" }}>
        {order.map((destination) => (
          <button key={destination.id} type="button" data-card="" aria-label={`${destination.name}, ${destination.line}`} onClick={() => onPick(destination.id)} onPointerEnter={() => setHovered(destination.id)} onPointerLeave={() => setHovered((current) => (current === destination.id ? null : current))} onFocus={() => setHovered(destination.id)} onBlur={() => setHovered(null)} style={{ all: "unset", cursor: "pointer", position: "relative", overflow: "hidden" }}>
            <Image src={destination.photo} alt="" fill priority sizes="34vw" style={{ ...fill, objectPosition: "60% 50%" }} />
            <GuideVideo name={`${destination.id}-intro`} poster={`/travel/guide/${destination.id}-guide.jpg`} place={destination.name} isActive={hovered === destination.id} style={{ left: "19%", bottom: 0, width: "62%", height: "62%", borderRadius: "18px 18px 0 0", zIndex: 1, opacity: hovered === destination.id ? 1 : 0, translate: hovered === destination.id ? "0 0" : "0 30%", transition: "opacity 320ms ease, translate 420ms cubic-bezier(.2,.8,.2,1)", pointerEvents: "none" }} />
            <Label style={{ position: "absolute", left: 24, bottom: 150, zIndex: 2 }}>{lines[destination.id]}</Label>
            {why.get(destination.id) ? <Label style={{ position: "absolute", left: 24, bottom: 128, color: "#FBBF24" }}>{why.get(destination.id)}</Label> : null}
          </button>
        ))}
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 380, background: "linear-gradient(180deg, rgba(2,6,23,0) 0%, rgba(2,6,23,0.65) 100%)", pointerEvents: "none" }} />
      <TopBar right="" />
      <Headline style={{ left: 0, right: 0, bottom: 200, textAlign: "center" }}>Where to next?</Headline>
    </>
  );
}

function RideCard({ option, isPick, tilt, onPick }: { option: TripOption; isPick: boolean; tilt: number; onPick: () => void }) {
  return (
    <button
      type="button"
      data-card=""
      aria-label={`${option.name}, ${option.depart} to ${option.arrive}, ${money(option.price, option.currency)}${isPick ? ", my pick" : ""}`}
      onClick={onPick}
      style={{ all: "unset", cursor: "pointer", position: "relative", width: isPick ? 360 : 290, height: isPick ? 440 : 360, borderRadius: 14, overflow: "hidden", rotate: `${tilt}deg`, boxShadow: `${isPick ? "0 0 0 2px #F59E0B, " : ""}0 30px 60px rgba(2,6,23,0.5)` }}
    >
      {option.kind === "flight" && isPick ? (
        <video src="/travel/video/plane-clouds.mp4" poster={ridePhotos.flight} autoPlay muted loop playsInline aria-hidden="true" style={fill} />
      ) : (
        <Image src={option.kind === "rail" ? ridePhotos.rail : "/travel/photos/cabin-window.jpg"} alt="" fill sizes="360px" style={fill} />
      )}
      {isPick ? <span style={{ position: "absolute", left: 16, top: 14, width: 10, height: 10, borderRadius: 5, background: "#F59E0B", boxShadow: "0 0 0 6px rgba(245,158,11,0.3)" }} /> : null}
      <span style={{ position: "absolute", left: 0, right: 0, bottom: 0, padding: 18, display: "flex", flexDirection: "column", gap: 4, background: "rgba(2,6,23,0.35)" }}>
        <span style={{ fontFamily: serifFamily, fontSize: isPick ? 40 : 34, lineHeight: 1 }}>{`${option.depart} → ${option.arrive}`}</span>
        <Label>{`${option.name.split(" ").slice(-2).join(" ")} · ${money(option.price, option.currency)}`}</Label>
      </span>
    </button>
  );
}

function Ride({ state, insight, isGuideDone, onGuideDone, onPick }: { state: TravelState; insight: Insight | null; isGuideDone: boolean; onGuideDone: () => void; onPick: (id: string) => void }) {
  const destination = destinations.find((candidate) => candidate.id === state.destination) ?? destinations[0]!;
  const options = isGuideDone ? (state.options ?? []) : [];
  return (
    <>
      <Backdrop photo={destination.photo} dim={0.34} />
      <GuideVideo key={destination.id} name={`${destination.id}-detail`} poster={`/travel/guide/${destination.id}-guide.jpg`} place={destination.name} isActive onDone={onGuideDone} style={{ left: "23vw", top: 120, width: 260, height: 380, rotate: "-3deg", zIndex: 3 }} />
      <ul aria-label={`Highlights of ${destination.name}`} style={{ position: "absolute", left: "calc(23vw - 128px)", top: 120, margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 10, zIndex: 3 }}>
        {highlights[destination.id].map((spot) => (
          <li key={spot.name} data-card="" style={{ position: "relative", width: 104, height: 120, borderRadius: 12, overflow: "hidden", boxShadow: "0 16px 30px rgba(2,6,23,0.45)" }}>
            <Image src={spot.photo} alt="" fill sizes="104px" style={{ objectFit: "cover" }} />
            <Label style={{ position: "absolute", left: 8, right: 8, bottom: 8, fontSize: 9, letterSpacing: "0.14em", color: "#F8FAFC", textShadow: "0 2px 8px rgba(2,6,23,0.8)" }}>{spot.name}</Label>
          </li>
        ))}
      </ul>
      {insight ? (
        <a href={insight.source.url} target="_blank" rel="noreferrer" style={{ position: "absolute", left: "23vw", top: 660, width: 300, zIndex: 3, color: "#F8FAFC", textDecoration: "none", display: "flex", flexDirection: "column", gap: 6 }}>
          <Label style={{ color: "#FBBF24" }}>Now in {destination.name}</Label>
          <span style={{ fontSize: 15, lineHeight: 1.4, textShadow: "0 2px 12px rgba(2,6,23,0.7)" }}>{insight.line}</span>
          <Label style={{ color: "#CBD5E1", fontSize: 10 }}>{insight.source.title}</Label>
        </a>
      ) : null}
      <TopBar right={`${tripFrom} → ${destination.name} · Sat 21 Nov`} />
      <Headline style={{ left: 36, bottom: "clamp(32px, calc(100vh - 664px), 120px)" }}>{destination.name}</Headline>
      <div style={{ position: "absolute", right: "6vw", top: 110, display: "flex", alignItems: "flex-start", gap: 28, zIndex: 3 }}>
        {options.map((option, index) => (
          <RideCard key={option.id} option={option} isPick={option.id === state.pick} tilt={index % 2 === 0 ? -4 : 5} onPick={() => onPick(option.id)} />
        ))}
      </div>
    </>
  );
}

function SeatScene({ state, onPick }: { state: TravelState; onPick: (seat: string) => void }) {
  const option = state.options?.find((candidate) => candidate.id === state.option);
  return (
    <>
      <Backdrop photo="/travel/photos/cabin-window.jpg" blur={14} dim={0.55} />
      <TopBar right={option ? `${option.name} · ${option.depart} → ${option.arrive}` : ""} />
      <div style={{ position: "absolute", left: 40, top: "34%", display: "flex", flexDirection: "column", gap: 8, zIndex: 4 }}>
        <Label style={{ color: "#FBBF24" }}>{state.seat === state.suggestion?.seat ? "My pick" : "Your choice"}</Label>
        <span style={{ fontSize: 110, lineHeight: 1.1 }}>
          <WrittenLine>your seat</WrittenLine>
        </span>
        <span style={{ fontFamily: serifFamily, fontSize: 64, lineHeight: 1 }}>{state.seat ?? ""}</span>
        <Label style={{ color: "#E2E8F0" }}>Tap it again to book</Label>
      </div>
      <div style={{ position: "absolute", left: "52%", top: "50%", translate: "-50% -52%", zIndex: 3 }}>
        {option && state.seats ? <SeatMap key={option.id} kind={option.kind} seats={state.seats} suggestion={state.suggestion} chosen={state.seat} onPick={onPick} /> : <Label>Reading the seats…</Label>}
      </div>
    </>
  );
}

function Hold({ state, onConfirm }: { state: TravelState; onConfirm: () => void }) {
  const option = state.options?.find((candidate) => candidate.id === state.option);
  const destination = destinations.find((candidate) => candidate.id === state.destination);
  return (
    <>
      <Backdrop photo="/travel/photos/cabin-window.jpg" dim={0.3} />
      <TopBar right={state.isConfirming ? "Booking on Hikari Travel" : "Held for 10 min · nothing booked yet"} />
      <div style={{ position: "absolute", left: "50%", top: "50%", translate: "-50% -54%", rotate: "-2deg", zIndex: 3 }}>
        {option && state.seat ? <HoldTicket from={tripFrom} to={destination?.name ?? ""} option={option} seat={state.seat} passenger={passenger} isBusy={state.isConfirming} onConfirm={onConfirm} /> : null}
      </div>
    </>
  );
}

function Booked({ state }: { state: TravelState }) {
  const option = state.options?.find((candidate) => candidate.id === state.option);
  const destination = destinations.find((candidate) => candidate.id === state.destination) ?? destinations[0]!;
  const body = option ? `${option.name}, ${tripFrom} → ${destination.name}, Sat 21 Nov, ${option.depart} → ${option.arrive}. Seat ${state.seat}. Reference ${state.reference}.` : "";
  const mail = `mailto:?subject=${encodeURIComponent(`Trip to ${destination.name}: ${option?.name ?? ""}, Sat 21 Nov`)}&body=${encodeURIComponent(body)}`;
  return (
    <>
      <Backdrop photo={destination.photo} dim={0.22} />
      <TopBar right="Confirmed on Hikari Travel" />
      <Headline style={{ left: 36, bottom: 120 }}>See you there</Headline>
      <Label style={{ position: "absolute", left: 48, bottom: 100, zIndex: 4 }}>{`Booked · Ref ${state.reference ?? ""} · Seat ${state.seat ?? ""} · 21 Nov ${option?.depart ?? ""}`}</Label>
      <div data-card="" style={{ position: "absolute", right: "9vw", top: 140, width: 300, height: 380, borderRadius: 18, background: "#F8F5EE", color: "#0F172A", rotate: "4deg", boxShadow: "0 40px 90px rgba(2,6,23,0.5)", padding: 28, boxSizing: "border-box", display: "flex", flexDirection: "column", justifyContent: "space-between", zIndex: 3 }}>
        <Label style={{ color: "#64748B", fontSize: 10 }}>Boarding pass</Label>
        <span style={{ fontFamily: serifFamily, fontSize: 56, lineHeight: 1 }}>
          {tripFrom}
          <br />
          {destination.name}
        </span>
        <span style={{ fontFamily: serifFamily, fontSize: 44, letterSpacing: "0.08em" }}>{state.reference}</span>
      </div>
      <a href={mail} aria-label="Email the trip from your own mail app" style={{ position: "absolute", left: "50%", bottom: 34, translate: "-50% 0", width: 64, height: 64, borderRadius: 32, background: "rgba(248,250,252,0.94)", display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 0 0 10px rgba(248,250,252,0.16)", zIndex: 30 }}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#0F172A" strokeWidth="1.8" aria-hidden="true">
          <rect x="3" y="5" width="18" height="14" rx="2" />
          <path d="M3 7l9 6 9-6" />
        </svg>
      </a>
    </>
  );
}

function usePlayback(state: TravelState, dispatch: (action: TravelAction) => void, isOn: boolean) {
  useEffect(() => {
    if (!isOn) return;
    if (state.scene === "ride" && state.destination && !state.options) {
      const timer = setTimeout(() => dispatch({ type: "optionsRead", options: playbackOptions[state.destination!] }), 700);
      return () => clearTimeout(timer);
    }
    if (state.scene === "seat" && state.option && !state.seats) {
      const read = playbackSeats[state.option];
      if (!read) return;
      const timer = setTimeout(() => dispatch({ type: "seatsRead", seats: read.seats, suggestion: read.suggestion }), 450);
      return () => clearTimeout(timer);
    }
    if (state.scene === "hold" && state.isConfirming) {
      const timer = setTimeout(() => dispatch({ type: "booked", reference: "KQ7MTR" }), 1200);
      return () => clearTimeout(timer);
    }
  }, [state.scene, state.destination, state.options, state.option, state.seats, state.isConfirming, dispatch, isOn]);
}

const travellerClient = "hinata";
const traveller = "sales-lead";

type ReadSeats = { seats: Seat[]; suggestion: SeatSuggestion };

function seatOf(seat: { id: string; position: Seat["position"]; exitRow: boolean; available: boolean }): Seat {
  return { ...seat, row: Number.parseInt(seat.id, 10), letter: seat.id.replace(/^\d+/, "") };
}

function firstWindow(seats: readonly Seat[]): SeatSuggestion {
  const seat = seats.find((candidate) => candidate.available && candidate.position === "window") ?? seats.find((candidate) => candidate.available);
  return { seat: seat?.id ?? "", reason: "window" };
}

export type TrailLine = { id: string; text: string };

const stepNames: Record<string, string> = { "sign-in": "Signed in", search: "Searched", seats: "Read the seats", suggest: "Picked a seat" };
const rungNames: Record<string, string> = { script: "saved steps, no AI", tree: "read the page", claude: "Claude", human: "you" };

function trailText(stepId: string, tookMs: number, rung: string | undefined, detail: string | undefined) {
  const how = rung ? rungNames[rung] ?? rung : detail ?? "";
  return [stepNames[stepId] ?? stepId, `${tookMs} ms`, how].filter(Boolean).join(" · ");
}

function useLive(state: TravelState, dispatch: (action: TravelAction) => void, isOn: boolean, onTrail: (line: TrailLine | null) => void) {
  const trailTo = useRef(onTrail);
  useEffect(() => {
    trailTo.current = onTrail;
  });
  const seatsByOption = useRef(new Map<string, ReadSeats>());
  const booking = useRef<{ jobId: string; approvalId: string | null } | null>(null);

  useEffect(() => {
    if (!isOn || state.scene !== "ride" || !state.destination || state.options) return;
    const destination = destinations.find((candidate) => candidate.id === state.destination);
    if (!destination) return;
    let isGone = false;
    let close = () => {};
    void startTripSearch(travellerClient, destination.name, tripDate).then((started) => {
      if (isGone) return;
      if ("error" in started) {
        dispatch({ type: "failed", message: started.error });
        return;
      }
      const rungs = new Map<string, string>();
      const begun = Date.now();
      trailTo.current(null);
      close = followJob(started.jobId, (frame) => {
        if (frame.kind !== "event") return;
        const event = frame.event;
        if (event.type === "job.created") trailTo.current({ id: "run", text: event.run.kind === "replay" ? "Replaying saved steps on your computer" : "Learning Hikari Travel on your computer" });
        if (event.type === "step.decided") rungs.set(event.stepId, event.rung);
        if (event.type === "step.done") trailTo.current({ id: event.stepId, text: trailText(event.stepId, event.tookMs, rungs.get(event.stepId), event.detail) });
        if (event.type === "trip.options") trailTo.current({ id: "total", text: `Ready in ${((Date.now() - begun) / 1000).toFixed(2)} s` });
        if (event.type === "trip.options") {
          for (const option of event.options) {
            const seats = option.seats.map(seatOf);
            seatsByOption.current.set(option.id, { seats, suggestion: option.suggestion ? { seat: option.suggestion.seat, reason: option.suggestion.reason } : firstWindow(seats) });
          }
          dispatch({ type: "optionsRead", options: event.options.map((option) => ({ id: option.id, name: option.name, kind: option.kind, depart: option.depart, arrive: option.arrive, price: option.price, currency: option.currency })) });
        }
        if (event.type === "job.failed") dispatch({ type: "failed", message: event.message ?? "The search stopped." });
      });
    });
    return () => {
      isGone = true;
      close();
    };
  }, [isOn, state.scene, state.destination, state.options, dispatch]);

  useEffect(() => {
    if (!isOn || state.scene !== "seat" || !state.option || state.seats) return;
    const read = seatsByOption.current.get(state.option);
    if (read) dispatch({ type: "seatsRead", seats: read.seats, suggestion: read.suggestion });
  }, [isOn, state.scene, state.option, state.seats, dispatch]);

  useEffect(() => {
    if (!isOn || state.scene !== "hold" || !state.option || !state.seat) return;
    let isGone = false;
    let close = () => {};
    booking.current = null;
    void startTripBooking(travellerClient, state.option, state.seat).then((started) => {
      if (isGone) return;
      if ("error" in started) {
        dispatch({ type: "failed", message: started.error });
        return;
      }
      booking.current = { jobId: started.jobId, approvalId: null };
      close = followJob(started.jobId, (frame) => {
        if (frame.kind !== "event") return;
        const event = frame.event;
        if (event.type === "approval.requested" && booking.current) booking.current.approvalId = event.approvalId;
        if (event.type === "trip.booked") dispatch({ type: "booked", reference: event.reference });
        if (event.type === "job.failed") dispatch({ type: "failed", message: event.message ?? "The booking stopped." });
      });
    });
    return () => {
      isGone = true;
      close();
    };
  }, [isOn, state.scene, state.option, state.seat, dispatch]);

  return async () => {
    const current = booking.current;
    if (!current?.approvalId) {
      dispatch({ type: "failed", message: "The seat is still being held. Try again in a moment." });
      return;
    }
    const answer = await approve(current.jobId, current.approvalId);
    if ("error" in answer) dispatch({ type: "failed", message: answer.error });
  };
}

const noSubscription = () => () => {};

function useModeFromAddress(fallback: TravelMode): TravelMode {
  const asked = useSyncExternalStore(noSubscription, () => new URLSearchParams(globalThis.location.search).get("mode") ?? "", () => "");
  return asked === "live" || asked === "playback" ? asked : fallback;
}

export function TravelApp({ mode: fallbackMode }: { mode: TravelMode }) {
  const mode = useModeFromAddress(fallbackMode);
  const [state, dispatch] = useReducer(travelReducer, initialTravelState);
  const [isOpening, setOpening] = useState(true);
  const [isTalking, setTalking] = useState(false);
  const [heard, setHeard] = useState("");
  const [typed, setTyped] = useState("");
  const stage = useRef<HTMLDivElement>(null);
  const spoken = useRef(state.lines.length);
  const latest = useRef(state);
  useEffect(() => {
    latest.current = state;
  });
  const [knowledge, setKnowledge] = useState<KnowledgeGraph>(emptyGraph);
  useEffect(() => {
    void loadKnowledge(travellerClient).then((known) => setKnowledge(known.graph));
  }, []);
  const youSaid = state.lines.filter((line) => line.from === "you").map((line) => line.text).join(" ");
  const ranked = rank(knowledge, featuresOf(youSaid, tripDate), traveller);
  const isTalkMode = mode === "live";

  const contextOf = (current: TravelState): TripTalkContext => {
    const option = current.options?.find((candidate) => candidate.id === current.option);
    const preference = seatPreference(knowledge, traveller);
    return {
      month: featuresOf(youSaid, tripDate).month,
      scene: current.scene,
      destination: current.destination,
      ranked: rank(knowledge, featuresOf(current.lines.filter((line) => line.from === "you").map((line) => line.text).join(" "), tripDate), traveller).map((entry) => ({ destination: entry.destination, score: entry.score, reasons: entry.reasons.map((reason) => reason.label) })),
      options: current.options ? current.options.map((candidate) => ({ id: candidate.id, name: candidate.name, kind: candidate.kind, depart: candidate.depart, arrive: candidate.arrive, price: candidate.price, currency: candidate.currency })) : null,
      suggestion: option && current.suggestion ? { option: option.id, seat: current.suggestion.seat, reason: current.suggestion.reason } : null,
      seatPreference: preference.position ? { position: preference.position, exitRow: preference.exitRow.likes } : null,
      chosen: current.option && current.seat ? { option: current.option, seat: current.seat } : null,
      history: current.lines.slice(-6).map((line) => ({ from: line.from, text: line.text })),
    };
  };

  const apply = (decision: TripTalkDecision) => {
    const current = latest.current;
    switch (decision.action) {
      case "search":
        if (decision.destination && decision.destination !== current.destination) dispatch({ type: "destinationPicked", destination: decision.destination });
        return;
      case "show-destinations":
        if (current.scene !== "where") dispatch({ type: "said", text: "go back" });
        return;
      case "choose-ride":
      case "show-seats":
        if (decision.option && decision.option !== current.option) dispatch({ type: "optionPicked", option: decision.option });
        return;
      case "choose-seat":
        if (decision.seat) dispatch({ type: "seatPicked", seat: decision.seat });
        return;
      case "show-ticket":
        if (decision.seat) dispatch({ type: "seatPicked", seat: decision.seat });
        dispatch({ type: "ticketAsked" });
        return;
      default:
        return;
    }
  };

  const [isThinking, setThinking] = useState(false);
  const talk = async (said: string, shown = true) => {
    setOpening(false);
    if (shown) {
      setHeard(said);
      dispatch({ type: "heard", text: said });
    }
    if (!isTalkMode) {
      dispatch({ type: "said", text: said });
      return;
    }
    const mentioned = featuresOf(said, tripDate).mentioned;
    if (mentioned.length === 1 && mentioned[0] !== latest.current.destination) dispatch({ type: "destinationPicked", destination: mentioned[0]! });
    setThinking(true);
    const decision = await talkAboutTrip(travellerClient, said, contextOf(latest.current));
    setThinking(false);
    if ("error" in decision) {
      dispatch({ type: "said", text: said });
      return;
    }
    dispatch({ type: "replied", text: decision.reply });
    if (!decision.speaker || decision.speaker === "worker") void speak(decision.reply);
    apply(decision);
  };

  const voice = useVoice((text) => {
    setTimeout(() => setTalking(false), 700);
    void talk(text);
  });

  const [guideDoneFor, setGuideDoneFor] = useState<DestinationId | null>(null);
  const askedAbout = useRef(new Set<string>());
  useEffect(() => {
    if (!isTalkMode) return;
    const key = state.scene === "ride" && state.options && guideDoneFor === state.destination ? `rides:${state.destination}` : state.scene === "seat" && state.seats ? `seats:${state.option}` : null;
    if (!key || askedAbout.current.has(key)) return;
    askedAbout.current.add(key);
    void talk(state.scene === "ride" ? "What do you recommend?" : "Which seat would you pick for me?", false);
  });

  const [insight, setInsight] = useState<Insight | null>(null);
  const insightFor = useRef<string | null>(null);
  useEffect(() => {
    if (!isTalkMode || !state.destination || insightFor.current === state.destination) return;
    const destination = state.destination;
    insightFor.current = destination;
    void loadInsight(destination, featuresOf("", tripDate).month).then((found) => {
      if (insightFor.current !== destination) return;
      setInsight(found);
      if (found) void speak(`Here in ${destinations.find((candidate) => candidate.id === destination)?.name ?? destination} right now: ${found.line}`);
    });
  });

  const remembered = useRef(false);
  useEffect(() => {
    if (state.scene !== "booked" || remembered.current) return;
    const option = state.options?.find((candidate) => candidate.id === state.option);
    const seat = state.seats?.find((candidate) => candidate.id === state.seat);
    if (!option || !seat || !state.destination) return;
    remembered.current = true;
    void rememberTrip(travellerClient, { traveller, date: tripDate, said: youSaid.slice(0, 400), destination: state.destination, ride: option.kind, seat: seat.position, exitRow: seat.exitRow }).then(() => loadKnowledge(travellerClient).then((known) => setKnowledge(known.graph)));
  });
  usePlayback(state, dispatch, mode === "playback");
  const confirmLive = useLive(state, dispatch, mode === "live", () => {});

  useEffect(() => {
    const fresh = state.lines.slice(spoken.current);
    spoken.current = state.lines.length;
    const reply = fresh.filter((line) => line.from === "worker").at(-1);
    if (reply && !isOpening && !isTalkMode) void speak(reply.text);
  }, [state.lines, isOpening, isTalkMode]);

  const sceneKey = isOpening ? "opening" : state.scene;
  useGSAP(
    () => {
      if (!motionAllowed()) return;
      gsap.fromTo("[data-scene]", { opacity: 0, scale: 1.03 }, { opacity: 1, scale: 1, duration: 0.55, ease: "power2.out" });
      gsap.from("[data-card]", { opacity: 0, y: 40, rotate: 0, duration: 0.6, ease: "power3.out", stagger: 0.08, delay: 0.1 });
    },
    { scope: stage, dependencies: [sceneKey, state.options === null] },
  );

  const toggleTalk = () => {
    if (isTalking) {
      voice.stop();
      setTalking(false);
      return;
    }
    setOpening(false);
    setHeard("");
    setTalking(true);
    voice.start();
  };

  const say = (text: string) => {
    if (!text.trim()) return;
    setTyped("");
    voice.stop();
    setTimeout(() => setTalking(false), 700);
    void talk(text);
  };

  const scene = (() => {
    if (isOpening) return <Opening />;
    switch (state.scene) {
      case "where":
        return <Where ranked={ranked} onPick={(destination) => void talk(destinations.find((candidate) => candidate.id === destination)?.name ?? destination)} />;
      case "ride":
        return <Ride state={state} insight={insight} isGuideDone={guideDoneFor === state.destination} onGuideDone={() => setGuideDoneFor(state.destination)} onPick={(option) => void talk(state.options?.find((candidate) => candidate.id === option)?.name ?? option)} />;
      case "seat":
        return (
          <SeatScene
            state={state}
            onPick={(seat) => {
              if (seat !== state.seat) dispatch({ type: "seatPicked", seat });
              void talk(seat === state.seat ? `Book seat ${seat}` : `Seat ${seat}`);
            }}
          />
        );
      case "hold":
        return (
          <Hold
            state={state}
            onConfirm={() => {
              dispatch({ type: "held" });
              if (mode === "live") void confirmLive();
            }}
          />
        );
      case "booked":
        return <Booked state={state} />;
    }
  })();

  return (
    <main ref={stage} style={{ position: "fixed", inset: 0, overflow: "hidden", background: "#020617", color: "#F8FAFC", fontFamily: "Inter, system-ui, sans-serif" }}>
      <div key={sceneKey} data-scene="" style={{ position: "absolute", inset: 0 }}>
        {scene}
      </div>
      {isTalking ? (
        <div style={{ position: "absolute", inset: 0, background: "rgba(2,6,23,0.72)", backdropFilter: "blur(14px)", zIndex: 20 }}>
          <h2 style={{ position: "absolute", left: 0, right: 0, top: "12%", margin: 0, textAlign: "center", fontSize: "min(96px, 8vw)", lineHeight: 1.1, fontWeight: 400 }}>
            <WrittenLine key={heard || voice.partial}>{heard || voice.partial || "Where to?"}</WrittenLine>
          </h2>
          <Label style={{ position: "absolute", left: 0, right: 0, bottom: "16%", textAlign: "center", letterSpacing: "0.3em", color: "#E2E8F0" }}>{voice.isListening ? "Listening · tap to stop" : isThinking ? "Thinking" : "Tap the sky to talk"}</Label>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              say(typed);
            }}
            style={{ position: "absolute", left: "50%", bottom: "8%", translate: "-50% 0", width: 420 }}
          >
            <label htmlFor="travel-say" style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>
              Or type it
            </label>
            <input id="travel-say" value={typed} onChange={(event) => setTyped(event.target.value)} placeholder="or type it" autoComplete="off" style={{ width: "100%", boxSizing: "border-box", background: "transparent", border: "none", borderBottom: "1px solid rgba(248,250,252,0.3)", color: "#F8FAFC", fontSize: 16, textAlign: "center", padding: "8px 0", outline: "none" }} />
          </form>
        </div>
      ) : null}
      {state.scene === "booked" && !isOpening ? null : <Sphere isOpen={isTalking} isListening={voice.isListening} onPress={toggleTalk} />}
    </main>
  );
}
