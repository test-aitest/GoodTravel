"use client";

import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import Image from "next/image";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { motionAllowed } from "@/components/Motion";
import { Label } from "./Script";
import { playMedia, speakFile } from "./voice";

export function GuideVideo({ name, poster, place, isActive, onDone, style }: { name: string; poster: string; place: string; isActive: boolean; onDone?: () => void; style?: CSSProperties }) {
  const finished = useRef(onDone);
  useEffect(() => {
    finished.current = onDone;
  });
  const video = useRef<HTMLVideoElement>(null);
  const liveDot = useRef<HTMLSpanElement>(null);
  useGSAP(() => {
    if (!motionAllowed()) return;
    gsap.to(liveDot.current, { opacity: 0.2, duration: 0.8, ease: "sine.inOut", repeat: -1, yoyo: true });
  }, []);
  const [hasVideo, setHasVideo] = useState(true);
  useEffect(() => {
    const element = video.current;
    if (!isActive) {
      if (element && !element.paused) element.pause();
      return;
    }
    const calledOff = new AbortController();
    const turn = hasVideo && element ? playMedia(element, calledOff.signal) : speakFile(`/travel/guide/${name}.mp3`, calledOff.signal);
    void turn.then(() => {
      if (!calledOff.signal.aborted) finished.current?.();
    });
    return () => calledOff.abort();
  }, [isActive, hasVideo, name]);

  return (
    <div role="img" aria-label={`Your local guide in ${place}`} style={{ position: "absolute", borderRadius: 18, overflow: "hidden", boxShadow: "0 0 0 2px rgba(147,197,253,0.6), 0 30px 60px rgba(2,6,23,0.5)", ...style }}>
      {hasVideo ? (
        <video ref={video} src={`/travel/guide/${name}.mp4`} poster={poster} playsInline preload="auto" onError={() => setHasVideo(false)} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }}>
          <track kind="captions" src={`/travel/guide/${name}.vtt`} srcLang="en" label="English" default />
        </video>
      ) : (
        <Image src={poster} alt="" fill sizes="300px" style={{ objectFit: "cover" }} />
      )}
      <span style={{ position: "absolute", left: 14, top: 12, display: "flex", alignItems: "center", gap: 8 }}>
        <span ref={liveDot} data-live-dot="" style={{ width: 8, height: 8, borderRadius: 4, background: "#22C55E", boxShadow: "0 0 0 5px rgba(34,197,94,0.3)" }} />
        <Label style={{ color: "#F8FAFC", textShadow: "0 2px 8px rgba(2,6,23,0.6)" }}>{`Live from ${place}`}</Label>
      </span>
    </div>
  );
}
