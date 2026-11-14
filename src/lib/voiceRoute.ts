import { z } from "zod";
import { isSameOrigin, type RequestLike } from "./setupRoutes";

export type VoiceSettings = { key: string; voiceId: string; model: string };

const defaultVoice = "EXAVITQu4vr4xnSDxMaL";
const defaultModel = "eleven_multilingual_v2";
const bodySchema = z.strictObject({ text: z.string().trim().min(1).max(500) });

export function voiceSettings(env: Record<string, string | undefined>): VoiceSettings | null {
  const key = env.ELEVENLABS_API_KEY?.trim();
  if (!key) return null;
  return { key, voiceId: env.ELEVENLABS_VOICE_ID?.trim() || defaultVoice, model: env.ELEVENLABS_MODEL?.trim() || defaultModel };
}

function problem(error: string, status: number) {
  return Response.json({ error }, { status, headers: { "cache-control": "no-store" } });
}

export async function handleVoice(request: RequestLike, deps: { fetcher: typeof fetch; settings: VoiceSettings | null }) {
  if (!isSameOrigin(request)) return problem("Only the ClientOps page can do this.", 403);
  if (!deps.settings) return problem("The voice is not set up.", 503);
  const text = await request.text().catch(() => "");
  let body: unknown = null;
  try {
    body = JSON.parse(text);
  } catch {
    body = null;
  }
  const parsed = bodySchema.safeParse(body);
  if (!parsed.success) return problem("Say a short line.", 400);
  const { key, voiceId, model } = deps.settings;
  const reply = await deps
    .fetcher(`https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(voiceId)}?output_format=mp3_44100_128`, {
      method: "POST",
      headers: { "xi-api-key": key, "content-type": "application/json", accept: "audio/mpeg" },
      body: JSON.stringify({ text: parsed.data.text, model_id: model }),
    })
    .catch(() => null);
  if (!reply?.ok) return problem("The voice service did not answer.", 502);
  return new Response(await reply.arrayBuffer(), { status: 200, headers: { "content-type": "audio/mpeg", "cache-control": "no-store" } });
}
