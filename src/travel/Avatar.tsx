"use client";

import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import Image from "next/image";
import { useRef, type CSSProperties } from "react";
import { motionAllowed } from "@/components/Motion";
import { Label } from "./Script";

export function GuideCard({ src, place, isSpeaking, style }: { src: string; place: string; isSpeaking: boolean; style?: CSSProperties }) {
  const card = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      if (!card.current || !motionAllowed()) return;
      gsap.killTweensOf(card.current);
      if (isSpeaking) gsap.to(card.current, { scale: 1.02, duration: 0.9, ease: "sine.inOut", repeat: -1, yoyo: true });
      else gsap.to(card.current, { scale: 1, duration: 0.5, ease: "power2.out" });
    },
    { dependencies: [isSpeaking] },
  );

  return (
    <div
      ref={card}
      data-card=""
      role="img"
      aria-label={`Your local guide in ${place}`}
      style={{
        position: "absolute",
        width: 260,
        height: 380,
        borderRadius: 18,
        overflow: "hidden",
        rotate: "-3deg",
        boxShadow: isSpeaking ? "0 0 0 3px #93C5FD, 0 0 60px rgba(147,197,253,0.55), 0 30px 60px rgba(2,6,23,0.5)" : "0 30px 60px rgba(2,6,23,0.5)",
        transition: "box-shadow 300ms ease",
        ...style,
      }}
    >
      <Image src={src} alt="" fill sizes="260px" priority style={{ objectFit: "cover" }} />
      <span style={{ position: "absolute", left: 14, top: 12, display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ width: 8, height: 8, borderRadius: 4, background: isSpeaking ? "#22C55E" : "#E2E8F0", boxShadow: isSpeaking ? "0 0 0 5px rgba(34,197,94,0.3)" : "none" }} />
        <Label style={{ color: "#F8FAFC", textShadow: "0 2px 8px rgba(2,6,23,0.6)" }}>{`Live from ${place}`}</Label>
      </span>
    </div>
  );
}
