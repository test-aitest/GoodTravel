import { ChatMessage, ChatMessageBubble } from "@astryxdesign/core/Chat";
import { VStack } from "@astryxdesign/core/Stack";
import type { ReactNode } from "react";
import { WorkerMark } from "./WorkerMark";
import { Entrance } from "./Motion";

export function WorkerMessage({ children, attachment }: { children: ReactNode; attachment?: ReactNode }) {
  return (
    <Entrance>
      <ChatMessage sender="assistant" avatar={<WorkerMark size="sm" />}>
        <VStack gap={3} width="100%">
          <ChatMessageBubble variant="ghost" width="100%">{children}</ChatMessageBubble>
          {attachment}
        </VStack>
      </ChatMessage>
    </Entrance>
  );
}
