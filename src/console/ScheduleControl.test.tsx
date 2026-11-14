import { afterEach, describe, expect, test } from "bun:test";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { scheduleText, type ClientSchedule } from "@/lib/schedule";
import { clientById } from "./clients";
import type { ScheduleApi, ScheduleChange, ScheduleReply } from "./scheduleApi";
import { ScheduleControl } from "./ScheduleControl";

afterEach(cleanup);

const kumo = clientById("kumo");

function reply(schedule: ClientSchedule): ScheduleReply {
  return { clientId: "kumo", schedule, text: scheduleText(schedule), runs: [] };
}

function fakeApi(start: ClientSchedule = { isOn: true, time: "18:00", timeZone: "Asia/Tokyo" }, options: { failSave?: string; holdLoad?: boolean } = {}) {
  let current = start;
  const saves: ScheduleChange[] = [];
  const { promise: loaded, resolve: releaseLoad } = Promise.withResolvers<void>();
  const api: ScheduleApi = {
    load: async () => {
      if (options.holdLoad) await loaded;
      return reply(current);
    },
    save: async (_clientId, change) => {
      saves.push(change);
      if (options.failSave) throw new Error(options.failSave);
      current = { ...current, ...change };
      return reply(current);
    },
  };
  return { api, saves, releaseLoad };
}

function primaries(scope: HTMLElement) {
  return Array.from(scope.querySelectorAll('button[data-variant="primary"]')).map((button) => button.textContent);
}

async function openDialog() {
  fireEvent.click(await screen.findByRole("button", { name: "Change" }));
  return screen.getByRole("dialog", { name: "Daily run" });
}

describe("ScheduleControl", () => {
  test("shows the real schedule once it is known", async () => {
    render(<ScheduleControl client={kumo} api={fakeApi().api} />);
    expect(await screen.findByText("Runs daily at 18:00")).toBeTruthy();
  });

  test("says Not scheduled when the schedule is off", async () => {
    render(<ScheduleControl client={kumo} api={fakeApi({ isOn: false, time: "18:00", timeZone: "Asia/Tokyo" }).api} />);
    expect(await screen.findByText("Not scheduled")).toBeTruthy();
  });

  test("while the schedule is loading it shows no time and Change waits", async () => {
    const used = fakeApi(undefined, { holdLoad: true });
    render(<ScheduleControl client={kumo} api={used.api} />);
    expect(screen.queryByText("Runs daily at 18:00")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await act(async () => used.releaseLoad());
    expect(await screen.findByText("Runs daily at 18:00")).toBeTruthy();
  });

  test("Change opens a small dialog with the time, the client's time zone and one main button", async () => {
    render(<ScheduleControl client={kumo} api={fakeApi().api} />);
    const dialog = await openDialog();
    expect(within(dialog).getByText("Kumo Coffee's worker gets the changes ready, then waits for your OK.")).toBeTruthy();
    expect((within(dialog).getByRole("checkbox", { name: "Run every day" }) as HTMLInputElement).checked).toBe(true);
    expect((within(dialog).getByLabelText("Time") as HTMLInputElement).value).toBe("18:00");
    expect(within(dialog).getByText("Japan Standard Time")).toBeTruthy();
    expect(primaries(dialog)).toEqual(["Save"]);
  });

  test("a new time is saved and the line says it", async () => {
    const used = fakeApi();
    render(<ScheduleControl client={kumo} api={used.api} />);
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Time"), { target: { value: "07:30" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Runs daily at 07:30")).toBeTruthy();
    expect(used.saves).toEqual([{ isOn: true, time: "07:30" }]);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  test("turning it off is saved and the line says Not scheduled", async () => {
    const used = fakeApi();
    render(<ScheduleControl client={kumo} api={used.api} />);
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Run every day" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Not scheduled")).toBeTruthy();
    expect(used.saves).toEqual([{ isOn: false, time: "18:00" }]);
  });

  test("Cancel closes it without saving", async () => {
    const used = fakeApi();
    render(<ScheduleControl client={kumo} api={used.api} />);
    const dialog = await openDialog();
    fireEvent.change(within(dialog).getByLabelText("Time"), { target: { value: "07:30" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(used.saves).toEqual([]);
    expect(screen.getByText("Runs daily at 18:00")).toBeTruthy();
  });

  test("when saving fails it says why and keeps the dialog open", async () => {
    render(<ScheduleControl client={kumo} api={fakeApi(undefined, { failSave: "The schedule could not be saved." }).api} />);
    const dialog = await openDialog();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await within(dialog).findByText("The schedule could not be saved.")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Daily run" })).toBeTruthy();
    expect(screen.getByText("Runs daily at 18:00")).toBeTruthy();
  });

  test("every line for the same client changes together", async () => {
    const used = fakeApi();
    render(
      <>
        <ScheduleControl client={kumo} api={used.api} />
        <ScheduleControl client={kumo} api={used.api} />
      </>,
    );
    await waitFor(() => expect(screen.getAllByText("Runs daily at 18:00")).toHaveLength(2));
    fireEvent.click(screen.getAllByRole("button", { name: "Change" })[0]);
    const dialog = screen.getByRole("dialog", { name: "Daily run" });
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Run every day" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getAllByText("Not scheduled")).toHaveLength(2));
  });
});
