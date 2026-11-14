import { z } from "zod";

const replySchema = z.object({ jobIds: z.array(z.string().min(1)) });

type Fetcher = (input: string) => Promise<Response>;

export async function loadWaitingJobs(clientId: string, fetcher: Fetcher = (input) => fetch(input)): Promise<string[] | null> {
  const response = await fetcher(`/api/clients/${encodeURIComponent(clientId)}/waiting`).catch(() => null);
  if (!response?.ok) return null;
  const parsed = replySchema.safeParse(await response.json().catch(() => null));
  return parsed.success ? parsed.data.jobIds : null;
}
