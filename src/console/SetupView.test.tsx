import { afterEach, describe, expect, test } from "bun:test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ClientSetup } from "@/lib/setup";
import { clientById } from "./clients";
import type { ClientSetupState, ConsoleAction } from "./consoleState";
import { SetupView } from "./SetupView";
import type { SetupApi } from "./setupApi";

afterEach(cleanup);

const kumo = clientById("kumo");

function fromServer(computer: ClientSetup["computer"], supplier = false, store = false): ClientSetup {
  return { clientId: "kumo", computer, logins: { supplier, store }, useClaude: true };
}

function state(computer: ClientSetup["computer"], supplier = false, store = false): ClientSetupState {
  return { ...fromServer(computer, supplier, store), isFinished: false };
}

function api(overrides: Partial<SetupApi> = {}) {
  const calls: string[] = [];
  const built: SetupApi = {
    load: async () => fromServer("missing"),
    createComputer: async (clientId) => {
      calls.push(`create ${clientId}`);
      return { ...fromServer("ready"), clientId };
    },
    addLogin: async (clientId, site, login) => {
      calls.push(`login ${clientId} ${site} ${Object.keys(login).join(",")}`);
      return { ...fromServer("ready", site === "supplier", site === "store"), clientId };
    },
    checkLogins: async (clientId) => {
      calls.push(`check ${clientId}`);
      return { ...fromServer("ready", true, false), clientId };
    },
    setUseClaude: async (clientId, useClaude) => {
      calls.push(`claude ${clientId} ${useClaude}`);
      return useClaude;
    },
    ...overrides,
  };
  return { built, calls };
}

function primaries() {
  return Array.from(document.querySelectorAll('button[data-variant="primary"]')).map((button) => button.textContent);
}

function view(setup: ClientSetupState | undefined, options: { problem?: string; client?: ReturnType<typeof clientById>; setupApi?: SetupApi } = {}) {
  const actions: ConsoleAction[] = [];
  const retries: number[] = [];
  render(
    <SetupView
      client={options.client ?? kumo}
      setup={setup}
      problem={options.problem}
      onRetry={() => retries.push(1)}
      dispatch={(action) => actions.push(action)}
      api={options.setupApi ?? api().built}
    />,
  );
  return { actions, retries };
}

