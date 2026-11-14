import { findClient } from "@/lib/clients";
import { handleReadSchedule, handleSetSchedule, type ScheduleDeps } from "@/lib/scheduleRoutes";
import { readSchedule, scheduledRuns, writeSchedule } from "@/lib/scheduleStore";

const deps: ScheduleDeps = {
  findClient,
  read: (clientId) => readSchedule(clientId),
  write: (clientId, schedule) => writeSchedule(clientId, schedule),
  runs: (clientId) => scheduledRuns(clientId),
  warn: (message) => console.warn(message),
};

export async function GET(_request: Request, { params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  return handleReadSchedule(clientId, deps);
}

export async function PUT(request: Request, { params }: { params: Promise<{ clientId: string }> }) {
  const { clientId } = await params;
  return handleSetSchedule(request, clientId, deps);
}
