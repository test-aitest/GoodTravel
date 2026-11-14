import { agentFieldConfig, startTripBookingJob } from "@/lib/agentfieldClient";
import { findClient } from "@/lib/clients";
import { handleTripBooking } from "@/lib/tripRoutes";

export async function POST(request: Request) {
  const config = agentFieldConfig();
  return handleTripBooking(request, { findClient, startBooking: (booking) => startTripBookingJob(config, booking) });
}