describe("SetupView", () => {
  test("checks with the server before showing any step", () => {
    view(undefined);
    expect(screen.getByText("Checking Kumo Coffee's computer…")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Set up Kumo Coffee's worker" })).toBeNull();
    expect(primaries()).toEqual([]);
  });

  test("a server that cannot answer offers one way to try again", () => {
    const { retries } = view(undefined, { problem: "Couldn't reach the worker right now. Try again in a minute." });
    expect(screen.getByText("Couldn't reach the worker right now. Try again in a minute.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retries).toEqual([1]);
    expect(primaries()).toEqual(["Try again"]);
  });

  test("without a computer the one main button creates it, and the answer comes from the server", async () => {
    const used = api();
    const { actions } = view(state("missing"), { setupApi: used.built });
    expect(screen.getByRole("heading", { level: 1, name: "Set up Kumo Coffee's worker" })).toBeTruthy();
    expect(primaries()).toEqual(["Create its computer"]);
    expect(screen.queryByText("Maruyama Supply portal")).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Create its computer" }));
    });

    expect(used.calls).toEqual(["create kumo"]);
    expect(actions).toEqual([{ type: "setupLoaded", clientId: "kumo", setup: fromServer("ready") }]);
  });

  test("a computer that could not be created says so and keeps the step open", async () => {
    const used = api({
      createComputer: async () => {
        throw new Error("Couldn't create the computer right now. Try again in a minute.");
      },
    });
    const { actions } = view(state("missing"), { setupApi: used.built });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Create its computer" }));
    });
    expect(screen.getByText("Couldn't create the computer right now. Try again in a minute.")).toBeTruthy();
    expect(actions).toEqual([]);
  });

  test("with the computer ready, each site has its own Add login and only the first is the main button", async () => {
    const used = api({ checkLogins: async () => fromServer("ready") });
    await act(async () => {
      view(state("ready"), { setupApi: used.built });
    });
    expect(screen.getByText("Maruyama Supply portal")).toBeTruthy();
    expect(screen.getByText("Kumo store admin")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add login for Maruyama Supply portal" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add login for Kumo store admin" })).toBeTruthy();
    expect(primaries()).toEqual(["Add login"]);
    expect(screen.getByRole("switch", { name: /Use Claude for new steps/ })).toBeTruthy();
  });

  test("opening the login step checks the logins on the computer once and shows what it found", async () => {
    const used = api();
    let actions: ConsoleAction[] = [];
    await act(async () => {
      actions = view(state("ready"), { setupApi: used.built }).actions;
    });
    expect(used.calls).toEqual(["check kumo"]);
    expect(actions).toEqual([{ type: "setupLoaded", clientId: "kumo", setup: fromServer("ready", true, false) }]);
  });

  test("an added site says Added and can be replaced; the other site keeps its own button", async () => {
    await act(async () => {
      view(state("ready", true, false), { setupApi: api({ checkLogins: async () => fromServer("ready", true, false) }).built });
    });
    expect(screen.getAllByText("Added")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Replace login for Maruyama Supply portal" })).toBeTruthy();
    expect(primaries()).toEqual(["Add login"]);
  });

  test("one site's login goes to the server alone, and nothing typed stays on the page", async () => {
    const used = api({ checkLogins: async () => fromServer("ready") });
    let actions: ConsoleAction[] = [];
    await act(async () => {
      actions = view(state("ready"), { setupApi: used.built }).actions;
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Add login for Kumo store admin" }));
    });
    const dialog = screen.getByRole("dialog", { name: "Add the Kumo store admin login" });
    expect(dialog).toBeTruthy();
    expect(screen.getByLabelText("Password").getAttribute("type")).toBe("password");
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "kumo-store-unit" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "kumo-store-unit-Pass!" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Add login" }));
    });

    expect(used.calls.at(-1)).toBe("login kumo store username,password");
    expect(actions.at(-1)).toEqual({ type: "setupLoaded", clientId: "kumo", setup: fromServer("ready", false, true) });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.innerHTML).not.toContain("kumo-store-unit-Pass!");
  });

  test("the Use Claude switch is saved on the server before it changes", async () => {
    const used = api();
    let actions: ConsoleAction[] = [];
    await act(async () => {
      actions = view(state("ready"), { setupApi: used.built }).actions;
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("switch", { name: /Use Claude for new steps/ }));
    });
    expect(used.calls).toEqual(["check kumo", "claude kumo false"]);
    expect(actions.at(-1)).toEqual({ type: "useClaudeChanged", clientId: "kumo", useClaude: false });
  });

  test("with both logins added the one main button writes the first job, and nothing is checked again", () => {
    const used = api();
    const { actions } = view(state("ready", true, true), { setupApi: used.built });
    expect(screen.getAllByText("Added")).toHaveLength(2);
    expect(primaries()).toEqual(["Write the first job"]);
    fireEvent.click(screen.getByRole("button", { name: "Write the first job" }));
    expect(actions).toEqual([{ type: "finishSetup" }]);
    expect(used.calls).toEqual([]);
  });

  test("Sora cannot have a computer and says so plainly, with nothing to press", () => {
    view({ ...state("unavailable"), clientId: "sora" }, { client: clientById("sora") });
    expect(screen.getByText("Sora Outdoor can't have its own computer yet")).toBeTruthy();
    expect(screen.getByText("Its supplier and store sites aren't connected to ClientOps.")).toBeTruthy();
    expect(primaries()).toEqual([]);
    expect(screen.queryByRole("button", { name: "Create its computer" })).toBeNull();
  });
});
