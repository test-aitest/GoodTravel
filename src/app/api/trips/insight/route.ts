import { handleTripInsight, tavilySettings } from "@/lib/tavilyRoute";

export async function POST(request: Request) {
  return handleTripInsight(request, { fetcher: fetch, settings: tavilySettings(process.env) });
}
