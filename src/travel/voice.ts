"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type RecognitionResults = { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> };

type Recognition = {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  addEventListener(type: "result", listener: (event: RecognitionResults) => void): void;
  addEventListener(type: "end" | "error", listener: () => void): void;
  start: () => void;
  stop: () => void;
};

type RecognitionConstructor = new () => Recognition;

function recognitionClass(): RecognitionConstructor | null {
  const scope = globalThis as unknown as { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor };
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null;
}

export function useVoice(onHeard: (text: string) => void) {
  const [isListening, setListening] = useState(false);
  const [partial, setPartial] = useState("");
  const recognition = useRef<Recognition | null>(null);
  const heard = useRef(onHeard);
  useEffect(() => {
    heard.current = onHeard;
  }, [onHeard]);

  const stop = useCallback(() => {
    recognition.current?.stop();
    recognition.current = null;
    setListening(false);
  }, []);

  const start = useCallback(() => {
    const Kind = recognitionClass();
    if (!Kind) return false;
    hush();
    const next = new Kind();
    next.lang = "en-US";
    next.interimResults = true;
    next.continuous = false;
    next.addEventListener("result", (event) => {
      const results = Array.from(event.results);
      const text = results.map((result) => result[0]?.transcript ?? "").join(" ").trim();
      setPartial(text);
      if (results.at(-1)?.isFinal && text) {
        heard.current(text);
        setPartial("");
      }
    });
    next.addEventListener("end", () => setListening(false));
    next.addEventListener("error", () => setListening(false));
    recognition.current = next;
    next.start();
    setListening(true);
    return true;
  }, []);

  useEffect(() => stop, [stop]);

  return { isListening, partial, start, stop };
}

const preferredVoices = [/ava.*(premium|enhanced)/i, /(premium|enhanced)/i, /google us english/i, /samantha/i, /ava/i, /google uk english female/i];

export function bestVoice(voices: readonly { name: string; lang: string }[]) {
  const english = voices.filter((voice) => voice.lang.toLowerCase().startsWith("en"));
  for (const pattern of preferredVoices) {
    const found = english.find((voice) => pattern.test(voice.name));
    if (found) return found;
  }
  return english[0] ?? null;
}

let playing: HTMLMediaElement | null = null;
let queue: Promise<unknown> = Promise.resolve();
let generation = 0;

export function hush() {
  generation += 1;
  queue = Promise.resolve();
  playing?.pause();
  playing = null;
  globalThis.speechSynthesis?.cancel();
}

const speakingListeners = new Set<(isSpeaking: boolean) => void>();

export function onSpeaking(listener: (isSpeaking: boolean) => void) {
  speakingListeners.add(listener);
  return () => {
    speakingListeners.delete(listener);
  };
}

function setSpeaking(isSpeaking: boolean) {
  for (const listener of speakingListeners) listener(isSpeaking);
}

function playToEnd(audio: HTMLMediaElement, signal?: AbortSignal) {
  return new Promise<boolean>((resolve) => {
    signal?.addEventListener("abort", () => audio.pause(), { once: true });
    audio.addEventListener("ended", () => resolve(true), { once: true });
    audio.addEventListener("pause", () => resolve(true), { once: true });
    audio.addEventListener("error", () => resolve(false), { once: true });
    audio.play().catch(() => resolve(false));
  });
}

export function speak(text: string, fetcher: typeof fetch = fetch): Promise<"elevenlabs" | "browser" | "skipped"> {
  const mine = generation;
  const audioReady = fetcher("/api/voice", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text }) })
    .then(async (reply) => (reply.ok ? URL.createObjectURL(await reply.blob()) : null))
    .catch(() => null);
  const turn = queue.then(async () => {
    if (mine !== generation) return "skipped" as const;
    const url = await audioReady;
    if (mine !== generation) return "skipped" as const;
    if (url && typeof Audio !== "undefined") {
      const audio = new Audio(url);
      playing = audio;
      setSpeaking(true);
      const played = await playToEnd(audio);
      setSpeaking(false);
      if (played) return "elevenlabs" as const;
    }
    await speakInBrowser(text);
    return "browser" as const;
  });
  queue = turn.catch(() => undefined);
  return turn;
}

function speakInBrowser(text: string) {
  const synth = globalThis.speechSynthesis;
  if (!synth || typeof SpeechSynthesisUtterance === "undefined") return Promise.resolve();
  synth.cancel();
  const line = new SpeechSynthesisUtterance(text);
  const voice = bestVoice(synth.getVoices());
  if (voice) line.voice = voice as SpeechSynthesisVoice;
  line.lang = voice?.lang ?? "en-US";
  line.rate = 1;
  line.pitch = 1;
  return new Promise<void>((resolve) => {
    line.addEventListener("start", () => setSpeaking(true));
    line.addEventListener("end", () => {
      setSpeaking(false);
      resolve();
    });
    line.addEventListener("error", () => resolve());
    synth.speak(line);
  });
}

export function speakFile(url: string, signal?: AbortSignal): Promise<"file" | "skipped"> {
  const mine = generation;
  const turn = queue.then(async () => {
    if (mine !== generation || signal?.aborted || typeof Audio === "undefined") return "skipped" as const;
    const audio = new Audio(url);
    playing = audio;
    setSpeaking(true);
    await playToEnd(audio, signal);
    setSpeaking(false);
    return "file" as const;
  });
  queue = turn.catch(() => undefined);
  return turn;
}

export function playMedia(element: HTMLMediaElement, signal?: AbortSignal): Promise<"media" | "skipped"> {
  const mine = generation;
  const turn = queue.then(async () => {
    if (mine !== generation || signal?.aborted) return "skipped" as const;
    playing = element;
    element.currentTime = 0;
    element.muted = false;
    setSpeaking(true);
    const played = await playToEnd(element, signal);
    setSpeaking(false);
    return played ? ("media" as const) : ("skipped" as const);
  });
  queue = turn.catch(() => undefined);
  return turn;
}
