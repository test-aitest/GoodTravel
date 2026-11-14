import { describe, expect, test } from "bun:test";
import { handleVoice, voiceSettings } from "./voiceRoute";
import type { RequestLike } from "./setupRoutes";

function post(body: unknown, site = "same-origin"): RequestLike {
  const sent = new Headers({ origin: "http://app.test", host: "app.test", "sec-fetch-site": site });
  return { headers: { get: (name) => sent.get(name) }, text: async () => JSON.stringify(body) };
}

describe("the worker's voice from ElevenLabs", () => {
  test("sends only the line to speak, with the key in the header, and returns the audio", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const fetcher = (async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init: init ?? {} });
      return new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { "content-type": "audio/mpeg" } });
    }) as typeof fetch;
    const reply = await handleVoice(post({ text: "Booked. See you there." }), { fetcher, settings: { key: "test-key", voiceId: "voice-1", model: "model-1" } });
    expect(reply.status).toBe(200);
    expect(reply.headers.get("content-type")).toBe("audio/mpeg");
    expect(new Uint8Array(await reply.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("https://api.elevenlabs.io/v1/text-to-speech/voice-1?output_format=mp3_44100_128");
    expect(new Headers(calls[0]?.init.headers).get("xi-api-key")).toBe("test-key");
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({ text: "Booked. See you there.", model_id: "model-1" });
  });

  test("without a key, or from another site, or with an odd body, it says no and the page falls back to the browser voice", async () => {
    const fetcher = (async () => new Response("never")) as unknown as typeof fetch;
    expect((await handleVoice(post({ text: "Hi" }), { fetcher, settings: null })).status).toBe(503);
    expect((await handleVoice(post({ text: "Hi" }, "cross-site"), { fetcher, settings: { key: "k", voiceId: "v", model: "m" } })).status).toBe(403);
    expect((await handleVoice(post({ text: "x".repeat(501) }), { fetcher, settings: { key: "k", voiceId: "v", model: "m" } })).status).toBe(400);
  });

  test("a failure at ElevenLabs is a 502 without its body", async () => {
    const fetcher = (async () => new Response("quota exceeded for account 123", { status: 401 })) as unknown as typeof fetch;
    const reply = await handleVoice(post({ text: "Hi" }), { fetcher, settings: { key: "k", voiceId: "v", model: "m" } });
    expect(reply.status).toBe(502);
    expect(await reply.text()).not.toContain("123");
  });

  test("settings come from the server environment, with the natural-sounding default model", () => {
    expect(voiceSettings({})).toBeNull();
    expect(voiceSettings({ ELEVENLABS_API_KEY: "k" })).toEqual({ key: "k", voiceId: "EXAVITQu4vr4xnSDxMaL", model: "eleven_multilingual_v2" });
    expect(voiceSettings({ ELEVENLABS_API_KEY: "k", ELEVENLABS_VOICE_ID: "v", ELEVENLABS_MODEL: "m" })).toEqual({ key: "k", voiceId: "v", model: "m" });
  });
});
