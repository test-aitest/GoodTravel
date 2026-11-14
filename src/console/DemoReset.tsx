"use client";

import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Heading } from "@astryxdesign/core/Heading";
import { Link } from "@astryxdesign/core/Link";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { useState } from "react";
import { consoleClients } from "./clients";

export type DemoResetClient = { clientId: string; stopped: number; sites: "reset" | "skipped"; forgotten: number; dailyRun: "off" };
export type DemoResetAnswer = { clients: DemoResetClient[] } | { error: string };

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

function nameOf(clientId: string) {
  return consoleClients.find((client) => client.id === clientId)?.name ?? clientId;
}

function lineOf(client: DemoResetClient) {
  const sites = client.sites === "reset" ? "demo sites reset" : "no demo sites";
  return `${nameOf(client.clientId)}: stopped ${plural(client.stopped, "waiting job")}, forgot ${plural(client.forgotten, "saved item")}, ${sites}, daily run off.`;
}

export async function resetDemoOnServer(): Promise<DemoResetAnswer> {
  const response = await fetch("/api/dev/demo/reset", { method: "POST" }).catch(() => null);
  const body: unknown = await response?.json().catch(() => null);
  if (response?.ok && body && typeof body === "object" && "clients" in body) return body as DemoResetAnswer;
  const error = body && typeof body === "object" && "error" in body && typeof body.error === "string" ? body.error : "The worker service is not available right now.";
  return { error };
}

export function DemoReset({ reset = resetDemoOnServer }: { reset?: () => Promise<DemoResetAnswer> }) {
  const [answer, setAnswer] = useState<DemoResetAnswer | null>(null);
  const [isResetting, setIsResetting] = useState(false);
  const run = async () => {
    setIsResetting(true);
    setAnswer(await reset());
    setIsResetting(false);
  };
  return (
    <VStack gap={4} padding={6} width="100%" style={{ maxWidth: 640, marginInline: "auto" }}>
      <Heading level={1}>Demo</Heading>
      <Text type="supporting">Stops jobs waiting for an OK, has each worker forget its saved steps and undo copies, puts the demo sites back at day 1 and turns daily runs off. Computers and logins stay.</Text>
      <HStack>
        <Button label="Reset demo" variant="primary" isLoading={isResetting} onClick={() => void run()} />
      </HStack>
      {answer && "error" in answer ? <Banner status="error" title={answer.error} /> : null}
      {answer && "clients" in answer ? (
        <VStack gap={2}>
          <Text type="label" weight="semibold">
            The demo is back at the start.
          </Text>
          {answer.clients.map((client) => (
            <Text key={client.clientId} type="supporting">
              {lineOf(client)}
            </Text>
          ))}
        </VStack>
      ) : null}
      <Link href="/">Back to ClientOps</Link>
    </VStack>
  );
}
