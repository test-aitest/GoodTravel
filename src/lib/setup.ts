import { z } from "zod";

export const loginSites = ["supplier", "store"] as const;
export type LoginSite = (typeof loginSites)[number];

export const loginNames = {
  supplier: { username: "LP_SUPPLIER_USERNAME", password: "LP_SUPPLIER_PASSWORD" },
  store: { username: "LP_STORE_USERNAME", password: "LP_STORE_PASSWORD" },
} as const satisfies Record<LoginSite, { username: string; password: string }>;

export const allLoginNames = [
  loginNames.supplier.username,
  loginNames.supplier.password,
  loginNames.store.username,
  loginNames.store.password,
] as const;

export const computerStates = ["missing", "ready", "unavailable"] as const;
export type ComputerState = (typeof computerStates)[number];

export type ClientSetup = {
  clientId: string;
  computer: ComputerState;
  logins: Record<LoginSite, boolean>;
  useClaude: boolean;
};

export type WorkerAnswer = { computer: ComputerState; present: readonly string[] | null };

export type SetupWorker = {
  status(clientId: string, options?: { wake?: boolean }): Promise<WorkerAnswer>;
  prepare(clientId: string): Promise<WorkerAnswer>;
  putLogins(clientId: string, values: Readonly<Record<string, string>>): Promise<{ present: readonly string[] }>;
};

export class SetupWorkerError extends Error {
  override name = "SetupWorkerError";

  constructor(
    message: string,
    readonly problem: string,
  ) {
    super(message);
  }
}

const loginName = z.enum(allLoginNames);
const agentAnswerSchema = z.discriminatedUnion("computer", [
  z.object({
    clientId: z.string(),
    computer: z.enum(computerStates),
    logins: z.object({ present: z.array(loginName), missing: z.array(loginName) }).nullable(),
  }),
  z.object({ clientId: z.string(), computer: z.literal("failed"), problem: z.string() }),
]);

export function readAgentAnswer(clientId: string, raw: unknown): WorkerAnswer {
  const parsed = agentAnswerSchema.safeParse(raw);
  if (!parsed.success) throw new SetupWorkerError(`The worker's answer about ${clientId} was not understood.`, "unreadable");
  const answer = parsed.data;
  if (answer.clientId !== clientId) throw new SetupWorkerError(`The worker answered about ${answer.clientId}, not ${clientId}.`, "another_client");
  if (answer.computer === "failed") throw new SetupWorkerError(`${clientId}'s computer reported ${answer.problem}.`, answer.problem);
  return { computer: answer.computer, present: answer.logins?.present ?? null };
}

export function sitesAdded(present: readonly string[]): Record<LoginSite, boolean> {
  const has = (site: LoginSite) => present.includes(loginNames[site].username) && present.includes(loginNames[site].password);
  return { supplier: has("supplier"), store: has("store") };
}

const field = z.string().max(512).refine((value) => value.trim().length > 0);
const siteLogin = z.strictObject({ username: field, password: field });
export const loginFormSchema = z
  .strictObject({ supplier: siteLogin.optional(), store: siteLogin.optional() })
  .refine((form) => loginSites.some((site) => form[site] !== undefined));
export type LoginForm = z.infer<typeof loginFormSchema>;
export type SiteLogin = z.infer<typeof siteLogin>;

export function loginValues(form: LoginForm): Record<string, string> {
  return Object.fromEntries(
    loginSites.flatMap((site) => {
      const login = form[site];
      return login
        ? [
            [loginNames[site].username, login.username],
            [loginNames[site].password, login.password],
          ]
        : [];
    }),
  );
}

export function setupDone(setup: ClientSetup) {
  return (setup.computer === "ready" ? 1 : 0) + (setup.computer === "ready" && setup.logins.supplier && setup.logins.store ? 1 : 0);
}

export function isReady(setup: ClientSetup) {
  return setupDone(setup) === 2;
}
