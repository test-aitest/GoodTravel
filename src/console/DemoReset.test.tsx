import { afterEach, describe, expect, test } from "bun:test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DemoReset } from "./DemoReset";

afterEach(cleanup);

describe("DemoReset", () => {
  test("one main button puts the demo back at the start and says what it did", async () => {
    let calls = 0;
    render(
      <DemoReset
        reset={async () => {
          calls += 1;
          return { clients: [{ clientId: "kumo", stopped: 1, sites: "reset", forgotten: 3, dailyRun: "off" }] };
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Reset demo" }));
    expect(await screen.findByText("The demo is back at the start.")).toBeTruthy();
    expect(screen.getByText("Kumo Coffee Roasters: stopped 1 waiting job, forgot 3 saved items, demo sites reset, daily run off.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to ClientOps" }).getAttribute("href")).toBe("/");
    expect(calls).toBe(1);
  });

  test("a refusal is shown as it was given", async () => {
    render(<DemoReset reset={async () => ({ error: "Demo reset is off. Start the worker with CLIENTOPS_DEMO_RESET=on to use it." })} />);
    fireEvent.click(screen.getByRole("button", { name: "Reset demo" }));
    expect(await screen.findByText("Demo reset is off. Start the worker with CLIENTOPS_DEMO_RESET=on to use it.")).toBeTruthy();
  });
});
