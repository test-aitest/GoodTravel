import { Icon } from "@astryxdesign/core/Icon";
import { HStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { CheckCircleIcon, ExclamationCircleIcon, LockClosedIcon } from "@heroicons/react/24/outline";
import type { ReactNode } from "react";

const kinds = {
  done: { icon: CheckCircleIcon, color: "success", label: "Done" },
  stopped: { icon: ExclamationCircleIcon, color: "warning", label: "Stopped" },
  blocked: { icon: LockClosedIcon, color: "error", label: "Not allowed" },
} as const;

export type ResultKind = keyof typeof kinds;

export function ResultLine({ kind, children }: { kind: ResultKind; children: ReactNode }) {
  const { icon, color, label } = kinds[kind];
  return (
    <HStack gap={2} vAlign="center">
      <Icon icon={icon} color={color} size="md" label={label} />
      <Text>{children}</Text>
    </HStack>
  );
}
