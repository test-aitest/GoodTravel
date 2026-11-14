import { afterEach, describe, expect, test } from "bun:test";
import { existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { daemonAnswers, sendToDaemon } from "./client";
import { startDaemon } from "./daemon";
import { FakeTab } from "./fixtures/fakeTab";

const store = "http://127.0.0.1:3002";
const started: { stop: () => Promise<void> }[] = [];

function socketPath() {
  return join(tmpdir(), `cr-test-${process.pid}-${Math.random().toString(36).slice(2, 8)}.sock`);
}

async function daemonOn(path: string, idleMs = 60_000) {
  const tab = new FakeTab();
  const daemon = await startDaemon({ socketPath: path, openTab: async () => tab, idleMs });
  started.push(daemon);
  return { tab, daemon };
}

afterEach(async () => {
  await Promise.all(started.splice(0).map((daemon) => daemon.stop()));
});

describe("runner daemon", () => {
  test("keeps the page between commands, so a later command sees what an earlier one opened", async () => {
    const path = socketPath();
    await daemonOn(path);
    expect(await sendToDaemon(path, { command: { command: "open", url: `${store}/inventory` }, secrets: [] })).toMatchObject({ ok: true, command: "open" });
    const tree = await sendToDaemon(path, { command: { command: "tree", mode: "interactive" }, secrets: [] });
    expect(tree.ok && tree.command === "tree" && tree.elements).toHaveLength(45);
  });

  test("answers a malformed command with bad-command", async () => {
    const path = socketPath();
    await daemonOn(path);
    expect(await sendToDaemon(path, { command: { command: "evaluate" } as never, secrets: [] })).toMatchObject({
      ok: false,
      command: "unknown",
      error: { code: "bad-command" },
    });
  });

  test("hides secrets it was told about in every later answer", async () => {
    const path = socketPath();
    await daemonOn(path);
    await sendToDaemon(path, { command: { command: "open", url: `${store}/login` }, secrets: [] });
    await sendToDaemon(path, {
      command: { command: "fill", target: { role: "textbox", name: "Username", form: "/login" }, text: "demo-user" },
      secrets: ["demo-user"],
    });
    const tree = await sendToDaemon(path, { command: { command: "tree", mode: "full" }, secrets: [] });
    expect(JSON.stringify(tree)).not.toContain("demo-user");
  });

  test("close ends the page, stops the daemon and removes its socket", async () => {
    const path = socketPath();
    const { tab } = await daemonOn(path);
    await sendToDaemon(path, { command: { command: "open", url: `${store}/login` }, secrets: [] });
    expect(await sendToDaemon(path, { command: { command: "close" }, secrets: [] })).toEqual({ ok: true, command: "close" });
    for (let tries = 0; tries < 50 && existsSync(path); tries += 1) await Bun.sleep(10);
    expect(tab.closed).toBe(true);
    expect(existsSync(path)).toBe(false);
    expect(await daemonAnswers(path)).toBe(false);
  });

  test("stops by itself after being idle, closing the page", async () => {
    const path = socketPath();
    const { tab } = await daemonOn(path, 150);
    await sendToDaemon(path, { command: { command: "open", url: `${store}/login` }, secrets: [] });
    await Bun.sleep(400);
    expect(tab.closed).toBe(true);
    expect(await daemonAnswers(path)).toBe(false);
  });

  test("will not start a second daemon on a socket that is in use", async () => {
    const path = socketPath();
    await daemonOn(path);
    await expect(startDaemon({ socketPath: path, openTab: async () => new FakeTab(), idleMs: 1000 })).rejects.toThrow("already running");
  });

  test("takes over a socket file that nobody answers on", async () => {
    const path = socketPath();
    writeFileSync(path, "");
    await daemonOn(path);
    expect(await daemonAnswers(path)).toBe(true);
  });
});
