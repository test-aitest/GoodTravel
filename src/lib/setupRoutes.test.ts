import { afterEach, describe, expect, spyOn, test } from "bun:test";
import contract from "../../agent/tests/contract/setup.json";
import { AgentFieldError } from "./agentfieldClient";
import { LoginBridgeError } from "./loginBridge";
import { SettingsError } from "./settings";
import { readAgentAnswer, SetupWorkerError, type SetupWorker, type WorkerAnswer } from "./setup";
import { handleAddLogins, handleCheckLogins, handlePrepareComputer, handleReadSetup, handleSetUseClaude, type RequestLike, type SetupDeps } from "./setupRoutes";

const form = {
  supplier: { username: "kumo-supply-user", password: "kumo-supply-pass-3Rt!" },
  store: { username: "kumo-store-user", password: "kumo-store-pass-8Yu#" },
};
const formValues = [form.supplier.username, form.supplier.password, form.store.username, form.store.password];
const supplierOnly = { supplier: form.supplier };
const allNames = contract.answers.computerWithLogins.logins.present;
const sameOrigin = { "sec-fetch-site": "same-origin", origin: "http://app.test", host: "app.test", "content-type": "application/json" };

function request(headers: Record<string, string> = sameOrigin, body = ""): RequestLike {
  const lower = Object.fromEntries(Object.entries(headers).map(([name, value]) => [name.toLowerCase(), value]));
  return { headers: { get: (name: string) => lower[name.toLowerCase()] ?? null }, text: async () => body };
}

type Recorded = { status: string[]; prepare: string[]; putLogins: { clientId: string; values: Record<string, string> }[] };

function reply<T>(value: T | Error | undefined, fallback: T) {
  if (value instanceof Error) throw value;
  return value ?? fallback;
}

function worker(answer: Partial<{ status: WorkerAnswer | Error; prepare: WorkerAnswer | Error; putLogins: { present: string[] } | Error }> = {}) {
  const calls: Recorded = { status: [], prepare: [], putLogins: [] };
  const instance: SetupWorker = {
    status: async (clientId, options) => {
      calls.status.push(options?.wake ? `${clientId} waking` : clientId);
      return reply(answer.status, readAgentAnswer(clientId, { ...contract.answers.computerWithoutLogins, clientId }));
    },
    prepare: async (clientId) => {
      calls.prepare.push(clientId);
      return reply(answer.prepare, readAgentAnswer(clientId, { ...contract.answers.computerWithoutLogins, clientId }));
    },
    putLogins: async (clientId, values) => {
      calls.putLogins.push({ clientId, values: { ...values } });
      return reply(answer.putLogins, { present: allNames });
    },
  };
  return { instance, calls };
}

function deps(setupWorker: SetupWorker, overrides: Partial<SetupDeps> = {}, remembered: Record<string, readonly string[]> = {}) {
  const writes: [string, boolean][] = [];
  const warnings: string[] = [];
  const memory = { ...remembered };
  const built: SetupDeps = {
    worker: setupWorker,
    findClient: (id) => (["kumo", "hinata", "sora"].includes(id) ? { id, name: id } : null),
    settings: {
      read: async (clientId) => clientId !== "hinata",
      write: async (clientId, useClaude) => {
        writes.push([clientId, useClaude]);
        return useClaude;
      },
    },
    memory: {
      read: async (clientId) => memory[clientId] ?? null,
      write: async (clientId, present) => {
        memory[clientId] = [...present];
      },
    },
    warn: (message) => warnings.push(message),
    ...overrides,
  };
  return { built, writes, warnings, memory };
}

function leaked(text: string) {
  return formValues.filter((value) => text.includes(value));
}

afterEach(() => {
  for (const method of ["log", "info", "warn", "error", "debug"] as const) (console[method] as unknown as { mockRestore?: () => void }).mockRestore?.();
});

