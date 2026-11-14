"use client";

import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { useRef, type CSSProperties, type ReactNode } from "react";
import { motionAllowed } from "@/components/Motion";
import { scriptFamily } from "./fonts";

const mask: CSSProperties = {
  WebkitMaskImage: "linear-gradient(90deg, #000 46%, transparent 54%)",
  maskImage: "linear-gradient(90deg, #000 46%, transparent 54%)",
  WebkitMaskSize: "260% 100%",
  maskSize: "260% 100%",
  WebkitMaskPosition: "100% 0",
  maskPosition: "100% 0",
};

export function WrittenLine({ children, delay = 0, style }: { children: ReactNode; delay?: number; style?: CSSProperties }) {
  const line = useRef<HTMLSpanElement>(null);
  useGSAP(
    () => {
      const node = line.current;
      if (!node) return;
      if (!motionAllowed()) {
        gsap.set(node, { WebkitMaskPosition: "0% 0", maskPosition: "0% 0" });
        return;
      }
      gsap.fromTo(node, { WebkitMaskPosition: "100% 0", maskPosition: "100% 0" }, { WebkitMaskPosition: "0% 0", maskPosition: "0% 0", duration: 1.6, ease: "power2.inOut", delay });
    },
    { dependencies: [children] },
  );
  return (
    <span ref={line} style={{ display: "block", fontFamily: scriptFamily, fontWeight: 400, color: "#F8FAFC", textShadow: "0 6px 30px rgba(2,6,23,0.5)", ...mask, ...style }}>
      {children}
    </span>
  );
}

export function Label({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return <span style={{ fontSize: 11, letterSpacing: "0.22em", textTransform: "uppercase", ...style }}>{children}</span>;
}
