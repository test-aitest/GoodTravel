import { agentFieldConfig, startTripSearchJob } from "@/lib/agentfieldClient";
import { findClient } from "@/lib/clients";
import { handleTripSearch } from "@/lib/tripRoutes";

export async function POST(request: Request) {
  const config = agentFieldConfig();
  return handleTripSearch(request, { findClient, startSearch: (search) => startTripSearchJob(config, search) });
}
