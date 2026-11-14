import { handleVoice, voiceSettings } from "@/lib/voiceRoute";

export async function POST(request: Request) {
  return handleVoice(request, { fetcher: fetch, settings: voiceSettings(process.env) });
}
