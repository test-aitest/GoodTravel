"use client";

import { Button } from "@astryxdesign/core/Button";
import { HStack } from "@astryxdesign/core/Stack";
import { useState } from "react";
import { AppFrame } from "@/components/AppFrame";
import { Composer } from "@/components/Composer";
import { ProgressCard } from "@/components/ProgressCard";
import { Steps } from "@/components/Steps";
import { UserMessage } from "@/components/UserMessage";
import { WorkerMessage } from "@/components/WorkerMessage";

const ignore = () => {};

export function FrameDemo() {
  const [isActivityOpen, setIsActivityOpen] = useState(true);
  return (
    <AppFrame
      title="Supplier stock sync"
      subtitle="Kumo Coffee Roasters"
      userName="Aoi Tanaka"
      clients={[
        { name: "Kumo Coffee Roasters", href: "/dev/frame", status: "working", isSelected: true },
        { name: "Hinata Skincare", href: "/dev/frame?client=hinata", status: "waiting", approvals: 1 },
        { name: "Sora Outdoor", href: "/dev/frame?client=sora", status: "idle" },
      ]}
      recent={[
        { label: "Supplier stock sync", href: "/dev/frame", isSelected: true },
        { label: "Weekly price check", href: "/dev/frame?job=prices" },
        { label: "October invoices", href: "/dev/frame?job=invoices" },
      ]}
      composer={<Composer clientName="Kumo Coffee" isWorking onSubmit={ignore} />}
      activity={
        isActivityOpen ? (
          <>
            <Steps
              steps={[
                { label: "Signed in to Maruyama Supply", detail: "Decided by Claude · 2.2 s", state: "done" },
                { label: "Read today's stock, 42 products", detail: "Decided by Claude · 1.1 s", state: "done" },
                { label: "Comparing with the store admin", detail: "Claude is deciding", state: "now" },
                { label: "Check the save button", state: "next" },
                { label: "Ask you before saving", detail: "You decide", state: "next" },
                { label: "Save and check every row", state: "next" },
              ]}
            />
            <HStack gap={2}>
              <Button label="Take over" />
              <Button label="Stop" variant="ghost" />
            </HStack>
          </>
        ) : undefined
      }
      onCloseActivity={() => setIsActivityOpen(false)}
    >
      <UserMessage>Sync today&apos;s stock from Maruyama Supply to our store admin. Ask me before you save anything.</UserMessage>
      <WorkerMessage
        attachment={
          <ProgressCard
            title="Comparing with the store admin"
            done={2}
            total={6}
            onStop={ignore}
            onOpenActivity={() => setIsActivityOpen(true)}
            isActivityOpen={isActivityOpen}
          />
        }
      >
        I&apos;ll read Maruyama Supply&apos;s stock and compare it with your store admin. I&apos;ll ask before saving anything.
      </WorkerMessage>
    </AppFrame>
  );
}
