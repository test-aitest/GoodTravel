"use client";

import { Banner } from "@astryxdesign/core/Banner";
import { Button } from "@astryxdesign/core/Button";
import { Card } from "@astryxdesign/core/Card";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { FormLayout } from "@astryxdesign/core/FormLayout";
import { Heading } from "@astryxdesign/core/Heading";
import { Icon } from "@astryxdesign/core/Icon";
import { Layout, LayoutContent, LayoutFooter } from "@astryxdesign/core/Layout";
import { Spinner } from "@astryxdesign/core/Spinner";
import { HStack, VStack } from "@astryxdesign/core/Stack";
import { Step, Stepper } from "@astryxdesign/core/Stepper";
import { Switch } from "@astryxdesign/core/Switch";
import { Text } from "@astryxdesign/core/Text";
import { TextInput } from "@astryxdesign/core/TextInput";
import { CheckCircleIcon } from "@heroicons/react/24/outline";
import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { loginSites, setupDone, type ClientSetup, type LoginSite, type SiteLogin } from "@/lib/setup";
import type { ConsoleClient } from "./clients";
import type { ClientSetupState, ConsoleAction } from "./consoleState";
import type { SetupApi } from "./setupApi";

const centeredColumn: CSSProperties = { width: "100%", maxWidth: 640, marginInline: "auto", flex: 1, justifyContent: "center" };

const emptyLogin: SiteLogin = { username: "", password: "" };

type Editing = { site: LoginSite; isReplacing: boolean };

function messageOf(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong. Try again in a minute.";
}

function siteName(site: LoginSite, client: ConsoleClient) {
  return site === "supplier" ? "Maruyama Supply portal" : `${client.nickname} store admin`;
}

function SiteRow({
  name,
  isAdded,
  isMain,
  onEdit,
}: {
  name: string;
  isAdded: boolean;
  isMain: boolean;
  onEdit: () => void;
}) {
  return (
    <HStack gap={2} vAlign="center" hAlign="between">
      <Text>{name}</Text>
      {isAdded ? (
        <HStack gap={2} vAlign="center">
          <HStack gap={1} vAlign="center">
            <Icon icon={CheckCircleIcon} color="success" size="sm" />
            <Text type="supporting">Added</Text>
          </HStack>
          <Button label={`Replace login for ${name}`} variant="ghost" size="sm" onClick={onEdit}>
            Replace
          </Button>
        </HStack>
      ) : (
        <Button label={`Add login for ${name}`} variant={isMain ? "primary" : "secondary"} size="sm" onClick={onEdit}>
          Add login
        </Button>
      )}
    </HStack>
  );
}

function LoginDialog({
  client,
  editing,
  onClose,
  onSaved,
  api,
}: {
  client: ConsoleClient;
  editing: Editing;
  onClose: () => void;
  onSaved: (setup: ClientSetup) => void;
  api: SetupApi;
}) {
  const [login, setLogin] = useState<SiteLogin>(emptyLogin);
  const [isSending, setIsSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const name = siteName(editing.site, client);
  const action = editing.isReplacing ? "Replace login" : "Add login";
  const isComplete = login.username.trim() !== "" && login.password.trim() !== "";

  const close = () => {
    setLogin(emptyLogin);
    setProblem(null);
    onClose();
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!isComplete || isSending) return;
    setIsSending(true);
    setProblem(null);
    try {
      const setup = await api.addLogin(client.id, editing.site, login);
      setLogin(emptyLogin);
      onSaved(setup);
    } catch (error) {
      setProblem(messageOf(error));
      setIsSending(false);
    }
  };

  return (
    <Dialog isOpen onOpenChange={(open) => (open ? undefined : close())} purpose="form" width={520}>
      <form onSubmit={submit} noValidate>
        <Layout
          header={
            <DialogHeader
              title={`${editing.isReplacing ? "Replace" : "Add"} the ${name} login`}
              subtitle={`It goes straight to ${client.nickname}'s computer. You won't see it here again.`}
              onOpenChange={() => close()}
            />
          }
          content={
            <LayoutContent>
              <VStack gap={4}>
                <FormLayout>
                  <TextInput
                    label="Username"
                    value={login.username}
                    onChange={(username) => setLogin((current) => ({ ...current, username }))}
                    autoComplete="off"
                    autoCapitalize="none"
                    hasAutoCorrect={false}
                    hasSpellCheck={false}
                  />
                  <TextInput
                    type="password"
                    label="Password"
                    value={login.password}
                    onChange={(password) => setLogin((current) => ({ ...current, password }))}
                    autoComplete="new-password"
                  />
                </FormLayout>
                {problem ? <Banner status="error" title={problem} /> : null}
              </VStack>
            </LayoutContent>
          }
          footer={
            <LayoutFooter>
              <HStack gap={2} hAlign="end">
                <Button label="Cancel" variant="secondary" onClick={close} />
                <Button label={action} variant="primary" type="submit" isLoading={isSending} isDisabled={!isComplete} />
              </HStack>
            </LayoutFooter>
          }
        />
      </form>
    </Dialog>
  );
}

function Checking({ client, problem, onRetry }: { client: ConsoleClient; problem?: string; onRetry: () => void }) {
  return (
    <VStack gap={4} padding={6} hAlign="center" style={centeredColumn}>
      {problem ? (
        <VStack gap={4} hAlign="center">
          <Banner status="error" title={problem} />
          <Button label="Try again" variant="primary" onClick={onRetry} />
        </VStack>
      ) : (
        <Spinner size="lg" label={`Checking ${client.shortName}'s computer…`} />
      )}
    </VStack>
  );
}

