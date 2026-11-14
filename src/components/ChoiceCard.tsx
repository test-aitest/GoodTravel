"use client";

import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { RadioList, RadioListItem } from "@astryxdesign/core/RadioList";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { useId, useState } from "react";
import { Entrance } from "./Motion";

export type ChoiceOption = { ref: string; name: string };

export function ChoiceCard({
  question,
  detail,
  candidates,
  confirmLabel,
  noneLabel = "None of these",
  onPick,
}: {
  question: string;
  detail?: string;
  candidates: readonly ChoiceOption[];
  confirmLabel: string;
  noneLabel?: string;
  onPick: (ref: string | null) => void;
}) {
  const titleId = useId();
  const [picked, setPicked] = useState("");
  return (
    <Entrance>
      <Card padding={4} width="100%">
        <VStack gap={3} role="group" aria-labelledby={titleId}>
          <VStack gap={1}>
            <Text type="label" weight="semibold" id={titleId}>
              {question}
            </Text>
            {detail ? <Text type="supporting">{detail}</Text> : null}
          </VStack>
          <RadioList label={question} isLabelHidden value={picked} onChange={setPicked}>
            {candidates.map((candidate) => (
              <RadioListItem key={candidate.ref} value={candidate.ref} label={candidate.name} />
            ))}
          </RadioList>
          <HStack gap={1.5} vAlign="center">
            <Button label={confirmLabel} variant="primary" isDisabled={!picked} onClick={() => onPick(picked)} />
            <Button label={noneLabel} variant="ghost" onClick={() => onPick(null)} />
          </HStack>
        </VStack>
      </Card>
    </Entrance>
  );
}
