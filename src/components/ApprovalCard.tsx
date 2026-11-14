"use client";

import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { useId, type ReactNode } from "react";
import { Entrance } from "./Motion";

export function ApprovalCard({
  question,
  detail,
  confirmLabel,
  declineLabel = "Not now",
  onConfirm,
  onDecline,
}: {
  question: string;
  detail?: ReactNode;
  confirmLabel: string;
  declineLabel?: string;
  onConfirm: () => void;
  onDecline: () => void;
}) {
  const titleId = useId();
  return (
    <Entrance>
      <Card padding={4} width="100%">
        <VStack gap={3} role="group" aria-labelledby={titleId}>
          <Text type="label" weight="semibold" id={titleId}>
            {question}
          </Text>
          {detail}
          <HStack gap={1.5} vAlign="center">
            <Button label={confirmLabel} variant="primary" onClick={onConfirm} />
            <Button label={declineLabel} variant="ghost" onClick={onDecline} />
          </HStack>
        </VStack>
      </Card>
    </Entrance>
  );
}
