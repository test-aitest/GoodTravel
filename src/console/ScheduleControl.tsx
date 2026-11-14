"use client";

import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { ChatSystemMessage } from "@astryxdesign/core/Chat";
import { CheckboxInput } from "@astryxdesign/core/CheckboxInput";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { FormLayout } from "@astryxdesign/core/FormLayout";
import { Icon } from "@astryxdesign/core/Icon";
import { Layout, LayoutContent, LayoutFooter } from "@astryxdesign/core/Layout";
import { Link } from "@astryxdesign/core/Link";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { TimeInput, type ISOTimeString } from "@astryxdesign/core/TimeInput";
import { createISOTimeString } from "@astryxdesign/core/utils";
import { ClockIcon } from "@heroicons/react/24/outline";
import { useEffect, useState, useSyncExternalStore, type FormEvent } from "react";
import { scheduleText, skippedRunText, slotLabel, timeZoneName, type ClientSchedule } from "@/lib/schedule";
import type { ConsoleClient } from "./clients";
import { scheduleApi, type ScheduleApi, type ScheduleChange } from "./scheduleApi";

type Known = { schedule: ClientSchedule | null; isLoading: boolean; hasFailed: boolean };

const unknown: Known = { schedule: null, isLoading: false, hasFailed: false };

function createScheduleCache(api: ScheduleApi) {
  const known = new Map<string, Known>();
  const listeners = new Set<() => void>();
  const put = (clientId: string, next: Known) => {
    known.set(clientId, next);
    listeners.forEach((listener) => listener());
  };
  const read = (clientId: string) => known.get(clientId) ?? unknown;
  return {
    read,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    load(clientId: string) {
      const current = read(clientId);
      if (current.schedule || current.isLoading) return;
      put(clientId, { schedule: null, isLoading: true, hasFailed: false });
      api.load(clientId).then(
        (reply) => put(clientId, { schedule: reply.schedule, isLoading: false, hasFailed: false }),
        () => put(clientId, { schedule: null, isLoading: false, hasFailed: true }),
      );
    },
    async save(clientId: string, change: ScheduleChange) {
      const reply = await api.save(clientId, change);
      put(clientId, { schedule: reply.schedule, isLoading: false, hasFailed: false });
    },
  };
}

const caches = new WeakMap<ScheduleApi, ReturnType<typeof createScheduleCache>>();

function cacheFor(api: ScheduleApi) {
  const existing = caches.get(api);
  if (existing) return existing;
  const created = createScheduleCache(api);
  caches.set(api, created);
  return created;
}

function useClientSchedule(clientId: string, api: ScheduleApi) {
  const cache = cacheFor(api);
  const known = useSyncExternalStore(
    cache.subscribe,
    () => cache.read(clientId),
    () => unknown,
  );
  useEffect(() => cache.load(clientId), [cache, clientId]);
  return { ...known, save: (change: ScheduleChange) => cache.save(clientId, change) };
}

function messageOf(error: unknown) {
  return error instanceof Error && error.message ? error.message : "The schedule could not be saved. Try again in a minute.";
}

function ScheduleDialog({
  client,
  schedule,
  onClose,
  onSave,
}: {
  client: ConsoleClient;
  schedule: ClientSchedule;
  onClose: () => void;
  onSave: (change: ScheduleChange) => Promise<void>;
}) {
  const [isOn, setIsOn] = useState(schedule.isOn);
  const [time, setTime] = useState<ISOTimeString | undefined>(createISOTimeString(schedule.time) ?? undefined);
  const [isSaving, setIsSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!time || isSaving) return;
    setIsSaving(true);
    setProblem(null);
    try {
      await onSave({ isOn, time });
      onClose();
    } catch (error) {
      setProblem(messageOf(error));
      setIsSaving(false);
    }
  };

  return (
    <Dialog isOpen onOpenChange={(open) => (open ? undefined : onClose())} purpose="form" width={420}>
      <form onSubmit={submit} noValidate>
        <Layout
          header={
            <DialogHeader
              title="Daily run"
              subtitle={`${client.shortName}'s worker gets the changes ready, then waits for your OK.`}
              onOpenChange={() => onClose()}
            />
          }
          content={
            <LayoutContent>
              <VStack gap={4}>
                <FormLayout>
                  <CheckboxInput label="Run every day" value={isOn} onChange={setIsOn} />
                  <TimeInput
                    label="Time"
                    value={time}
                    onChange={setTime}
                    hourFormat="24h"
                    description={timeZoneName(schedule.timeZone)}
                    isDisabled={!isOn}
                  />
                </FormLayout>
                {problem ? <Banner status="error" title={problem} /> : null}
              </VStack>
            </LayoutContent>
          }
          footer={
            <LayoutFooter>
              <HStack gap={2} hAlign="end">
                <Button label="Cancel" variant="secondary" onClick={onClose} />
                <Button label="Save" variant="primary" type="submit" isLoading={isSaving} isDisabled={!time} />
              </HStack>
            </LayoutFooter>
          }
        />
      </form>
    </Dialog>
  );
}

const MINUTE = 60_000;

function currentMinute() {
  return Math.floor(Date.now() / MINUTE) * MINUTE;
}

function notYetKnown() {
  return null;
}

function everyMinute(listener: () => void) {
  const timer = setInterval(listener, MINUTE);
  return () => clearInterval(timer);
}

export function ScheduledDivider({ slot, timeZone }: { slot: string; timeZone: string }) {
  const now = useSyncExternalStore<number | null>(everyMinute, currentMinute, notYetKnown);
  return <ChatSystemMessage variant="divider">{slotLabel(slot, timeZone, now === null ? null : new Date(now))}</ChatSystemMessage>;
}

export function SkippedRunLine({ slot, timeZone }: { slot: string; timeZone: string }) {
  const now = useSyncExternalStore<number | null>(everyMinute, currentMinute, notYetKnown);
  return <ChatSystemMessage>{skippedRunText(slot, timeZone, now === null ? null : new Date(now))}</ChatSystemMessage>;
}

export function ScheduleControl({ client, api = scheduleApi }: { client: ConsoleClient; api?: ScheduleApi }) {
  const { schedule, hasFailed, save } = useClientSchedule(client.id, api);
  const [isOpen, setIsOpen] = useState(false);
  return (
    <>
      <HStack gap={1.5} vAlign="center">
        <Icon icon={ClockIcon} size="sm" color="secondary" />
        {schedule ? <Text type="supporting">{scheduleText(schedule)}</Text> : null}
        {hasFailed ? <Text type="supporting">Couldn&apos;t check the schedule.</Text> : null}
      </HStack>
      <Link isDisabled={!schedule} onClick={() => setIsOpen(true)}>
        Change
      </Link>
      {isOpen && schedule ? <ScheduleDialog client={client} schedule={schedule} onClose={() => setIsOpen(false)} onSave={save} /> : null}
    </>
  );
}
