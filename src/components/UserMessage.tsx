import { ChatMessage, ChatMessageBubble } from "@astryxdesign/core/Chat";
import type { ReactNode } from "react";
import { Entrance } from "./Motion";

export function UserMessage({ children }: { children: ReactNode }) {
  return (
    <Entrance>
      <ChatMessage sender="user">
        <ChatMessageBubble>{children}</ChatMessageBubble>
      </ChatMessage>
    </Entrance>
  );
}
