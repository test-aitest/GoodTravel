"use client";

import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { useRef, useState, type CSSProperties } from "react";
import { motionAllowed } from "@/components/Motion";
import { seatLetters, seatRows, type RideKind, type Seat, type SeatSuggestion } from "./travelData";

export const VISIBLE_ROWS = 9;

const seatBase: CSSProperties = {
  all: "unset",
  boxSizing: "border-box",
  width: 44,
  height: 48,
  borderRadius: "12px 12px 8px 8px",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};

function lookOf(seat: Seat, suggested: boolean, chosen: boolean): CSSProperties {
  if (chosen) return { ...seatBase, background: "#2563EB", color: "#F8FAFC", boxShadow: "0 0 0 4px rgba(37,99,235,0.35)" };
  if (suggested) return { ...seatBase, background: "#F59E0B", color: "#1C1917" };
  if (!seat.available) return { ...seatBase, background: "#475569", color: "#64748B", cursor: "default" };
  if (seat.exitRow) return { ...seatBase, background: "#DBEAFE", color: "#1E3A8A" };
  return { ...seatBase, background: "#F8FAFC", color: "#475569" };
}

function nameOf(seat: Seat, suggested: boolean, chosen: boolean) {
  const parts = [`Seat ${seat.id}`, seat.position, ...(seat.exitRow ? ["exit row"] : []), seat.available ? "available" : "taken"];
  if (suggested) parts.push("suggested");
  if (chosen) parts.push("your choice");
  return parts.join(", ");
}

export function firstVisibleRow(focusRow: number, rows: number) {
  return Math.min(Math.max(1, focusRow - Math.floor(VISIBLE_ROWS / 2)), Math.max(1, rows - VISIBLE_ROWS + 1));
}

function aisleBefore(kind: RideKind) {
  return kind === "flight" ? "D" : "D";
}

