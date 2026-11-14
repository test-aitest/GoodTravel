import { describe, expect, test } from "bun:test";
import type { ClientSetup } from "@/lib/setup";
import { createSetupApi } from "./setupApi";

type Sent = { path: string; method: string; cache?: RequestCache; credentials?: RequestCredentials; contentType: string | null; body?: string };

function server(reply: (sent: Sent) => { status: number; body: unknown }) {
  const sent: Sent[] = [];
  const fetcher = (async (input: string | URL | Request, init?: RequestInit) => {
    const call: Sent = {
      path: String(input),
      method: init?.method ?? "GET",
      cache: init?.cache,
      credentials: init?.credentials,
      contentType: new Headers(init?.headers).get("content-type"),
      body: init?.body === undefined ? undefined : String(init.body),
    };
    sent.push(call);
    const answer = reply(call);
    return new Response(JSON.stringify(answer.body), { status: answer.status, headers: { "content-type": "application/json" } });
  }) as typeof fetch;
  return { sent, api: createSetupApi(fetcher) };
}

const ready: ClientSetup = { clientId: "kumo", computer: "ready", logins: { supplier: true, store: true }, useClaude: true };
const form = { supplier: { username: "u-1", password: "p-1" }, store: { username: "u-2", password: "p-2" } };

describe("the setup calls from the page", () => {
  test("each step goes to its own route on the same origin, never cached", async () => {
    const { sent, api } = server((call) => ({ status: 200, body: call.path.endsWith("/settings") ? { clientId: "kumo", useClaude: false } : ready }));

    expect(await api.load("kumo")).toEqual(ready);
    expect(await api.createComputer("kumo")).toEqual(ready);
    expect(await api.addLogin("kumo", "store", form.store)).toEqual(ready);
    expect(await api.checkLogins("kumo")).toEqual(ready);
    expect(await api.setUseClaude("kumo", false)).toBe(false);

    expect(sent.map((call) => `${call.method} ${call.path}`)).toEqual([
      "GET /api/clients/kumo/setup",
      "POST /api/clients/kumo/computer",
      "POST /api/clients/kumo/logins",
      "POST /api/clients/kumo/logins/check",
      "PUT /api/clients/kumo/settings",
    ]);
    expect(sent.every((call) => call.cache === "no-store" && call.credentials === "same-origin")).toBe(true);
    expect(JSON.parse(sent[2].body ?? "")).toEqual({ store: form.store });
    expect(sent[2].contentType).toBe("application/json");
    expect(JSON.parse(sent[4].body ?? "")).toEqual({ useClaude: false });
  });

  test("the server's plain message is what the page shows when a step fails", async () => {
    const { api } = server(() => ({ status: 502, body: { error: "Couldn't add the logins. Try again in a minute." } }));
    await expect(api.addLogin("kumo", "supplier", form.supplier)).rejects.toThrow("Couldn't add the logins. Try again in a minute.");
  });

  test("an answer that is not a setup is refused", async () => {
    const { api } = server(() => ({ status: 200, body: { ...ready, computer: "on fire" } }));
    await expect(api.load("kumo")).rejects.toThrow();
  });
});
