"use client";

import { gsap } from "gsap";
import { useEffect, useRef, useState } from "react";
import { motionAllowed } from "@/components/Motion";
import { serifFamily } from "./fonts";
import { money, tripDay, type TripOption } from "./travelData";

const ring = 2 * Math.PI * 66;

const codes: Record<string, string> = { Tokyo: "TYO", Seoul: "SEL", Singapore: "SIN" };

function Field({ label, value, wide = false }: { label: string; value: string; wide?: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, gridColumn: wide ? "span 2" : undefined }}>
      <span style={{ fontSize: 10, letterSpacing: "0.22em", textTransform: "uppercase", color: "#64748B" }}>{label}</span>
      <span style={{ fontSize: 18, fontWeight: 600, whiteSpace: "nowrap" }}>{value}</span>
    </div>
  );
}

export function TicketFace({ from, to, option, seat, passenger }: { from: string; to: string; option: TripOption; seat: string; passenger: string }) {
  const short = (place: string) => (option.kind === "flight" ? (codes[place] ?? place) : place);
  return (
    <div style={{ flex: 1, padding: "34px 40px", display: "flex", flexDirection: "column", justifyContent: "space-between", gap: 26, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 18 }}>
        <span style={{ fontFamily: serifFamily, fontSize: 72, lineHeight: 1 }}>{short(from)}</span>
        <svg width="56" height="20" viewBox="0 0 56 20" fill="none" stroke="#0F172A" strokeWidth="1.6" aria-hidden="true">
          <path d="M2 10h50M44 3l8 7-8 7" />
        </svg>
        <span style={{ fontFamily: serifFamily, fontSize: 72, lineHeight: 1 }}>{short(to)}</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 18 }}>
        <Field label={option.kind === "rail" ? "Train" : "Flight"} value={option.name} wide />
        <Field label="Date" value={tripDay} />
        <Field label={option.kind === "rail" ? "Departs" : "Boards"} value={option.depart} />
        <Field label="Seat" value={option.kind === "rail" ? `Car 7 · ${seat}` : seat} />
        <Field label="Passenger" value={passenger} />
        <Field label="Fare" value={money(option.price, option.currency)} />
        <Field label="Arrives" value={option.kind === "rail" ? option.arrive : `${option.arrive} local`} />
      </div>
    </div>
  );
}

export function HoldTicket({
  from,
  to,
  option,
  seat,
  passenger,
  holdMs = 1200,
  isBusy = false,
  onConfirm,
}: {
  from: string;
  to: string;
  option: TripOption;
  seat: string;
  passenger: string;
  holdMs?: number;
  isBusy?: boolean;
  onConfirm: () => void;
}) {
  const progress = useRef<SVGCircleElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [isDone, setDone] = useState(false);

  const release = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    if (progress.current && !isDone) gsap.to(progress.current, { strokeDashoffset: ring, duration: 0.25, ease: "power2.out" });
  };

  const press = () => {
    if (isDone || isBusy || timer.current) return;
    if (progress.current) gsap.fromTo(progress.current, { strokeDashoffset: ring }, { strokeDashoffset: 0, duration: motionAllowed() ? holdMs / 1000 : 0, ease: "none" });
    timer.current = setTimeout(() => {
      timer.current = null;
      setDone(true);
      onConfirm();
    }, holdMs);
  };

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return (
    <div style={{ display: "flex", width: "min(960px, 92vw)", borderRadius: 22, overflow: "hidden", background: "#F8F5EE", color: "#0F172A", boxShadow: "0 40px 90px rgba(2,6,23,0.55)" }}>
      <TicketFace from={from} to={to} option={option} seat={seat} passenger={passenger} />
      <div style={{ width: 240, flex: "none", borderLeft: "2px dashed #CBD5E1", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 14, padding: "28px 0" }}>
        <button
          type="button"
          aria-label="Hold to book"
          disabled={isDone || isBusy}
          onPointerDown={press}
          onPointerUp={release}
          onPointerLeave={release}
          onKeyDown={(event) => {
            if (event.key === " " || event.key === "Enter") {
              event.preventDefault();
              press();
            }
          }}
          onKeyUp={release}
          style={{ all: "unset", cursor: "pointer", width: 150, height: 150, borderRadius: 75, position: "relative", display: "flex", alignItems: "center", justifyContent: "center", touchAction: "none" }}
        >
          <svg width="150" height="150" viewBox="0 0 150 150" aria-hidden="true" style={{ position: "absolute", inset: 0 }}>
            <circle cx="75" cy="75" r="66" fill="none" stroke="#E2E8F0" strokeWidth="8" />
            <circle ref={progress} cx="75" cy="75" r="66" fill="none" stroke="#0F172A" strokeWidth="8" strokeLinecap="round" strokeDasharray={ring} strokeDashoffset={isDone ? 0 : ring} transform="rotate(-90 75 75)" />
          </svg>
          <span style={{ width: 110, height: 110, borderRadius: 55, background: "#0F172A", color: "#F8FAFC", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, letterSpacing: "0.22em" }}>{isDone || isBusy ? "BOOKING" : "HOLD"}</span>
        </button>
        
      </div>
    </div>
  );
}