describe("reading a client's setup", () => {
  test("combines the computer and logins from the worker with the client's switch, uncached", async () => {
    const used = worker({ status: readAgentAnswer("kumo", contract.answers.computerWithLogins) });

    const response = await handleReadSetup(request({}), "kumo", deps(used.instance).built);

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ clientId: "kumo", computer: "ready", logins: { supplier: true, store: true }, useClaude: true });
  });

  test("shows a client without a computer, and Sora that cannot have one", async () => {
    const missing = worker({ status: readAgentAnswer("kumo", contract.answers.noComputer) });
    expect(await (await handleReadSetup(request({}), "kumo", deps(missing.instance).built)).json()).toEqual({
      clientId: "kumo",
      computer: "missing",
      logins: { supplier: false, store: false },
      useClaude: true,
    });
    const sora = worker({ status: readAgentAnswer("sora", contract.answers.noSites) });
    expect(await (await handleReadSetup(request({}), "sora", deps(sora.instance).built)).json()).toMatchObject({ computer: "unavailable" });
  });

  test("reports only the sites whose username and password are both there", async () => {
    const half = worker({ status: { computer: "ready", present: ["LP_SUPPLIER_USERNAME", "LP_SUPPLIER_PASSWORD", "LP_STORE_USERNAME"] } });
    const body = await (await handleReadSetup(request({}), "kumo", deps(half.instance).built)).json();
    expect(body.logins).toEqual({ supplier: true, store: false });
  });

  test("an unknown client is 404 and the worker is never asked", async () => {
    const used = worker();
    const response = await handleReadSetup(request({}), "acme", deps(used.instance).built);
    expect(response.status).toBe(404);
    expect(used.calls.status).toEqual([]);
  });

  test("a worker that cannot answer is 502 with a plain message", async () => {
    for (const failure of [new AgentFieldError("AgentField is not reachable"), new SetupWorkerError("kumo's computer reported key_refused.", "key_refused")]) {
      const { built, warnings } = deps(worker({ status: failure }).instance);
      const response = await handleReadSetup(request({}), "kumo", built);
      expect(response.status).toBe(502);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect((await response.json()).error).toBe("Couldn't reach the worker right now. Try again in a minute.");
      expect(warnings).toHaveLength(1);
    }
  });

  test("settings that cannot be trusted are 500, not a guess", async () => {
    const { built } = deps(worker().instance, {
      settings: {
        read: async () => {
          throw new SettingsError("The settings file is not JSON.");
        },
        write: async () => true,
      },
    });
    const response = await handleReadSetup(request({}), "kumo", built);
    expect(response.status).toBe(500);
  });

  test("the contract's failed answer is a worker error with its problem", () => {
    expect(() => readAgentAnswer("kumo", contract.answers.daytonaRefusedTheKey)).toThrow(SetupWorkerError);
    expect(() => readAgentAnswer("hinata", contract.answers.computerWithLogins)).toThrow(SetupWorkerError);
    expect(() => readAgentAnswer("kumo", { ...contract.answers.computerWithLogins, logins: { present: ["AWS_SECRET"], missing: [] } })).toThrow(SetupWorkerError);
  });
});

describe("remembering the login names instead of waking a computer", () => {
  test("a computer that is asleep is not woken; the last confirmed names are shown", async () => {
    const used = worker({ status: readAgentAnswer("kumo", contract.answers.computerAsleep) });
    const { built } = deps(used.instance, {}, { kumo: ["LP_STORE_USERNAME", "LP_STORE_PASSWORD"] });

    const body = await (await handleReadSetup(request({}), "kumo", built)).json();

    expect(body).toEqual({ clientId: "kumo", computer: "ready", logins: { supplier: false, store: true }, useClaude: true });
    expect(used.calls.status).toEqual(["kumo"]);
  });

  test("with nothing remembered, an asleep computer shows no logins yet", async () => {
    const used = worker({ status: readAgentAnswer("kumo", contract.answers.computerAsleep) });
    const body = await (await handleReadSetup(request({}), "kumo", deps(used.instance).built)).json();
    expect(body.logins).toEqual({ supplier: false, store: false });
  });

  test("names read from a running computer replace what was remembered", async () => {
    const used = worker({ status: readAgentAnswer("kumo", contract.answers.computerWithLogins) });
    const { built, memory } = deps(used.instance, {}, { kumo: [] });
    await handleReadSetup(request({}), "kumo", built);
    expect(memory.kumo).toEqual(allNames);
  });

  test("checking the logins again may wake the computer, and remembers what it found", async () => {
    const used = worker({ status: readAgentAnswer("kumo", contract.answers.computerWithLogins) });
    const { built, memory } = deps(used.instance, {}, { kumo: [] });

    const response = await handleCheckLogins(request(), "kumo", built);

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ clientId: "kumo", computer: "ready", logins: { supplier: true, store: true }, useClaude: true });
    expect(used.calls.status).toEqual(["kumo waking"]);
    expect(memory.kumo).toEqual(allNames);
  });

  test("only the ClientOps page may ask for a check, and only for a known client", async () => {
    const used = worker();
    expect((await handleCheckLogins(request({ ...sameOrigin, "sec-fetch-site": "cross-site" }), "kumo", deps(used.instance).built)).status).toBe(403);
    expect((await handleCheckLogins(request(), "acme", deps(used.instance).built)).status).toBe(404);
    expect(used.calls.status).toEqual([]);
  });

  test("a memory that cannot be written does not stop the answer", async () => {
    const used = worker({ status: readAgentAnswer("kumo", contract.answers.computerWithLogins) });
    const { built, warnings } = deps(used.instance, {
      memory: {
        read: async () => null,
        write: async () => {
          throw new SettingsError("CLIENTOPS_DATA_DIR must point outside the project.");
        },
      },
    });
    const response = await handleReadSetup(request({}), "kumo", built);
    expect(response.status).toBe(200);
    expect(warnings).toHaveLength(1);
  });
});