function useLoginCheck(client: ConsoleClient, isLoginStep: boolean, dispatch: (action: ConsoleAction) => void, api: SetupApi) {
  const checked = useRef<string | null>(null);
  const [isChecking, setIsChecking] = useState(false);
  useEffect(() => {
    if (!isLoginStep || checked.current === client.id) return;
    checked.current = client.id;
    let isCurrent = true;
    setIsChecking(true);
    api.checkLogins(client.id).then(
      (setup) => {
        if (isCurrent) dispatch({ type: "setupLoaded", clientId: client.id, setup });
      },
      () => {},
    ).finally(() => {
      if (isCurrent) setIsChecking(false);
    });
    return () => {
      isCurrent = false;
    };
  }, [client.id, isLoginStep, dispatch, api]);
  return isChecking;
}

export function SetupView({
  client,
  setup,
  problem,
  onRetry,
  dispatch,
  api,
}: {
  client: ConsoleClient;
  setup: ClientSetupState | undefined;
  problem?: string;
  onRetry: () => void;
  dispatch: (action: ConsoleAction) => void;
  api: SetupApi;
}) {
  const [isCreating, setIsCreating] = useState(false);
  const [createProblem, setCreateProblem] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [switchProblem, setSwitchProblem] = useState<string | null>(null);
  const done = setup && setup.computer !== "unavailable" ? setupDone(setup) : -1;
  const isChecking = useLoginCheck(client, done === 1, dispatch, api);

  if (!setup) return <Checking client={client} problem={problem} onRetry={onRetry} />;

  const title = <Heading level={1}>{`Set up ${client.shortName}'s worker`}</Heading>;

  if (setup.computer === "unavailable") {
    return (
      <VStack gap={6} padding={6} style={centeredColumn}>
        {title}
        <Banner status="info" title={`${client.name} can't have its own computer yet`} description="Its supplier and store sites aren't connected to ClientOps." />
      </VStack>
    );
  }

  const firstMissing = loginSites.find((site) => !setup.logins[site]);

  const createComputer = async () => {
    setIsCreating(true);
    setCreateProblem(null);
    try {
      dispatch({ type: "setupLoaded", clientId: client.id, setup: await api.createComputer(client.id) });
    } catch (error) {
      setCreateProblem(messageOf(error));
    } finally {
      setIsCreating(false);
    }
  };

  const changeUseClaude = async (useClaude: boolean) => {
    setIsSaving(true);
    setSwitchProblem(null);
    try {
      dispatch({ type: "useClaudeChanged", clientId: client.id, useClaude: await api.setUseClaude(client.id, useClaude) });
    } catch (error) {
      setSwitchProblem(messageOf(error));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <VStack gap={6} padding={6} style={centeredColumn}>
      <VStack gap={1}>
        {title}
        <Text type="supporting">Three short steps. About five minutes.</Text>
      </VStack>
      <Card padding={4} width="100%">
        <Stepper label="Setup steps" orientation="vertical" activeStep={done}>
          <Step step={0} label="Create its computer" description={`A private cloud computer that only works for ${client.shortName}.`}>
            {done === 0 ? (
              <VStack gap={2} padding={2} hAlign="start">
                <Button label="Create its computer" variant="primary" isLoading={isCreating} onClick={createComputer} />
                {isCreating ? <Text type="supporting">This takes about a minute.</Text> : null}
                {createProblem ? <Banner status="error" title={createProblem} /> : null}
              </VStack>
            ) : null}
          </Step>
          <Step
            step={1}
            label="Add the logins it needs"
            description={`They stay on ${client.nickname}'s computer. The worker can use them, but never sees or shares them.`}
          >
            {done >= 1 ? (
              <VStack gap={3} padding={2}>
                {loginSites.map((site) => (
                  <SiteRow
                    key={site}
                    name={siteName(site, client)}
                    isAdded={setup.logins[site]}
                    isMain={done === 1 && site === firstMissing}
                    onEdit={() => setEditing({ site, isReplacing: setup.logins[site] })}
                  />
                ))}
                {isChecking ? <Text type="supporting">{`Checking the logins on ${client.nickname}'s computer…`}</Text> : null}
                <Switch
                  label="Use Claude for new steps"
                  description="Claude sees button names only. Off: the worker asks you instead."
                  value={setup.useClaude}
                  isLoading={isSaving}
                  onChange={changeUseClaude}
                  status={switchProblem ? { type: "error", message: switchProblem } : undefined}
                />
              </VStack>
            ) : null}
          </Step>
          <Step step={2} label="Give it a first job" description="For example: sync today's supplier stock to the store admin.">
            {done >= 2 ? (
              <VStack padding={2} hAlign="start">
                <Button label="Write the first job" variant="primary" onClick={() => dispatch({ type: "finishSetup" })} />
              </VStack>
            ) : null}
          </Step>
        </Stepper>
      </Card>
      {editing ? (
        <LoginDialog
          client={client}
          editing={editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            dispatch({ type: "setupLoaded", clientId: client.id, setup: saved });
          }}
          api={api}
        />
      ) : null}
    </VStack>
  );
}
