"use client";

import { VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { ResultLine } from "@/components/ResultLine";
import { UserMessage } from "@/components/UserMessage";
import { WorkerMessage } from "@/components/WorkerMessage";
import { factLines, type FactSource } from "@/lib/chatFacts";
import type { ConsoleClient } from "./clients";
import type { ChatEntry } from "./consoleState";
import type { ScheduleApi } from "./scheduleApi";
import { ScheduleControl } from "./ScheduleControl";

const canDo = "I can sync today's supplier stock, run it again, undo the last sync, change the daily run, or tell you about the last job.";

function lead(chat: ChatEntry): string | null {
  const decision = chat.decision;
  if (!decision) return null;
  switch (decision.action) {
    case "answer":
      return decision.facts.length === 0 ? "I don't have that for this conversation yet." : null;
    case "run":
      return null;
    case "undo":
      return "Putting back the stock from before the last sync. I'll check every row after.";
    case "schedule":
      return "Here's the daily run. You can change it here.";
    case "clarify":
      return decision.clarify === "which-job" ? "Which job do you mean? Tell me in a few words, like \"the last sync\"." : `What would you like me to do? ${canDo}`;
    case "cannot":
      return `I can't do that here. ${canDo}`;
  }
}

export function ChatMessages({ chat, source, client, scheduleApi }: { chat: ChatEntry; source: FactSource; client: ConsoleClient; scheduleApi?: ScheduleApi }) {
  const decision = chat.decision;
  const opening = lead(chat);
  const lines = decision?.action === "answer" ? (chat.answer ?? decision.facts.flatMap((id) => factLines(id, source))) : [];
  if (decision?.action === "run" && !chat.problem) return <UserMessage>{chat.text}</UserMessage>;
  return (
    <>
      <UserMessage>{chat.text}</UserMessage>
      <WorkerMessage attachment={decision?.action === "schedule" ? <ScheduleControl client={client} api={scheduleApi} /> : undefined}>
        {chat.problem ? (
          <ResultLine kind="stopped">{chat.problem}</ResultLine>
        ) : !decision ? (
          <Text type="supporting">Thinking…</Text>
        ) : (
          <VStack gap={1}>
            {opening ? <Text>{opening}</Text> : null}
            {lines.map((line, index) => (
              <Text key={`${index}-${line}`}>{line}</Text>
            ))}
          </VStack>
        )}
      </WorkerMessage>
    </>
  );
}