describe("creating the computer", () => {
  test("asks the worker to prepare that client's computer and answers the new setup", async () => {
    const used = worker();
    const { built, memory } = deps(used.instance);
    const response = await handlePrepareComputer(request(), "kumo", built);
    expect(memory).toEqual({ kumo: [] });
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ clientId: "kumo", computer: "ready", logins: { supplier: false, store: false }, useClaude: true });
    expect(used.calls.prepare).toEqual(["kumo"]);
  });

  test("only the ClientOps page itself may ask", async () => {
    const used = worker();
    const others: Record<string, string>[] = [
      { ...sameOrigin, "sec-fetch-site": "cross-site" },
      { ...sameOrigin, "sec-fetch-site": "same-site" },
      { origin: "http://evil.test", host: "app.test" },
      { host: "app.test" },
      {},
    ];
    for (const headers of others) {
      const response = await handlePrepareComputer(request(headers), "kumo", deps(used.instance).built);
      expect(response.status).toBe(403);
    }
    expect((await handlePrepareComputer(request({ origin: "http://app.test", host: "app.test" }), "kumo", deps(used.instance).built)).status).toBe(200);
    expect(used.calls.prepare).toEqual(["kumo"]);
  });

  test("a computer that cannot be created is 502", async () => {
    const used = worker({ prepare: new SetupWorkerError("kumo's computer reported key_refused.", "key_refused") });
    const response = await handlePrepareComputer(request(), "kumo", deps(used.instance).built);
    expect(response.status).toBe(502);
    expect((await response.json()).error).toBe("Couldn't create the computer right now. Try again in a minute.");
  });
});

