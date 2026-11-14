import { z } from "zod";
import { computerStates, type ClientSetup, type LoginSite, type SiteLogin } from "@/lib/setup";

export type SetupApi = {
  load(clientId: string): Promise<ClientSetup>;
  createComputer(clientId: string): Promise<ClientSetup>;
  addLogin(clientId: string, site: LoginSite, login: SiteLogin): Promise<ClientSetup>;
  checkLogins(clientId: string): Promise<ClientSetup>;
  setUseClaude(clientId: string, useClaude: boolean): Promise<boolean>;
};

const fallback = "Something went wrong. Try again in a minute.";

const setupSchema = z.object({
  clientId: z.string(),
  computer: z.enum(computerStates),
  logins: z.object({ supplier: z.boolean(), store: z.boolean() }),
  useClaude: z.boolean(),
});
const switchSchema = z.object({ clientId: z.string(), useClaude: z.boolean() });
const problemSchema = z.object({ error: z.string() });

type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

async function send(fetcher: Fetcher, path: string, method: "GET" | "POST" | "PUT", body?: unknown) {
  const response = await fetcher(path, {
    method,
    cache: "no-store",
    credentials: "same-origin",
    headers: body === undefined ? { accept: "application/json" } : { accept: "application/json", "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).catch(() => null);
  if (!response) throw new Error(fallback);
  const answer: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(problemSchema.safeParse(answer).data?.error ?? fallback);
  return answer;
}

function base(clientId: string) {
  return `/api/clients/${encodeURIComponent(clientId)}`;
}

export function createSetupApi(fetcher: Fetcher = (input, init) => fetch(input, init)): SetupApi {
  return {
    load: async (clientId) => setupSchema.parse(await send(fetcher, `${base(clientId)}/setup`, "GET")),
    createComputer: async (clientId) => setupSchema.parse(await send(fetcher, `${base(clientId)}/computer`, "POST")),
    addLogin: async (clientId, site, login) => setupSchema.parse(await send(fetcher, `${base(clientId)}/logins`, "POST", { [site]: login })),
    checkLogins: async (clientId) => setupSchema.parse(await send(fetcher, `${base(clientId)}/logins/check`, "POST")),
    setUseClaude: async (clientId, useClaude) => switchSchema.parse(await send(fetcher, `${base(clientId)}/settings`, "PUT", { useClaude })).useClaude,
  };
}

export const setupApi = createSetupApi();
