"use client";

import { Card } from "@astryxdesign/core/Card";
import { Grid } from "@astryxdesign/core/Grid";
import { Heading } from "@astryxdesign/core/Heading";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import type { ReactNode } from "react";
import { ApprovalCard } from "@/components/ApprovalCard";
import { Composer } from "@/components/Composer";
import { ProgressCard } from "@/components/ProgressCard";
import { ResultLine } from "@/components/ResultLine";
import { Steps } from "@/components/Steps";
import { UserMessage } from "@/components/UserMessage";
import { WorkerMark } from "@/components/WorkerMark";
import { WorkerMessage } from "@/components/WorkerMessage";

function Part({ name, rule, children }: { name: string; rule: string; children: ReactNode }) {
  return (
    <Card padding={5} height="100%">
      <VStack gap={4} height="100%" role="region" aria-label={name}>
        <Text type="label" weight="semibold">
          {name}
        </Text>
        <VStack gap={2} vAlign="center" height="100%">
          {children}
        </VStack>
        <Text type="supporting">{rule}</Text>
      </VStack>
    </Card>
  );
}

const ignore = () => {};

export function PartsGallery() {
  return (
    <VStack gap={6} padding={8}>
      <VStack gap={1}>
        <Heading level={1}>Every screen is made of these seven parts</Heading>
        <Text type="supporting">Built from Astryx components. No new parts without a reason.</Text>
      </VStack>
      <Grid columns={{ minWidth: 300, repeat: "fill" }} gap={5}>
        <Part name="1 · Composer" rule="Always at the bottom. Shows which client it will work for.">
          <Composer clientName="Kumo Coffee" onSubmit={ignore} />
        </Part>
        <Part name="2 · Your message" rule="Right side, gray. Plain words, like a chat.">
          <UserMessage>Sync today&apos;s supplier stock. Ask me before saving.</UserMessage>
        </Part>
        <Part name="3 · Worker message" rule="Two sentences at most. No tool names or IDs.">
          <WorkerMessage>12 products are different. Nothing is saved yet.</WorkerMessage>
        </Part>
        <Part name="4 · Progress card" rule="One per job. Says what is happening now. Opens Activity.">
          <ProgressCard title="Comparing with the store admin" done={2} total={6} onStop={ignore} onOpenActivity={ignore} />
        </Part>
        <Part name="5 · Steps" rule="Only inside Activity. Done, now, next, and who decided.">
          <Steps
            steps={[
              { label: "Read today's stock", detail: "Decided by Claude", state: "done" },
              { label: "Comparing with the store admin", detail: "Claude is deciding", state: "now" },
              { label: "Ask you before saving", detail: "You decide", state: "next" },
            ]}
          />
        </Part>
        <Part name="6 · Approval card" rule="A question as the title. One main button.">
          <ApprovalCard question="Save 12 stock changes to the store admin?" confirmLabel="Save changes" onConfirm={ignore} onDecline={ignore} />
        </Part>
        <Part name="7 · Result line" rule="Shape and color together, never color alone.">
          <ResultLine kind="done">Done. 12 products updated.</ResultLine>
          <ResultLine kind="stopped">Stopped before saving.</ResultLine>
          <ResultLine kind="blocked">Not allowed for this client.</ResultLine>
        </Part>
        <Part name="The worker mark" rule="A computer with a prompt: the worker always has its own machine.">
          <HStack gap={5} vAlign="end" hAlign="center">
            <WorkerMark size="sm" />
            <WorkerMark size="md" />
            <WorkerMark size="lg" />
          </HStack>
        </Part>
      </Grid>
    </VStack>
  );
}
