import { afterEach, describe, expect, mock, test } from "bun:test";
import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { playbackOptions, playbackSeats } from "./travelData";
import { GuideVideo } from "./GuideVideo";
import { HoldTicket } from "./HoldTicket";
import { firstVisibleRow, SeatMap } from "./SeatMap";
import { TopBar } from "./TravelApp";
import { bestVoice } from "./voice";

afterEach(cleanup);

const flight = playbackOptions.singapore[0]!;
const { seats, suggestion } = playbackSeats["ka-631"]!;

describe("SeatMap", () => {
  test("shows a close-up of nine rows around the chosen seat, each seat a button named by row, letter, position and whether it is free", () => {
    render(<SeatMap kind="flight" seats={seats} suggestion={suggestion} chosen="12C" onPick={() => {}} />);
    expect(screen.getByRole("group", { name: "Seat map, rows 8 to 16" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /^Seat / })).toHaveLength(54);
    expect(screen.getByRole("button", { name: "Seat 12C, aisle, exit row, available, suggested, your choice" }).getAttribute("aria-pressed")).toBe("true");
    const taken = seats.find((seat) => !seat.available && seat.row >= 8 && seat.row <= 16)!;
    expect((screen.getByRole("button", { name: new RegExp(`^Seat ${taken.id}, .*taken$`) }) as HTMLButtonElement).disabled).toBe(true);
  });

  test("tapping a free seat picks it, and the whole-plane map moves the close-up", () => {
    const onPick = mock(() => {});
    render(<SeatMap kind="flight" seats={seats} suggestion={suggestion} chosen="12C" onPick={onPick} />);
    const free = seats.find((seat) => seat.available && seat.row === 14)!;
    fireEvent.click(screen.getByRole("button", { name: new RegExp(`^Seat ${free.id},`) }));
    expect(onPick).toHaveBeenCalledWith(free.id);
    expect(firstVisibleRow(29, 30)).toBe(22);
    expect(firstVisibleRow(1, 30)).toBe(1);
  });
});

describe("HoldTicket", () => {
  test("shows the trip, and only a full hold books it; letting go early does nothing", async () => {
    const onConfirm = mock(() => {});
    render(<HoldTicket from="Tokyo" to="Singapore" option={flight} seat="12C" passenger="Haruka Mori" holdMs={60} onConfirm={onConfirm} />);
    expect(screen.getByText("Kestrel Air KA 631")).toBeTruthy();
    expect(screen.getByText("JPY 72,800")).toBeTruthy();
    const hold = screen.getByRole("button", { name: "Hold to book" });
    fireEvent.pointerDown(hold);
    fireEvent.pointerUp(hold);
    await act(() => new Promise((resolve) => setTimeout(resolve, 100)));
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.keyDown(hold, { key: " " });
    await act(() => new Promise((resolve) => setTimeout(resolve, 120)));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

describe("the city guide", () => {
  test("talks once when the city opens, even when React starts it twice, and then says it is done", async () => {
    const realPlay = HTMLMediaElement.prototype.play;
    const plays = mock(function (this: HTMLMediaElement) {
      setTimeout(() => this.dispatchEvent(new Event("ended")), 5);
      return Promise.resolve();
    });
    HTMLMediaElement.prototype.play = plays;
    const done = mock(() => {});
    try {
      render(
        <StrictMode>
          <GuideVideo name="singapore-detail" poster="/travel/guide/singapore-guide.jpg" place="Singapore" isActive onDone={done} />
        </StrictMode>,
      );
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 60));
      });
      expect(plays).toHaveBeenCalledTimes(1);
      expect(done).toHaveBeenCalledTimes(1);
    } finally {
      HTMLMediaElement.prototype.play = realPlay;
    }
  });

  test("the live dot on the guide's card blinks", async () => {
    const { container } = render(<GuideVideo name="singapore-detail" poster="/travel/guide/singapore-guide.jpg" place="Singapore" isActive={false} />);
    const dot = container.querySelector<HTMLElement>("[data-live-dot]");
    expect(dot).not.toBeNull();
    const seen = new Set<string>();
    for (let tick = 0; tick < 8; tick += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 120));
      });
      seen.add(getComputedStyle(dot!).opacity);
    }
    expect(seen.size).toBeGreaterThan(2);
  });
});

describe("the top bar", () => {
  test("shows only the trip, not the engine's name", () => {
    render(<TopBar right="Tokyo → Singapore · Sat 21 Nov" />);
    expect(screen.getByText("Tokyo → Singapore · Sat 21 Nov")).toBeDefined();
    expect(screen.queryByText(/clientops/i)).toBeNull();
  });
});

describe("the worker's voice", () => {
  test("picks the most natural English voice the device has", () => {
    const voices = [
      { name: "Fred", lang: "en-US" },
      { name: "Google US English", lang: "en-US" },
      { name: "Ava (Premium)", lang: "en-US" },
      { name: "Kyoko", lang: "ja-JP" },
    ];
    expect(bestVoice(voices)?.name).toBe("Ava (Premium)");
    expect(bestVoice(voices.filter((voice) => !voice.name.startsWith("Ava")))?.name).toBe("Google US English");
    expect(bestVoice([{ name: "Kyoko", lang: "ja-JP" }])).toBeNull();
  });
});
