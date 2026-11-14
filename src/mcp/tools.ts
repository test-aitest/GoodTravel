import { McpServer, type CallToolResult } from "@modelcontextprotocol/server";
import { z } from "zod";
import { knownClients } from "../lib/clients";
import { jobSummarySchema, savedProcedureSummarySchema, waitingApprovalSchema } from "../lib/mcpViews";

export type AppLink = { appUrl: string; key: () => Promise<string>; fetch: typeof fetch };

type AppAnswer = { ok: true; body: unknown } | { ok: false; error: string };

const name = z.string().min(1);
const screen = z.url();
const clientList = knownClients.map((client) => `${client.id} (${client.name})`).join(", ");
const approvalRule = "Nothing is saved until a person approves it on the ClientOps screen; this server can't approve, undo or stop a job.";

const startedSchema = z.strictObject({ jobId: name, clientId: name, clientName: name, screen });
const statusSchema = jobSummarySchema.extend({ screen });
const waitingSchema = z.strictObject({ clientId: name, clientName: name, waiting: z.array(waitingApprovalSchema), screen });
const proceduresSchema = z.strictObject({ clientId: name, clientName: name, procedures: z.array(savedProcedureSummarySchema) });

const clientInput = z.object({ clientId: name.describe(`The client's id: ${clientList}`) });

function failed(error: string): CallToolResult {
  return { content: [{ type: "text", text: error }], isError: true };
}

function errorOf(body: unknown, status: number) {
  const error = typeof body === "object" && body !== null ? (body as { error?: unknown }).error : undefined;
  return typeof error === "string" ? error : `The ClientOps app answered ${status}.`;
}

async function askApp(app: AppLink, path: string, body?: unknown): Promise<AppAnswer> {
  const headers: Record<string, string> = { accept: "application/json", authorization: `Bearer ${await app.key()}` };
  if (body !== undefined) headers["content-type"] = "application/json";
  let response: Response;
  try {
    response = await app.fetch(`${app.appUrl}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return { ok: false, error: `The ClientOps app is not running at ${app.appUrl}. Start it with bun run dev, then try again.` };
  }
  const answer: unknown = await response.json().catch(() => null);
  return response.ok ? { ok: true, body: answer } : { ok: false, error: errorOf(answer, response.status) };
}

async function relay(app: AppLink, path: string, schema: z.ZodType<Record<string, unknown>>, extra: Record<string, unknown>, body?: unknown): Promise<CallToolResult> {
  const answer = await askApp(app, path, body);
  if (!answer.ok) return failed(answer.error);
  const parsed = schema.safeParse(typeof answer.body === "object" && answer.body !== null ? { ...answer.body, ...extra } : null);
  if (!parsed.success) return failed("The ClientOps app answered with something this server doesn't pass on.");
  return { content: [{ type: "text", text: JSON.stringify(parsed.data) }], structuredContent: parsed.data };
}

export function clientOpsMcpServer(link: AppLink) {
  const app = { ...link, appUrl: link.appUrl.replace(/\/+$/, "") };
  const onScreen = { screen: `${app.appUrl}/` };
  const server = new McpServer(
    { name: "clientops", version: "0.1.0" },
    { instructions: `ClientOps runs each client's operations on that client's own computer. ${approvalRule}` },
  );

  server.registerTool(
    "start_job",
    {
      title: "Start a job",
      description: `Ask a client's worker to do a job, the same as typing it on the ClientOps screen. ${approvalRule} The answer has the job id for job_status and the screen's link.`,
      inputSchema: z.object({
        clientId: name.describe(`The client's id: ${clientList}`),
        request: z.string().describe("What the job is, in plain words, up to 2,000 characters"),
      }),
      outputSchema: startedSchema,
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
    },
    ({ clientId, request }) => relay(app, "/api/mcp/jobs", startedSchema, onScreen, { clientId, request }),
  );

  server.registerTool(
    "job_status",
    {
      title: "Job status",
      description: `Where a job is: its steps, whether it waits for a person's OK, and how it ended. ${approvalRule}`,
      inputSchema: z.object({ jobId: name.describe("The job id that start_job gave") }),
      outputSchema: statusSchema,
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    ({ jobId }) => relay(app, `/api/mcp/jobs/${encodeURIComponent(jobId)}`, statusSchema, onScreen),
  );

  server.registerTool(
    "waiting_approvals",
    {
      title: "Jobs waiting for approval",
      description: `A client's jobs that are waiting for a person's OK, with the link to the ClientOps screen where they approve. ${approvalRule}`,
      inputSchema: clientInput,
      outputSchema: waitingSchema,
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    ({ clientId }) => relay(app, `/api/mcp/clients/${encodeURIComponent(clientId)}/approvals`, waitingSchema, onScreen),
  );

  server.registerTool(
    "saved_procedures",
    {
      title: "Saved steps",
      description: "The steps a client's worker has learned and saved, by version, step count and expected replay time.",
      inputSchema: clientInput,
      outputSchema: proceduresSchema,
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    ({ clientId }) => relay(app, `/api/mcp/clients/${encodeURIComponent(clientId)}/procedures`, proceduresSchema, {}),
  );

  return server;
}
