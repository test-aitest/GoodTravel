import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { SemanticNode } from "../tree";

export type RecordedPage = {
  observed: { date: string; lightpanda: string; url: string; title: string };
  jsonTree: SemanticNode;
  text?: string;
  interactiveText?: string;
  html?: string;
};

export type RecordedPageName =
  | "store-login"
  | "store-inventory"
  | "store-inventory-decoy"
  | "supplier-stock-1"
  | "supplier-stock-2"
  | "supplier-stock-3"
  | "supplier-stock-hidden-note";

const contractDirectory = join(import.meta.dir, "..", "..", "..", "agent", "tests", "contract", "external", "lightpanda");

export function recorded(name: RecordedPageName): RecordedPage {
  return JSON.parse(readFileSync(join(import.meta.dir, `${name}.json`), "utf8")) as RecordedPage;
}

type ContractCall = { request: { method: string; params: Record<string, unknown> }; response: { result?: unknown } };

export function recordedCall(recording: string, method: string, params: Record<string, unknown>): unknown {
  const calls = (JSON.parse(readFileSync(join(contractDirectory, `${recording}.json`), "utf8")) as { calls: ContractCall[] }).calls;
  const call = calls.find(
    (candidate) => candidate.request.method === method && JSON.stringify(candidate.request.params) === JSON.stringify(params),
  );
  if (!call) throw new Error(`${recording} has no ${method} ${JSON.stringify(params)}`);
  return call.response.result;
}