describe("adding the logins", () => {
  test("hands the values to the worker under their placeholder names and answers names only", async () => {
    const used = worker();
    const spies = ["log", "info", "warn", "error", "debug"].map((method) => spyOn(console, method as "log"));
    const { built, warnings } = deps(used.instance);

    const response = await handleAddLogins(request(sameOrigin, JSON.stringify(supplierOnly)), "kumo", built);

    const text = await response.text();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(JSON.parse(text)).toEqual({ clientId: "kumo", computer: "ready", logins: { supplier: true, store: true }, useClaude: true });
    expect(used.calls.putLogins).toEqual([
      { clientId: "kumo", values: { LP_SUPPLIER_USERNAME: form.supplier.username, LP_SUPPLIER_PASSWORD: form.supplier.password } },
    ]);
    expect(leaked(text)).toEqual([]);
    expect(warnings).toEqual([]);
    expect(spies.flatMap((spy) => spy.mock.calls)).toEqual([]);
  });

  test("a site whose logins did not stay is not shown as added", async () => {
    const used = worker({ putLogins: { present: ["LP_SUPPLIER_USERNAME", "LP_SUPPLIER_PASSWORD"] } });
    const body = await (await handleAddLogins(request(sameOrigin, JSON.stringify(supplierOnly)), "kumo", deps(used.instance).built)).json();
    expect(body.logins).toEqual({ supplier: true, store: false });
  });

  test("both sites may still be sent together", async () => {
    const used = worker();
    const response = await handleAddLogins(request(sameOrigin, JSON.stringify(form)), "kumo", deps(used.instance).built);
    expect(response.status).toBe(200);
    expect(Object.keys(used.calls.putLogins[0].values)).toEqual(allNames);
  });

  test("the names the computer confirmed are remembered, never the values", async () => {
    const used = worker({ putLogins: { present: ["LP_SUPPLIER_USERNAME", "LP_SUPPLIER_PASSWORD"] } });
    const { built, memory } = deps(used.instance);
    await handleAddLogins(request(sameOrigin, JSON.stringify(supplierOnly)), "kumo", built);
    expect(memory).toEqual({ kumo: ["LP_SUPPLIER_USERNAME", "LP_SUPPLIER_PASSWORD"] });
    expect(leaked(JSON.stringify(memory))).toEqual([]);
  });

  const wrong: [string, Record<string, string>, string, number][] = [
    ["not JSON", sameOrigin, "{supplier", 400],
    ["no site at all", sameOrigin, JSON.stringify({}), 400],
    ["a site without its password", sameOrigin, JSON.stringify({ supplier: { username: "kumo-supply-user" } }), 400],
    ["an empty password", sameOrigin, JSON.stringify({ ...form, store: { username: "kumo-store-user", password: "" } }), 400],
    ["a blank username", sameOrigin, JSON.stringify({ ...form, store: { username: "   ", password: "kumo-store-pass-8Yu#" } }), 400],
    ["an extra field", sameOrigin, JSON.stringify({ ...form, admin: { username: "kumo-supply-user", password: "kumo-supply-pass-3Rt!" } }), 400],
    ["a value that is not text", sameOrigin, JSON.stringify({ ...form, store: { username: "kumo-store-user", password: 1234 } }), 400],
    ["a form post", { ...sameOrigin, "content-type": "application/x-www-form-urlencoded" }, "supplier=kumo-supply-pass-3Rt!", 415],
    ["far too much", sameOrigin, JSON.stringify({ ...form, store: { username: "kumo-store-user", password: "x".repeat(20_000) } }), 413],
    ["another site", { ...sameOrigin, "sec-fetch-site": "cross-site" }, JSON.stringify(form), 403],
  ];

  for (const [name, headers, body, status] of wrong) {
    test(`refuses ${name} without asking the worker or repeating the values`, async () => {
      const used = worker();
      const response = await handleAddLogins(request(headers, body), "kumo", deps(used.instance).built);
      expect(response.status).toBe(status);
      expect(leaked(await response.text())).toEqual([]);
      expect(used.calls.putLogins).toEqual([]);
    });
  }

  test("an unknown client is 404 before the body is read", async () => {
    const used = worker();
    const response = await handleAddLogins(request(sameOrigin, JSON.stringify(form)), "acme", deps(used.instance).built);
    expect(response.status).toBe(404);
    expect(used.calls.putLogins).toEqual([]);
  });

  test("a worker failure is 502 with a plain message; the log line names the problem but no value", async () => {
    const spies = ["log", "info", "warn", "error", "debug"].map((method) => spyOn(console, method as "log"));
    const used = worker({ putLogins: new LoginBridgeError("Could not put the logins into Kumo's computer (key_refused).") });
    const { built, warnings } = deps(used.instance);

    const response = await handleAddLogins(request(sameOrigin, JSON.stringify(form)), "kumo", built);

    const text = await response.text();
    expect(response.status).toBe(502);
    expect(JSON.parse(text).error).toBe("Couldn't add the logins. Try again in a minute.");
    expect(warnings).toEqual(["Setup for kumo: Could not put the logins into Kumo's computer (key_refused)."]);
    expect(leaked(text + warnings.join(""))).toEqual([]);
    expect(spies.flatMap((spy) => spy.mock.calls)).toEqual([]);
  });
});

describe("the Use Claude switch", () => {
  test("is saved for that client and answered back", async () => {
    const { built, writes } = deps(worker().instance);
    const response = await handleSetUseClaude(request(sameOrigin, JSON.stringify({ useClaude: false })), "kumo", built);
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ clientId: "kumo", useClaude: false });
    expect(writes).toEqual([["kumo", false]]);
  });

  test("anything but true or false is refused", async () => {
    for (const body of ['{"useClaude":"no"}', "{}", '{"useClaude":true,"extra":1}', "nope"]) {
      const { built, writes } = deps(worker().instance);
      const response = await handleSetUseClaude(request(sameOrigin, body), "kumo", built);
      expect(response.status).toBe(400);
      expect(writes).toEqual([]);
    }
  });

  test("only the ClientOps page may change it, and only for a known client", async () => {
    const { built, writes } = deps(worker().instance);
    expect((await handleSetUseClaude(request({ ...sameOrigin, "sec-fetch-site": "cross-site" }, '{"useClaude":false}'), "kumo", built)).status).toBe(403);
    expect((await handleSetUseClaude(request(sameOrigin, '{"useClaude":false}'), "acme", built)).status).toBe(404);
    expect(writes).toEqual([]);
  });

  test("settings that cannot be saved are 500", async () => {
    const { built } = deps(worker().instance, {
      settings: {
        read: async () => true,
        write: async () => {
          throw new SettingsError("CLIENTOPS_DATA_DIR must point outside the project.");
        },
      },
    });
    expect((await handleSetUseClaude(request(sameOrigin, '{"useClaude":false}'), "kumo", built)).status).toBe(500);
  });
});