export function SeatMap({ kind, seats, suggestion, chosen, onPick }: { kind: RideKind; seats: readonly Seat[]; suggestion: SeatSuggestion | null; chosen: string | null; onPick: (seat: string) => void }) {
  const scope = useRef<HTMLDivElement>(null);
  const rows = seatRows(kind);
  const letters = seatLetters(kind);
  const focus = Number.parseInt(chosen ?? suggestion?.seat ?? "1", 10) || 1;
  const [first, setFirst] = useState(() => firstVisibleRow(focus, rows));
  const byId = new Map(seats.map((seat) => [seat.id, seat]));
  const shown = Array.from({ length: Math.min(VISIBLE_ROWS, rows) }, (_, index) => first + index);
  const exitRows = [...new Set(seats.filter((seat) => seat.exitRow).map((seat) => seat.row))];
  const wingRows = kind === "flight" ? [11, 15] : null;
  const rowHeight = 56;

  useGSAP(
    () => {
      if (!motionAllowed()) return;
      gsap.from("[data-seat]", { opacity: 0, y: 8, duration: 0.3, ease: "power2.out", stagger: 0.004 });
      gsap.to("[data-suggested='true']", { boxShadow: "0 0 0 8px rgba(245,158,11,0.4)", repeat: -1, yoyo: true, duration: 0.8, ease: "sine.inOut" });
    },
    { scope, dependencies: [first, kind] },
  );

  const wingTop = wingRows ? (wingRows[0] - first) * rowHeight + 40 : null;
  const wingBottom = wingRows ? (wingRows[1] - first + 1) * rowHeight + 40 : null;
  const showWing = wingTop !== null && wingBottom !== null && wingBottom > 40 && wingTop < VISIBLE_ROWS * rowHeight + 40;

  return (
    <div ref={scope} style={{ position: "relative", display: "flex", alignItems: "center", gap: 48 }}>
      <div style={{ position: "relative", width: 560, height: VISIBLE_ROWS * rowHeight + 60, display: "flex", justifyContent: "center" }}>
        {showWing ? (
          <svg aria-hidden="true" width="560" height={VISIBLE_ROWS * rowHeight + 60} style={{ position: "absolute", inset: 0, overflow: "visible" }}>
            <path d={`M90 ${wingTop} L-60 ${(wingTop ?? 0) + 150} L-60 ${(wingTop ?? 0) + 200} L90 ${wingBottom}`} fill="rgba(148,163,184,0.4)" stroke="rgba(248,250,252,0.45)" strokeWidth="1.5" />
            <path d={`M470 ${wingTop} L620 ${(wingTop ?? 0) + 150} L620 ${(wingTop ?? 0) + 200} L470 ${wingBottom}`} fill="rgba(148,163,184,0.4)" stroke="rgba(248,250,252,0.45)" strokeWidth="1.5" />
          </svg>
        ) : null}
        <div role="group" aria-label={`Seat map, rows ${shown[0]} to ${shown.at(-1)}`} style={{ position: "relative", width: 380, height: "100%", borderRadius: 18, background: "rgba(15,23,42,0.92)", border: "2px solid rgba(248,250,252,0.6)", boxSizing: "border-box", padding: "10px 0", display: "flex", flexDirection: "column", alignItems: "center" }}>
          <div aria-hidden="true" style={{ display: "flex", gap: 8, height: 30, alignItems: "center", paddingLeft: 32 }}>
            {letters.map((letter) => (
              <span key={letter} style={{ width: 44, textAlign: "center", fontSize: 12, color: "#CBD5E1", marginLeft: letter === aisleBefore(kind) ? 20 : 0 }}>
                {letter}
              </span>
            ))}
          </div>
          {shown.map((row) => {
            const isExit = exitRows.includes(row);
            return (
              <div key={row} style={{ position: "relative", display: "flex", alignItems: "center", gap: 8, height: rowHeight }}>
                <span aria-hidden="true" style={{ width: 24, textAlign: "right", fontSize: 12, color: isExit ? "#FBBF24" : "#CBD5E1" }}>
                  {row}
                </span>
                {letters.map((letter) => {
                  const seat = byId.get(`${row}${letter}`);
                  if (!seat) return <span key={letter} style={{ width: 44 }} />;
                  const suggested = suggestion?.seat === seat.id;
                  const isChosen = chosen === seat.id;
                  return (
                    <button
                      key={seat.id}
                      type="button"
                      data-seat=""
                      data-suggested={suggested && !isChosen ? "true" : "false"}
                      aria-label={nameOf(seat, suggested, isChosen)}
                      aria-pressed={isChosen}
                      disabled={!seat.available}
                      onClick={() => onPick(seat.id)}
                      style={{ ...lookOf(seat, suggested, isChosen), marginLeft: letter === aisleBefore(kind) ? 20 : 0 }}
                    >
                      {seat.id}
                    </button>
                  );
                })}
                {isExit ? (
                  <>
                    <span aria-hidden="true" style={{ position: "absolute", left: -46, width: 0, height: 0, borderTop: "9px solid transparent", borderBottom: "9px solid transparent", borderRight: "14px solid #FBBF24" }} />
                    <span aria-hidden="true" style={{ position: "absolute", right: -46, width: 0, height: 0, borderTop: "9px solid transparent", borderBottom: "9px solid transparent", borderLeft: "14px solid #FBBF24" }} />
                  </>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
      <button
        type="button"
        aria-label={`Whole ${kind === "flight" ? "plane" : "car"}, showing rows ${shown[0]} to ${shown.at(-1)}. Tap to look at another part.`}
        onClick={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          const ratio = (event.clientY - box.top) / Math.max(1, box.height);
          setFirst(firstVisibleRow(Math.round(ratio * rows) || 1, rows));
        }}
        style={{ all: "unset", cursor: "pointer", display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}
      >
        <span style={{ fontSize: 11, letterSpacing: "0.22em", color: "#CBD5E1" }}>{kind === "flight" ? "WHOLE PLANE" : "WHOLE CAR"}</span>
        <svg width="110" height="420" viewBox="0 0 110 420" aria-hidden="true">
          {kind === "flight" ? (
            <>
              <path d="M14 140 L-8 175 L-8 192 L14 180 Z" fill="rgba(148,163,184,0.45)" />
              <path d="M96 140 L118 175 L118 192 L96 180 Z" fill="rgba(148,163,184,0.45)" />
              <path d="M55 4 C 28 4 14 36 14 62 L 14 376 C 14 398 32 414 55 416 C 78 414 96 398 96 376 L 96 62 C 96 36 82 4 55 4 Z" fill="rgba(15,23,42,0.9)" stroke="rgba(248,250,252,0.6)" strokeWidth="1.5" />
            </>
          ) : (
            <rect x="14" y="4" width="82" height="412" rx="14" fill="rgba(15,23,42,0.9)" stroke="rgba(248,250,252,0.6)" strokeWidth="1.5" />
          )}
          <rect x="22" y={62 + ((first - 1) / rows) * 300} width="66" height={(Math.min(VISIBLE_ROWS, rows) / rows) * 300} rx="5" fill="none" stroke="#F59E0B" strokeWidth="2.5" />
          {suggestion ? <circle cx="40" cy={62 + ((Number.parseInt(suggestion.seat, 10) - 0.5) / rows) * 300} r="4" fill="#F59E0B" /> : null}
        </svg>
        <span style={{ fontSize: 11, letterSpacing: "0.22em", color: "#E2E8F0" }}>{`ROWS ${shown[0]}–${shown.at(-1)}`}</span>
      </button>
    </div>
  );
}
