"use client";

import { AppShell } from "@astryxdesign/core/AppShell";
import { Badge } from "@astryxdesign/core/Badge";
import { ChatLayout, ChatMessageList } from "@astryxdesign/core/Chat";
import { Heading } from "@astryxdesign/core/Heading";
import { Icon } from "@astryxdesign/core/Icon";
import { IconButton } from "@astryxdesign/core/IconButton";
import { Layout, LayoutContent, LayoutHeader, LayoutPanel } from "@astryxdesign/core/Layout";
import { SideNav, SideNavHeading, SideNavItem, SideNavSection } from "@astryxdesign/core/SideNav";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { StatusDot } from "@astryxdesign/core/StatusDot";
import { Text } from "@astryxdesign/core/Text";
import { Toolbar } from "@astryxdesign/core/Toolbar";
import { ClipboardDocumentCheckIcon, MagnifyingGlassIcon, PencilSquareIcon, UserCircleIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { useMemo, useState, type CSSProperties, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import { MotionRoot, MotionScope, SideDrawer, useKeptWhileLeaving, WidthReveal } from "./Motion";
import { SidebarIcon } from "./SidebarIcon";
import { WorkerMark } from "./WorkerMark";

export type ClientStatus = "working" | "waiting" | "idle";

export type ClientLink = { name: string; href: string; status: ClientStatus; approvals?: number; isSelected?: boolean; onSelect?: () => void };

export type RecentLink = { label: string; href: string; isSelected?: boolean; onSelect?: () => void };

function selecting(onSelect?: () => void) {
  if (!onSelect) return undefined;
  return (event: ReactMouseEvent) => {
    event.preventDefault();
    onSelect();
  };
}

const titleSize: CSSProperties = {
  fontSize: "var(--text-heading-5-size)",
  lineHeight: "var(--text-heading-5-leading)",
  fontWeight: "var(--text-heading-5-weight)",
};

const fillColumn: CSSProperties = { flex: 1, minHeight: 0, width: "100%", maxWidth: 800, marginInline: "auto" };

const statusDots = {
  working: { variant: "success", label: "Working" },
  waiting: { variant: "warning", label: "Waiting for you" },
  idle: { variant: "neutral", label: "Idle" },
} as const;

function ClientState({ client }: { client: ClientLink }) {
  const dot = statusDots[client.status];
  const approvals = client.approvals ?? 0;
  return (
    <HStack gap={1.5} vAlign="center">
      {approvals > 0 ? (
        <Badge variant="neutral" label={String(approvals)} aria-label={`${approvals} waiting for your approval`} />
      ) : client.status === "working" ? (
        <Text type="supporting">Working</Text>
      ) : null}
      <StatusDot variant={dot.variant} label={dot.label} />
    </HStack>
  );
}

export function AppFrame({
  title,
  subtitle,
  userName,
  clients,
  recent,
  composer,
  children,
  main,
  emptyState,
  activity,
  panelTitle = "Activity",
  onCloseActivity,
  onNewJob,
  setup,
  isTitleHeading = true,
  viewKey,
}: {
  title: string;
  subtitle?: string;
  userName: string;
  clients: ClientLink[];
  recent: RecentLink[];
  composer?: ReactNode;
  children?: ReactNode;
  main?: ReactNode;
  emptyState?: ReactNode;
  activity?: ReactNode;
  panelTitle?: string;
  onCloseActivity?: () => void;
  onNewJob?: () => void;
  setup?: { done: number; total: number };
  isTitleHeading?: boolean;
  viewKey?: string;
}) {
  const [isNavOpen, setNavOpen] = useState(true);
  const panelContent = useMemo(() => (activity ? { activity, title: panelTitle } : null), [activity, panelTitle]);
  const panel = useKeptWhileLeaving(panelContent);
  return (
    <MotionRoot>
      <AppShell
        contentPadding={0}
        sideNav={
          <SideDrawer isOpen={isNavOpen}>
            <SideNav
              resizable={{ defaultWidth: 272, minWidth: 220, maxWidth: 360 }}
              header={
                <SideNavHeading
                  heading="ClientOps"
                  icon={<WorkerMark size="sm" tone="dark" label="ClientOps" />}
                  headingHref="/"
                  headerEndContent={
                    <IconButton label="Close sidebar" variant="ghost" size="sm" icon={<Icon icon={SidebarIcon} size="sm" />} onClick={() => setNavOpen(false)} />
                  }
                />
              }
              footer={
                <VStack gap={2}>
                  <SideNavSection title="Account" isHeaderHidden>
                    {setup ? (
                      <SideNavItem
                        label="Finish setup"
                        icon={ClipboardDocumentCheckIcon}
                        href="/"
                        onClick={selecting(onNewJob)}
                        endContent={<Text type="supporting">{`${setup.done} of ${setup.total}`}</Text>}
                      />
                    ) : null}
                    <SideNavItem label={userName} icon={UserCircleIcon} href="/account" />
                  </SideNavSection>
                </VStack>
              }
            >
              <SideNavSection title="Jobs" isHeaderHidden>
                <SideNavItem label="New job" icon={PencilSquareIcon} href="/" onClick={selecting(onNewJob)} />
                <SideNavItem label="Search jobs" icon={MagnifyingGlassIcon} href="/search" />
              </SideNavSection>
              <SideNavSection title="Clients">
                {clients.map((client) => (
                  <SideNavItem
                    key={client.href}
                    label={client.name}
                    href={client.href}
                    isSelected={client.isSelected}
                    onClick={selecting(client.onSelect)}
                    endContent={<ClientState client={client} />}
                  />
                ))}
              </SideNavSection>
              {recent.length > 0 ? (
                <SideNavSection title="Recent">
                  {recent.map((job) => (
                    <SideNavItem key={job.href} label={job.label} href={job.href} isSelected={job.isSelected} onClick={selecting(job.onSelect)} />
                  ))}
                </SideNavSection>
              ) : null}
            </SideNav>
          </SideDrawer>
        }
      >
        <Layout
          height="fill"
          header={
            <LayoutHeader label="Job">
              <HStack gap={2} vAlign="center">
                {isNavOpen ? null : <IconButton label="Open sidebar" variant="ghost" size="sm" icon={<Icon icon={SidebarIcon} size="sm" />} onClick={() => setNavOpen(true)} />}
                <VStack gap={0}>
                  {isTitleHeading ? (
                    <Heading level={1} style={titleSize}>
                      {title}
                    </Heading>
                  ) : (
                    <Text type="label" weight="semibold">
                      {title}
                    </Text>
                  )}
                  {subtitle ? <Text type="supporting">{subtitle}</Text> : null}
                </VStack>
              </HStack>
            </LayoutHeader>
          }
          content={
            <LayoutContent padding={0} isScrollable={false}>
              <MotionScope fade key={viewKey}>
                {main ?? (
                  <ChatLayout density="spacious" composer={composer} emptyState={emptyState} style={fillColumn}>
                    {children ? <ChatMessageList align="top">{children}</ChatMessageList> : null}
                  </ChatLayout>
                )}
              </MotionScope>
            </LayoutContent>
          }
          end={
            panel.shown ? (
              <WidthReveal name="panel" isLeaving={panel.isLeaving} onLeft={panel.finish}>
                <LayoutPanel role="complementary" label={panel.shown.title} width={400} hasDivider padding={0}>
                  <VStack gap={0}>
                    <Toolbar
                      label={`${panel.shown.title} actions`}
                      dividers={["bottom"]}
                      startContent={
                        <Text type="label" weight="semibold">
                          {panel.shown.title}
                        </Text>
                      }
                      endContent={
                        onCloseActivity ? (
                          <IconButton label={`Close ${panel.shown.title.toLowerCase()}`} variant="ghost" size="sm" icon={<Icon icon={XMarkIcon} size="sm" />} onClick={onCloseActivity} />
                        ) : null
                      }
                    />
                    <VStack gap={4} padding={4}>
                      {panel.shown.activity}
                    </VStack>
                  </VStack>
                </LayoutPanel>
              </WidthReveal>
            ) : null
          }
        />
      </AppShell>
    </MotionRoot>
  );
}
