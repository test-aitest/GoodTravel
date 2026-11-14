type Answer = { clients?: { clientId: string; stopped: number; forgotten: number; sites: string }[]; error?: string } | null;

export async function requestDemoReset(appUrl: string): Promise<{ ok: boolean; lines: string[] }> {
  const response = await fetch(new URL("/api/dev/demo/reset", appUrl), {
    method: "POST",
    headers: { origin: new URL(appUrl).origin, "sec-fetch-site": "same-origin" },
  }).catch(() => null);
  if (!response) return { ok: false, lines: [`The app is not answering at ${appUrl}. Start it first.`] };
  const body = (await response.json().catch(() => null)) as Answer;
  if (!response.ok || !body?.clients) return { ok: false, lines: [body?.error ?? `The app answered ${response.status}.`] };
  return {
    ok: true,
    lines: ["The demo is back at the start.", ...body.clients.map((client) => `${client.clientId}: stopped ${client.stopped}, forgot ${client.forgotten}, sites ${client.sites}, daily run off`)],
  };
}

if (import.meta.main) {
  const outcome = await requestDemoReset(process.env.CLIENTOPS_APP_URL || "http://localhost:3000");
  for (const line of outcome.lines) (outcome.ok ? console.log : console.error)(line);
  process.exit(outcome.ok ? 0 : 1);
}
