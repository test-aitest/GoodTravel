import { createHmac } from "node:crypto";

export function approvalBody(approvalId: string, feedback = "") {
  return JSON.stringify({ requestId: approvalId, decision: "approved", feedback });
}

export function choiceBody(choiceId: string, ref: string | null) {
  return JSON.stringify(ref === null ? { requestId: choiceId, decision: "rejected", feedback: "" } : { requestId: choiceId, decision: "approved", feedback: ref });
}

export function approvalSignature(secret: string | Uint8Array, rawBody: string) {
  return `sha256=${createHmac("sha256", secret).update(rawBody, "utf8").digest("hex")}`;
}
