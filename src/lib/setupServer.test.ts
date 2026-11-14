import { describe, expect, test } from "bun:test";
import contract from "../../agent/tests/contract/setup.json";
import { fakeWorldNames } from "./fakeSetupWorker";
import { allLoginNames, loginNames, SetupWorkerError } from "./setup";
import { agentSetupWorker, externalsMode, FAKE_WORLD_COOKIE, setupContext } from "./setupServer";
import type { RequestLike } from "./setupRoutes";

const values: Record<string, string> = contract.logins.stdin;

function request(cookie?: string): RequestLike {
  return { headers: { get: (name: string) => (name.toLowerCase() === "cookie" ? (cookie ?? null) : null) }, text: async () => "" };
}

function worldCookie(setCookie: string | null) {
  const value = setCookie?.split(";")[0] ?? "";
  return value.startsWith(`${FAKE_WORLD_COOKIE}=`) ? value : "";
}

const fakeEnv = { CLIENTOPS_EXTERNALS: "fake", CLIENTOPS_DATA_DIR: "/tmp/clientops-unused" };

describe("which worker the setup routes use", () => {
  test("CLIENTOPS_EXTERNALS is real when unset or 'real', fake only when said, and anything else is an error", () => {
    expect(externalsMode({})).toBe("real");
    expect(externalsMode({ CLIENTOPS_EXTERNALS: "" })).toBe("real");
    expect(externalsMode({ CLIENTOPS_EXTERNALS: "real" })).toBe("real");
    expect(externalsMode({ CLIENTOPS_EXTERNALS: "fake" })).toBe("fake");
    expect(() => externalsMode({ CLIENTOPS_EXTERNALS: "mock" })).toThrow();
  });
});

describe("the fake worker, one world per browser", () => {
  test("a browser without the cookie gets its own new world and the cookie for it", () => {
    const context = setupContext(request(), fakeEnv);
    const response = context.respond(new Response("{}"));
    const cookie = response.headers.get("set-cookie") ?? "";
    expect(cookie).toMatch(new RegExp(`^${FAKE_WORLD_COOKIE}=[0-9a-f-]{36}; Path=/; HttpOnly; SameSite=Strict$`));
  });

  test("a new world has Hinata set up, Kumo without a computer, and Sora unable to have one", async () => {
    const { deps } = setupContext(request(), fakeEnv);
    expect(await deps.worker.status("hinata")).toEqual({ computer: "ready", present: allLoginNames });
    expect(await deps.worker.status("kumo")).toEqual({ computer: "missing", present: [] });
    expect(await deps.worker.status("sora")).toEqual({ computer: "unavailable", present: [] });
  });

  test("the same cookie keeps the same world; another browser does not see it", async () => {
    const first = setupContext(request(), fakeEnv);
    const cookie = worldCookie(first.respond(new Response("{}")).headers.get("set-cookie"));
    await first.deps.worker.prepare("kumo");

    const again = setupContext(request(cookie), fakeEnv);
    expect(again.respond(new Response("{}")).headers.get("set-cookie")).toBeNull();
    expect(await again.deps.worker.status("kumo")).toEqual({ computer: "ready", present: [] });
    expect(await setupContext(request(), fakeEnv).deps.worker.status("kumo")).toEqual({ computer: "missing", present: [] });
  });

  test("a cookie that is not a world id starts a new world", async () => {
    const context = setupContext(request(`${FAKE_WORLD_COOKIE}=../../etc; other=1`), fakeEnv);
    expect(context.respond(new Response("{}")).headers.get("set-cookie")).toContain(FAKE_WORLD_COOKIE);
  });

  test("logins keep their names only, and one site added later keeps the other, like the real vault", async () => {
    const context = setupContext(request(), fakeEnv);
    const cookie = worldCookie(context.respond(new Response("{}")).headers.get("set-cookie"));
    await context.deps.worker.prepare("kumo");

    expect(await context.deps.worker.putLogins("kumo", { [loginNames.store.username]: "a", [loginNames.store.password]: "b" })).toEqual({
      present: [loginNames.store.username, loginNames.store.password],
    });
    expect(await context.deps.worker.putLogins("kumo", { [loginNames.supplier.username]: "c", [loginNames.supplier.password]: "d" })).toEqual({
      present: allLoginNames,
    });
    expect(await context.deps.worker.putLogins("kumo", values)).toEqual({ present: allLoginNames });
    const kept = JSON.stringify(fakeWorldNames(cookie.split("=")[1]));
    expect(Object.values(values).filter((value) => kept.includes(value))).toEqual([]);
  });

  test("Sora cannot be given logins", async () => {
    const { deps } = setupContext(request(), fakeEnv);
    await expect(deps.worker.putLogins("sora", values)).rejects.toBeInstanceOf(SetupWorkerError);
    await expect(deps.worker.prepare("sora")).resolves.toEqual({ computer: "unavailable", present: [] });
  });
});

describe("the real worker", () => {
  test("asks AgentField for the setup skills and sends logins through the bridge", async () => {
    const asked: string[] = [];
    const bodies: unknown[] = [];
    const fetcher = (async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      asked.push(`${init?.method ?? "GET"} ${url}`);
      if (init?.body) bodies.push(JSON.parse(String(init.body)));
      if (url.includes("/execute/async/")) return Response.json({ execution_id: "exec_1", run_id: "run_1" }, { status: 202 });
      const skill = asked.find((line) => line.includes("/execute/async/"))?.split("clientops.")[1];
      const result = skill === "prepare_computer" ? contract.answers.computerWithoutLogins : contract.answers.computerWithLogins;
      return Response.json({ execution_id: "exec_1", status: "succeeded", result });
    }) as typeof fetch;
    const config = { baseUrl: "http://agentfield.test", fetch: fetcher };
    const bridge = {
      command: [process.execPath, "-e", `console.log(JSON.stringify(${JSON.stringify(contract.logins.added)}))`],
      cwd: process.cwd(),
      env: { PATH: process.env.PATH },
      timeoutMs: 10_000,
    };
    const worker = agentSetupWorker(config, bridge);

    expect(await worker.status("kumo")).toEqual({ computer: "ready", present: allLoginNames });
    expect(asked[0]).toBe("POST http://agentfield.test/api/v1/execute/async/clientops.setup_status");
    expect(bodies[0]).toEqual({ input: { client_id: "kumo", wake: false } });
    await worker.status("kumo", { wake: true });
    expect(bodies.at(-1)).toEqual({ input: { client_id: "kumo", wake: true } });
    asked.length = 0;
    expect(await worker.prepare("kumo")).toEqual({ computer: "ready", present: [] });
    expect(asked[0]).toBe("POST http://agentfield.test/api/v1/execute/async/clientops.prepare_computer");
    const added: unknown = await worker.putLogins("kumo", values);
    expect(added).toEqual({ present: allLoginNames, missing: [] });
  });
});
