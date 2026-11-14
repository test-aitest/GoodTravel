"use client";

import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { useRef } from "react";
import { motionAllowed } from "@/components/Motion";

const small = 76;
const large = 300;

const layer = { position: "absolute", inset: "-25%", pointerEvents: "none" } as const;

export function Sphere({ isOpen, isListening, onPress }: { isOpen: boolean; isListening: boolean; onPress: () => void }) {
  const button = useRef<HTMLButtonElement>(null);
  const swirl = useRef<HTMLSpanElement>(null);
  const swirlBack = useRef<HTMLSpanElement>(null);

  useGSAP(() => {
    if (!motionAllowed()) return;
    gsap.to(swirl.current, { rotation: 360, duration: 14, ease: "none", repeat: -1 });
    gsap.to(swirlBack.current, { rotation: -360, duration: 19, ease: "none", repeat: -1 });
  }, []);

  useGSAP(
    () => {
      const node = button.current;
      if (!node) return;
      const size = isOpen ? large : small;
      const target = {
        width: size,
        height: size,
        bottom: isOpen ? "calc(50% - 150px)" : 34,
        boxShadow: isOpen ? "0 0 0 40px rgba(147,197,253,0.10), 0 30px 120px rgba(37,99,235,0.7)" : "0 0 0 9px rgba(147,197,253,0.16), 0 14px 44px rgba(37,99,235,0.6)",
      };
      if (!motionAllowed()) {
        gsap.set(node, target);
        return;
      }
      gsap.to(node, { ...target, duration: 0.7, ease: isOpen ? "expo.out" : "power3.inOut" });
    },
    { dependencies: [isOpen] },
  );

  useGSAP(
    () => {
      if (!button.current || !motionAllowed()) return;
      gsap.killTweensOf(button.current, "scale");
      if (isListening) gsap.fromTo(button.current, { scale: 1 }, { scale: 1.06, duration: 0.8, ease: "sine.inOut", repeat: -1, yoyo: true });
      else gsap.to(button.current, { scale: 1, duration: 0.3 });
    },
    { dependencies: [isListening] },
  );

  return (
    <button
      ref={button}
      type="button"
      aria-label={isOpen ? "Stop talking" : "Talk to the worker"}
      aria-pressed={isOpen}
      onClick={onPress}
      style={{ all: "unset", cursor: "pointer", position: "absolute", left: "50%", bottom: 34, width: small, height: small, translate: "-50% 0", borderRadius: "50%", overflow: "hidden", zIndex: 30, boxShadow: "0 0 0 9px rgba(147,197,253,0.16), 0 14px 44px rgba(37,99,235,0.6)" }}
    >
      <span style={{ position: "absolute", inset: 0, background: "radial-gradient(circle at 50% 85%, #1D4ED8 0%, #2563EB 30%, #60A5FA 58%, #BAE6FD 82%, #F0F9FF 100%)" }} />
      <span ref={swirl} style={{ ...layer, background: "radial-gradient(ellipse 45% 30% at 30% 35%, rgba(255,255,255,0.95) 0%, rgba(255,255,255,0) 100%), radial-gradient(ellipse 35% 22% at 68% 58%, rgba(224,242,254,0.85) 0%, rgba(224,242,254,0) 100%)", filter: "blur(6px)" }} />
      <span ref={swirlBack} style={{ ...layer, background: "radial-gradient(ellipse 30% 40% at 60% 30%, rgba(186,230,253,0.8) 0%, rgba(186,230,253,0) 100%), radial-gradient(ellipse 40% 25% at 35% 70%, rgba(59,130,246,0.7) 0%, rgba(59,130,246,0) 100%)", filter: "blur(8px)" }} />
      <span style={{ position: "absolute", inset: 0, borderRadius: "50%", background: "radial-gradient(circle at 32% 26%, rgba(255,255,255,0.45) 0%, rgba(255,255,255,0) 34%), radial-gradient(circle at 50% 50%, rgba(0,0,0,0) 64%, rgba(30,58,138,0.28) 100%)" }} />
    </button>
  );
}
