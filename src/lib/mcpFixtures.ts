import approved from "../../agent/tests/contract/approved_job_notes.json";
import blocked from "../../agent/tests/contract/boundary_blocked_notes.json";
import decoy from "../../agent/tests/contract/decoy_job_notes.json";
import learned from "../../agent/tests/contract/learned_job_notes.json";
import refind from "../../agent/tests/contract/refind_job_notes.json";
import savedProcedure from "../../agent/tests/contract/saved_procedure.json";
import stopped from "../../agent/tests/contract/stopped_job_notes.json";
import { eventNoteTag, newEvents, type ExecutionStatus } from "./agentfieldRecords";
import type { JobRead } from "./approvalRoutes";

type NoteContract = { jobId?: string; input: { client_id: string }; notes: readonly unknown[] };
type NoteEvent = { type: string; [field: string]: unknown };

const startSeconds = Date.UTC(2026, 10, 14, 9, 0, 0) / 1000;

function eventsOf(contract: NoteContract) {
  return contract.notes.map((note) => (note as { event: NoteEvent }).event);
}

function untilApproval(contract: NoteContract) {
  return eventsOf(contract).findIndex((event) => event.type === "approval.requested") + 1;
}

export function readContract(contract: NoteContract, options: { jobId: string; status: ExecutionStatus; upTo?: number; clientId?: string }): JobRead {
  const notes = contract.notes.slice(0, options.upTo ?? contract.notes.length);
  const conversion = newEvents(
    {
      executionId: options.jobId,
      clientId: options.clientId ?? contract.input.client_id,
      status: options.status,
      expectedEvents: notes.length,
      notes: notes.map((payload, index) => ({ message: JSON.stringify(payload), tags: [eventNoteTag], timestamp: startSeconds + index * 0.5 })),
    },
    0,
    { flush: true },
  );
  if (conversion.rejected.length > 0) throw new Error(conversion.rejected.join("; "));
  return { status: options.status, events: conversion.events };
}

export const fixtureJobIds = {
  waiting: "exec_mcp_waiting",
  waitingHinata: "exec_mcp_waiting_hinata",
  running: "exec_mcp_running",
  learned: "exec_mcp_learned",
  refind: "exec_mcp_refind",
  blocked: "exec_mcp_blocked",
  stopped: "exec_mcp_stopped",
  mismatch: "exec_mcp_mismatch",
  starting: "exec_mcp_starting",
} as const;

export const fixtureJobs: Readonly<Record<string, JobRead>> = {
  [fixtureJobIds.waiting]: readContract(approved, { jobId: fixtureJobIds.waiting, status: "waiting", upTo: untilApproval(approved) }),
  [fixtureJobIds.waitingHinata]: readContract(approved, {
    jobId: fixtureJobIds.waitingHinata,
    status: "waiting",
    upTo: untilApproval(approved),
    clientId: "hinata",
  }),
  [fixtureJobIds.running]: readContract(approved, { jobId: fixtureJobIds.running, status: "running", upTo: 9 }),
  [fixtureJobIds.learned]: readContract(learned, { jobId: fixtureJobIds.learned, status: "succeeded" }),
  [fixtureJobIds.refind]: readContract(refind, { jobId: fixtureJobIds.refind, status: "succeeded" }),
  [fixtureJobIds.blocked]: readContract(blocked, { jobId: fixtureJobIds.blocked, status: "succeeded" }),
  [fixtureJobIds.stopped]: readContract(stopped, { jobId: fixtureJobIds.stopped, status: "cancelled" }),
  [fixtureJobIds.mismatch]: readContract(decoy, { jobId: fixtureJobIds.mismatch, status: "succeeded" }),
  [fixtureJobIds.starting]: { status: "running", events: [] },
};

export const fixtureWaitingJobIds: Readonly<Record<string, readonly string[]>> = {
  kumo: [fixtureJobIds.waiting, fixtureJobIds.running, fixtureJobIds.waitingHinata],
  hinata: [fixtureJobIds.waitingHinata],
};

export const fixtureSavedProcedures: Readonly<Record<string, unknown>> = {
  kumo: savedProcedure.record,
  hinata: { ...savedProcedure.record, procedureId: "kumo-stock-sync" },
};

export const savedProcedureContract = savedProcedure;

function selectorsIn(value: unknown, into: string[]) {
  if (Array.isArray(value)) for (const item of value) selectorsIn(item, into);
  else if (typeof value === "object" && value !== null) {
    for (const [key, item] of Object.entries(value)) {
      if ((key === "css" || key === "scope" || key === "namePrefix") && typeof item === "string") into.push(item);
      else selectorsIn(item, into);
    }
  }
  return into;
}

function forbiddenFromJobs() {
  const texts: string[] = [];
  for (const contract of [approved, learned, refind, stopped, decoy, blocked] as NoteContract[]) {
    for (const event of eventsOf(contract)) {
      if (event.type === "page.seen") {
        const html = String(event.html);
        texts.push(html, ...[...html.matchAll(/<td[^>]*>([^<>]+)<\/td>/g)].map((match) => match[1].trim()));
      }
      if (event.type === "approval.requested") {
        texts.push(String(event.approvalId));
        for (const change of event.changes as { sku: string; name: string }[]) texts.push(change.sku, change.name);
      }
      if (event.type === "job.created") texts.push(String(event.request));
    }
  }
  return texts;
}

function forbiddenFromProcedure() {
  const record = savedProcedure.record;
  const logins = record.script.match(/\$LP_[A-Z_]+/g) ?? [];
  return [record.script, ...selectorsIn(record.steps, []), ...logins, "LP_", "input[name", "form[action", "127.0.0.1:3001", "127.0.0.1:3002", "maxPages", "<!doctype", "<html", "<table", "<td"];
}

export const forbiddenTexts: readonly string[] = [...new Set([...forbiddenFromJobs(), ...forbiddenFromProcedure()])].filter((text) => text.length >= 4);

export function leaksIn(output: unknown) {
  const serialized = JSON.stringify(output);
  return forbiddenTexts.filter((text) => serialized.includes(text) || serialized.includes(JSON.stringify(text).slice(1, -1)));
}
