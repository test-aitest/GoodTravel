"use client";

import { HStack, VStack } from "@astryxdesign/core/Stack";
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { CustomEase } from "gsap/CustomEase";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

gsap.registerPlugin(useGSAP, CustomEase);

export type MotionKind = "enter" | "view" | "move" | "leave";

const tokens: Record<MotionKind, readonly [duration: string, ease: string]> = {
  enter: ["--duration-medium-min", "--ease-entrance"],
  view: ["--duration-fast", "--ease-standard"],
  move: ["--duration-medium", "--ease-move"],
  leave: ["--duration-medium-min", "--ease-exit"],
};

const eases = new Map<string, string>();

function seconds(value: string) {
  const amount = Number.parseFloat(value);
  if (!Number.isFinite(amount)) return 0;
  return value.trim().endsWith("ms") ? amount / 1000 : amount;
}

function easeOf(token: string, value: string) {
  const points = /cubic-bezier\(([^)]+)\)/.exec(value)?.[1]?.replace(/\s+/g, "");
  if (!points) return "power2.out";
  const key = `${token}:${points}`;
  const known = eases.get(key);
  if (known) return known;
  const id = `astryx${token}${eases.size}`;
  CustomEase.create(id, points);
  eases.set(key, id);
  return id;
}

export function timing(kind: MotionKind) {
  const style = getComputedStyle(document.documentElement);
  const [duration, ease] = tokens[kind];
  return { duration: seconds(style.getPropertyValue(duration)), ease: easeOf(ease, style.getPropertyValue(ease)) };
}

export function motionAllowed() {
  return !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

const Settled = createContext<RefObject<boolean> | null>(null);

function useSettledFlag() {
  const settled = useRef(false);
  useEffect(() => {
    settled.current = true;
    return () => {
      settled.current = false;
    };
  }, []);
  return settled;
}

export function useHasSettled() {
  return useContext(Settled);
}

export function MotionScope({ children, fade = false }: { children: ReactNode; fade?: boolean }) {
  const ref = useRef<HTMLElement>(null);
  const parent = useContext(Settled);
  const settled = useSettledFlag();
  useGSAP(
    () => {
      if (!fade || !parent?.current || !ref.current || !motionAllowed()) return;
      gsap.from(ref.current, { autoAlpha: 0, ...timing("view"), clearProps: "opacity,visibility" });
    },
    { scope: ref },
  );
  return (
    <Settled.Provider value={settled}>
      <VStack ref={ref} width="100%" height="100%" data-motion={fade ? "view" : undefined}>
        {children}
      </VStack>
    </Settled.Provider>
  );
}

export function Entrance({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  const scope = useContext(Settled);
  useGSAP(
    () => {
      if (!scope?.current || !ref.current || !motionAllowed()) return;
      gsap.from(ref.current, { autoAlpha: 0, y: 8, ...timing("enter"), clearProps: "opacity,visibility,transform" });
    },
    { scope: ref },
  );
  return (
    <VStack ref={ref} width="100%" data-motion="enter">
      {children}
    </VStack>
  );
}

export function useKeptWhileLeaving<T>(value: T | null) {
  const [current, setCurrent] = useState(value);
  const [leaving, setLeaving] = useState<T | null>(null);
  if (value !== current) {
    setCurrent(value);
    setLeaving(value === null ? current : null);
  }
  return { shown: value ?? leaving, isLeaving: value === null && leaving !== null, finish: () => setLeaving(null) };
}

export function WidthReveal({
  children,
  isLeaving,
  onLeft,
  name,
}: {
  children: ReactNode;
  isLeaving: boolean;
  onLeft: () => void;
  name: string;
}) {
  const ref = useRef<HTMLElement>(null);
  const scope = useContext(Settled);
  useGSAP(
    () => {
      const element = ref.current;
      if (!element) return;
      if (isLeaving) {
        if (!motionAllowed()) {
          onLeft();
          return;
        }
        gsap.to(element, { width: 0, overflow: "hidden", ...timing("leave"), onComplete: onLeft });
        return;
      }
      if (!scope?.current || !motionAllowed()) return;
      gsap.from(element, { width: 0, overflow: "hidden", ...timing("move"), clearProps: "width,overflow" });
    },
    { dependencies: [isLeaving], scope: ref },
  );
  return (
    <HStack ref={ref} height="100%" data-motion={name}>
      {children}
    </HStack>
  );
}

export function MotionRoot({ children }: { children: ReactNode }) {
  const settled = useSettledFlag();
  return <Settled.Provider value={settled}>{children}</Settled.Provider>;
}

export function SideDrawer({ isOpen, children }: { isOpen: boolean; children: ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  const shown = useRef(isOpen);
  useGSAP(
    () => {
      const element = ref.current;
      if (!element) return;
      if (shown.current === isOpen) {
        if (!isOpen) gsap.set(element, { width: 0, overflow: "hidden" });
        return;
      }
      shown.current = isOpen;
      if (!motionAllowed()) {
        gsap.set(element, isOpen ? { clearProps: "width,overflow" } : { width: 0, overflow: "hidden" });
        return;
      }
      if (isOpen) {
        gsap.set(element, { clearProps: "width" });
        const width = element.getBoundingClientRect().width;
        gsap.fromTo(element, { width: 0, overflow: "hidden" }, { width, ...timing("move"), clearProps: "width,overflow" });
        return;
      }
      gsap.to(element, { width: 0, overflow: "hidden", ...timing("leave") });
    },
    { dependencies: [isOpen], scope: ref },
  );
  return (
    <HStack ref={ref} height="100%" inert={!isOpen} data-motion="sidebar">
      {children}
    </HStack>
  );
}
