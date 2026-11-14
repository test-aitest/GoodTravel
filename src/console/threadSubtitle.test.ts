import { describe, expect, test } from "bun:test";
import { threadSubtitle } from "./threadSubtitle";

describe("threadSubtitle", () => {
  test("live jobs show the client's real schedule only when it is on", () => {
    expect(threadSubtitle({ clientName: "Kumo Coffee Roasters", jobsMode: "live", schedule: { isOn: true, time: "07:30", timeZone: "Asia/Tokyo" }, hasSavedSteps: true })).toBe(
      "Kumo Coffee Roasters · runs daily at 07:30",
    );
    expect(threadSubtitle({ clientName: "Kumo Coffee Roasters", jobsMode: "live", schedule: { isOn: false, time: "18:00", timeZone: "Asia/Tokyo" }, hasSavedSteps: true })).toBe(
      "Kumo Coffee Roasters",
    );
    expect(threadSubtitle({ clientName: "Kumo Coffee Roasters", jobsMode: "live", schedule: null, hasSavedSteps: true })).toBe("Kumo Coffee Roasters");
  });

  test("the playback demo keeps the canvas wording once steps are saved", () => {
    expect(threadSubtitle({ clientName: "Kumo Coffee Roasters", jobsMode: "playback", schedule: null, hasSavedSteps: true })).toBe(
      "Kumo Coffee Roasters · runs daily at 18:00",
    );
    expect(threadSubtitle({ clientName: "Kumo Coffee Roasters", jobsMode: "playback", schedule: null, hasSavedSteps: false })).toBe("Kumo Coffee Roasters");
  });
});
