import { agentFieldConfig, cancelExecution, runSkill, waitingJobIds } from "@/lib/agentfieldClient";
import { knownClients } from "@/lib/clients";
import { handleDemoReset, readAgentReset } from "@/lib/demoReset";
import { readSchedule, writeSchedule } from "@/lib/scheduleStore";

const RESET_WAIT_MS = 60_000;

export async function POST(request: Request) {
  const config = agentFieldConfig();
  return handleDemoReset(request, {
    clients: knownClients,
    resetAgent: async (clientId) => readAgentReset(clientId, await runSkill(config, "reset_demo", { client_id: clientId }, { timeoutMs: RESET_WAIT_MS })),
    waitingJobIds: (clientId) => waitingJobIds(config, clientId),
    stop: (jobId) => cancelExecution(config, jobId, "Demo reset"),
    scheduleOff: async (clientId) => {
      await writeSchedule(clientId, { ...(await readSchedule(clientId)), isOn: false });
    },
  });
}
