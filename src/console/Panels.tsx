"use client";

import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { Icon } from "@astryxdesign/core/Icon";
import { MetadataList, MetadataListItem } from "@astryxdesign/core/MetadataList";
import { Skeleton } from "@astryxdesign/core/Skeleton";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { StatusDot } from "@astryxdesign/core/StatusDot";
import { Table } from "@astryxdesign/core/Table";
import { Text } from "@astryxdesign/core/Text";
import { Token } from "@astryxdesign/core/Token";
import { ShieldCheckIcon } from "@heroicons/react/24/outline";
import { Steps } from "@/components/Steps";
import type { StockChange } from "@/lib/events";
import type { JobCost, JobState, SeenPage } from "@/lib/jobState";
import type { CSSProperties } from "react";
import { claudeSpend, computerTime } from "@/lib/costView";
import { stepItems } from "@/lib/jobView";
import type { ConsoleClient } from "./clients";

type PreviewRow = { sku: string; name: string; available: number } & Record<string, unknown>;

const previewColumns = [
  { key: "sku", header: "SKU" },
  { key: "name", header: "Product" },
  { key: "available", header: "Available", align: "end" as const },
];

const PAGE_SCALE = 0.6;
const pageWindow: CSSProperties = { height: 320, overflow: "hidden", borderRadius: "var(--radius-container)", border: "1px solid var(--color-border)" };
const pageFrame: CSSProperties = {
  width: `${100 / PAGE_SCALE}%`,
  height: `${320 / PAGE_SCALE}px`,
  border: "none",
  transform: `scale(${PAGE_SCALE})`,
  transformOrigin: "top left",
  background: "var(--color-background-surface)",
};

function PageSnapshot({ page }: { page: SeenPage }) {
  return (
    <VStack style={pageWindow}>
      <iframe title={`The page the worker saw on ${page.host}`} sandbox="" referrerPolicy="no-referrer" srcDoc={page.html} style={pageFrame} />
    </VStack>
  );
}

function LiveView({ client, page, preview }: { client: ConsoleClient; page: SeenPage | null; preview: StockChange[] | null }) {
  const rows: PreviewRow[] | null = !page && preview ? preview.slice(0, 4).map((change) => ({ sku: change.sku, name: change.name, available: change.supplier })) : null;
  return (
    <Card padding={3} width="100%">
      <VStack gap={3} role="group" aria-label={`${client.nickname}'s computer`}>
        <HStack gap={2} vAlign="center">
          <StatusDot variant={rows ? "warning" : "success"} label={rows ? "Preview" : "Live"} isPulsing={!rows} />
          <Text type="label" weight="semibold">
            {rows ? "Preview" : "Live"}
          </Text>
          <Text type="supporting">{rows ? `Not saved · ${client.storeHost}` : `${client.nickname}'s computer · ${page?.host ?? client.storeHost}`}</Text>
        </HStack>
        {page ? (
          <PageSnapshot page={page} />
        ) : rows ? (
          <Table data={rows} columns={previewColumns} idKey="sku" density="compact" textOverflow="truncate" />
        ) : (
          <VStack gap={2}>
            <Skeleton height={12} width="60%" />
            <Skeleton height={12} index={1} />
            <Skeleton height={12} index={2} />
            <Skeleton height={12} index={3} width="80%" />
          </VStack>
        )}
      </VStack>
    </Card>
  );
}

function CostDetails({ cost }: { cost: JobCost }) {
  return (
    <MetadataList>
      <MetadataListItem label="Claude">{claudeSpend(cost)}</MetadataListItem>
      <MetadataListItem label="Computer">{computerTime(cost)}</MetadataListItem>
    </MetadataList>
  );
}

export function ActivityView({ client, job, onStop }: { client: ConsoleClient; job: JobState; onStop: () => void }) {
  const isActive = job.status === "running" || job.status === "waiting";
  return (
    <>
      <LiveView client={client} page={job.page} preview={job.pendingApproval?.changes ?? null} />
      {job.ignoredInstructions.map((ignored) => (
        <HStack key={ignored.host} gap={1.5} vAlign="start">
          <Icon icon={ShieldCheckIcon} size="sm" color="secondary" />
          <Text type="supporting">{`Ignored a suspicious instruction on ${ignored.host}. Nothing it asked for was done.`}</Text>
        </HStack>
      ))}
      <Steps steps={stepItems(job)} />
      {job.cost ? <CostDetails cost={job.cost} /> : null}
      {isActive ? (
        <HStack gap={2}>
          <Button label="Stop" variant="secondary" onClick={onStop} />
        </HStack>
      ) : null}
    </>
  );
}

function ButtonRow({ label, buttonName }: { label: string; buttonName: string }) {
  return (
    <Card padding={3} variant="muted" width="100%">
      <VStack gap={2}>
        <Text type="supporting">{label}</Text>
        <HStack gap={1.5}>
          <Token size="sm" label="Discard" />
          <Token size="sm" label={buttonName} />
        </HStack>
      </VStack>
    </Card>
  );
}

export function WhatChangedView({ job }: { job: JobState }) {
  const approval = job.pendingApproval ?? null;
  const change = approval?.procedureChange;
  const rows = approval?.changes.length ?? job.written ?? 0;
  if (!change) return <Text type="supporting">Nothing changed on the page.</Text>;
  const step = job.steps.find((candidate) => candidate.stepId === change.stepId);
  const byYou = step?.decidedBy === "human";
  const claudeLooked = step?.decidedBy === "claude" || Boolean(step?.escalatedFrom?.includes("claude"));
  return (
    <>
      <VStack gap={2}>
        <ButtonRow label="Before" buttonName={change.before} />
        <ButtonRow label="Today" buttonName={change.after} />
      </VStack>
      <Text>
        {byYou
          ? "Same button in the same form, with a new name. You picked it from the buttons in that form."
          : "Same button in the same form, with a new name. I found it by what it does on the page, not by where it sits on the screen."}
      </Text>
      <MetadataList>
        <MetadataListItem label="Saved steps">{`Become v${change.toVersion} after you approve`}</MetadataListItem>
        <MetadataListItem label="Decided by">{byYou ? "You, for this one step" : "Claude, for this one step"}</MetadataListItem>
        {claudeLooked ? <MetadataListItem label="Claude saw">Button and field names only</MetadataListItem> : null}
        <MetadataListItem label="After saving">{`I re-read all ${rows} rows`}</MetadataListItem>
      </MetadataList>
    </>
  );
}
