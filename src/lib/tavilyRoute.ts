import { z } from "zod";
import { isSameOrigin, type RequestLike } from "./setupRoutes";
import { monthNames } from "./tripKnowledge";

export type TavilySettings = { key: string };

const places = { kyoto: "Kyoto", seoul: "Seoul", singapore: "Singapore" } as const;
const LONGEST_LINE = 180;

const bodySchema = z.strictObject({ destination: z.enum(["kyoto", "seoul", "singapore"]), month: z.enum(monthNames) });

const searchSchema = z.object({
  answer: z.string().nullish(),
  results: z.array(z.object({ title: z.string(), url: z.url(), content: z.string() })),
});

export function tavilySettings(env: Record<string, string | undefined>): TavilySettings | null {
  if (env.CLIENTOPS_TAVILY?.trim() !== "on") return null;
  const key = env.TAVILY_API_KEY?.trim();
  return key ? { key } : null;
}

function problem(error: string, status: number) {
  return Response.json({ error }, { status, headers: { "cache-control": "no-store" } });
}

function firstSentence(text: string) {
  const sentence = text.replaceAll(/\s+/g, " ").trim().split(/(?<=[.!?])\s/)[0] ?? "";
  return sentence.length <= LONGEST_LINE ? sentence : `${sentence.slice(0, LONGEST_LINE - 1).trimEnd()}…`;
}

function titleCase(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export async function handleTripInsight(request: RequestLike, deps: { fetcher: typeof fetch; settings: TavilySettings | null }) {
  if (!isSameOrigin(request)) return problem("Only the ClientOps page can do this.", 403);
  if (!deps.settings) return problem("Trip insights are not set up.", 503);
  const text = await request.text().catch(() => "");
  let body: unknown = null;
  try {
    body = JSON.parse(text);
  } catch {
    body = null;
  }
  const parsed = bodySchema.safeParse(body);
  if (!parsed.success) return problem("Say a place and a month.", 400);
  const query = `${places[parsed.data.destination]} in ${titleCase(parsed.data.month)}: weather and major events for visitors`;
  const reply = await deps
    .fetcher("https://api.tavily.com/search", {
      method: "POST",
      headers: { authorization: `Bearer ${deps.settings.key}`, "content-type": "application/json" },
      body: JSON.stringify({ query, topic: "general", search_depth: "basic", max_results: 3, include_answer: "basic" }),
    })
    .catch(() => null);
  const found = searchSchema.safeParse(reply?.ok ? await reply.json().catch(() => null) : null);
  const source = found.success ? found.data.results[0] : undefined;
  const line = found.success && source ? firstSentence(found.data.answer?.trim() || source.content) : "";
  if (!source || !line) return problem("The search service did not answer.", 502);
  return Response.json({ line, source: { title: source.title, url: source.url } }, { headers: { "cache-control": "no-store" } });
}
