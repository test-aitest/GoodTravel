import { z } from "zod";
import { AgentFieldError } from "./agentfieldClient";
import { isTerminal } from "./agentfieldRecords";
import { agentFieldAnswer, problem, readBody, routeCopy, type ApprovalDeps } from "./approvalRoutes";
import { approvalSignature, choiceBody } from "./approvalSignature";
import type { JobEvent } from "./events";
import { isSameOrigin, type RequestLike } from "./setupRoutes";

const identifier = z.string().trim().min(1).max(200);
const choiceSchema = z.strictObject({ jobId: identifier, choiceId: identifier, ref: identifier.nullable() });

const copy = {
  sayWhich: "Say which job, which question and which element.",
  noChoice: "This job has no such question.",
  answered: "This question was already answered.",
  notReady: "This job isn't ready for your answer yet. Try again in a moment.",
  notACandidate: "Pick one of the elements the worker showed you.",
};

function choiceState(events: readonly JobEvent[], choiceId: string) {
  const asked = events.find((event) => event.type === "choice.requested" && event.choiceId === choiceId);
  if (!asked || asked.type !== "choice.requested") return { state: "unknown" as const, refs: [] };
  const refs = asked.candidates.map((candidate) => candidate.ref);
  if (events.some((event) => event.type === "choice.answered" && event.choiceId === choiceId)) return { state: "answered" as const, refs };
  if (events.some((event) => event.type === "job.failed" || event.type === "boundary.blocked")) return { state: "ended" as const, refs };
  return { state: "open" as const, refs };
}

export async function handleChoose(request: RequestLike, deps: ApprovalDeps) {
  if (!isSameOrigin(request)) return problem(403, routeCopy.notThisPage);
  const parsed = choiceSchema.safeParse(await readBody(request));
  if (!parsed.success) return problem(400, copy.sayWhich);
  if (!deps.secret) return problem(503, routeCopy.noSecret);
  const { jobId, choiceId, ref } = parsed.data;
  try {
    const job = await deps.readJob(jobId);
    if (!job) return problem(404, routeCopy.noJob);
    const { state, refs } = choiceState(job.events, choiceId);
    if (state === "unknown") return problem(404, copy.noChoice);
    if (state === "answered") return problem(409, copy.answered);
    if (state === "ended" || isTerminal(job.status)) return problem(409, routeCopy.ended);
    if (job.status !== "waiting") return problem(409, copy.notReady);
    if (ref !== null && !refs.includes(ref)) return problem(400, copy.notACandidate);
    const raw = choiceBody(choiceId, ref);
    return agentFieldAnswer(await deps.sendApproval(raw, approvalSignature(deps.secret, raw)), { jobId, choiceId, ref }, copy.answered);
  } catch (error) {
    if (error instanceof AgentFieldError) return problem(502, routeCopy.unavailable);
    throw error;
  }
}
