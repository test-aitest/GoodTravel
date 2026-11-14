"use client";

import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { Icon } from "@astryxdesign/core/Icon";
import { IconButton } from "@astryxdesign/core/IconButton";
import { ProgressBar } from "@astryxdesign/core/ProgressBar";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { StopIcon } from "@heroicons/react/24/solid";
import { Entrance } from "./Motion";

export function ProgressCard({
  title,
  done,
  total,
  onStop,
  onOpenActivity,
  isActivityOpen = false,
}: {
  title: string;
  done: number;
  total: number;
  onStop?: () => void;
  onOpenActivity?: () => void;
  isActivityOpen?: boolean;
}) {
  const current = Math.min(done + 1, total);
  return (
    <Entrance>
      <Card padding={3} width="100%">
        <VStack gap={2}>
          <HStack gap={2} vAlign="center" hAlign="between">
            <Text type="label" weight="semibold">
              {title}
            </Text>
            {onStop ? <IconButton label="Stop the job" variant="primary" size="sm" icon={<Icon icon={StopIcon} size="xsm" />} onClick={onStop} /> : null}
          </HStack>
          <ProgressBar label="Job progress" isLabelHidden value={done} max={total} />
          <HStack gap={2} vAlign="center" hAlign="between">
            <Text type="supporting">{`Step ${current} of ${total}`}</Text>
            {onOpenActivity && !isActivityOpen ? (
              <Button label="Show activity" variant="ghost" size="sm" onClick={onOpenActivity} />
            ) : null}
          </HStack>
        </VStack>
      </Card>
    </Entrance>
  );
}
