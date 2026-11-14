"use client";

import { ChatComposer, ChatComposerInput } from "@astryxdesign/core/Chat";
import { Icon } from "@astryxdesign/core/Icon";
import { VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { Token } from "@astryxdesign/core/Token";
import { ShieldCheckIcon } from "@heroicons/react/24/outline";

export function Composer({
  clientName,
  isWorking = false,
  isFollowUp = false,
  onSubmit,
}: {
  clientName: string;
  isWorking?: boolean;
  isFollowUp?: boolean;
  onSubmit: (text: string) => void;
}) {
  return (
    <VStack gap={2}>
      <ChatComposer
        onSubmit={onSubmit}
        input={
          <ChatComposerInput
            label={isWorking || isFollowUp ? "Message the worker" : "Describe a job"}
            placeholder={
              isWorking ? "Ask something while it works" : isFollowUp ? `Give ${clientName}'s worker another job` : "Describe a job in your own words"
            }
          />
        }
        footerActions={<Token size="sm" label="Asks before saving" icon={<Icon icon={ShieldCheckIcon} size="xsm" />} />}
      />
      <Text type="supporting" justify="center">
        {`Works on ${clientName}'s own computer with its logins only.`}
      </Text>
    </VStack>
  );
}
