import { describe, expect, test } from "bun:test";
import { destinations, playbackOptions, playbackSeats } from "./travelData";
import { initialTravelState, travelReducer, type TravelAction, type TravelState } from "./travelState";

const apply = (state: TravelState, ...actions: TravelAction[]) => actions.reduce(travelReducer, state);

describe("the trip moves forward by talking or tapping", () => {
  test("starts by asking where the trip is", () => {
    expect(initialTravelState.scene).toBe("where");
    expect(destinations.map((destination) => destination.name)).toEqual(["Kyoto", "Seoul", "Singapore"]);
  });

  test("saying a place picks it and waits for the worker to read the options", () => {
    const said = apply(initialTravelState, { type: "said", text: "Singapore next Saturday, for the trade show" });
    expect(said.destination).toBe("singapore");
    expect(said.scene).toBe("ride");
    expect(said.options).toBeNull();
    expect(said.lines.slice(-2)).toEqual([
      { from: "you", text: "Singapore next Saturday, for the trade show" },
      { from: "worker", text: "Looking at trips to Singapore on Hikari Travel…" },
    ]);
  });

  test("tapping a place does the same, and the options come from the worker", () => {
    const tapped = apply(initialTravelState, { type: "destinationPicked", destination: "seoul" });
    const arrived = apply(tapped, { type: "optionsRead", options: playbackOptions.seoul });
    expect(arrived.options?.map((option) => option.id)).toEqual(["ka-712", "ns-104"]);
    expect(arrived.pick).toBe("ka-712");
  });

  test("the earlier or later one, or its name, picks a ride and opens its seats with the worker's suggestion", () => {
    const ride = apply(initialTravelState, { type: "destinationPicked", destination: "singapore" }, { type: "optionsRead", options: playbackOptions.singapore });
    const later = apply(ride, { type: "said", text: "the later one" });
    expect(later.option).toBe("mr-12");
    const named = apply(ride, { type: "said", text: "KA 631 please" });
    expect(named.option).toBe("ka-631");
    expect(named.scene).toBe("seat");
    const seats = apply(named, { type: "seatsRead", seats: playbackSeats["ka-631"].seats, suggestion: playbackSeats["ka-631"].suggestion });
    expect(seats.seat).toBe("12C");
    expect(seats.suggestion).toEqual({ seat: "12C", reason: "exit-legroom" });
  });

  test("a seat can be tapped or said, only when it is free", () => {
    const seatScene = apply(
      initialTravelState,
      { type: "destinationPicked", destination: "singapore" },
      { type: "optionsRead", options: playbackOptions.singapore },
      { type: "optionPicked", option: "ka-631" },
      { type: "seatsRead", seats: playbackSeats["ka-631"].seats, suggestion: playbackSeats["ka-631"].suggestion },
    );
    expect(apply(seatScene, { type: "seatPicked", seat: "3F" }).seat).toBe("3F");
    expect(apply(seatScene, { type: "said", text: "Seat 20c is fine" }).seat).toBe("20C");
    const taken = playbackSeats["ka-631"].seats.find((seat) => !seat.available)?.id ?? "";
    expect(apply(seatScene, { type: "seatPicked", seat: taken }).seat).toBe("12C");
  });

  test("saying book, or yes, moves to the ticket, and holding it is the OK; the booking comes from the worker", () => {
    const seatScene = apply(
      initialTravelState,
      { type: "destinationPicked", destination: "singapore" },
      { type: "optionsRead", options: playbackOptions.singapore },
      { type: "optionPicked", option: "ka-631" },
      { type: "seatsRead", seats: playbackSeats["ka-631"].seats, suggestion: playbackSeats["ka-631"].suggestion },
    );
    const ticket = apply(seatScene, { type: "said", text: "Book it" });
    expect(ticket.scene).toBe("hold");
    const tappedAgain = apply(seatScene, { type: "said", text: "Book seat 12C" });
    expect(tappedAgain.scene).toBe("hold");
    expect(tappedAgain.seat).toBe("12C");
    expect(apply(seatScene, { type: "said", text: "Book seat 20C" }).scene).toBe("seat");
    const held = apply(ticket, { type: "held" });
    expect(held.isConfirming).toBe(true);
    const booked = apply(held, { type: "booked", reference: "KQ7MTR" });
    expect(booked.scene).toBe("booked");
    expect(booked.reference).toBe("KQ7MTR");
  });

  test("a message it cannot place gets a short hint and changes nothing else", () => {
    const said = apply(initialTravelState, { type: "said", text: "hmm" });
    expect(said.scene).toBe("where");
    expect(said.lines.at(-1)).toEqual({ from: "worker", text: "Tell me a place, like Kyoto, Seoul or Singapore." });
  });

  test("going back is a word away", () => {
    const ride = apply(initialTravelState, { type: "destinationPicked", destination: "kyoto" }, { type: "optionsRead", options: playbackOptions.kyoto });
    expect(apply(ride, { type: "said", text: "Actually, Seoul" }).destination).toBe("seoul");
    expect(apply(ride, { type: "said", text: "go back" }).scene).toBe("where");
  });
});

describe("seat maps", () => {
  test("a flight has rows 1 to 30 with A to F and exit rows 12 and 13; a train car has 16 rows with A to E", () => {
    const flight = playbackSeats["ka-631"].seats;
    expect(flight).toHaveLength(180);
    expect(flight.find((seat) => seat.id === "12A")).toMatchObject({ position: "window", exitRow: true });
    expect(flight.find((seat) => seat.id === "12C")).toMatchObject({ position: "aisle", exitRow: true, available: true });
    expect(flight.find((seat) => seat.id === "1C")).toMatchObject({ position: "aisle", exitRow: false });
    const rail = playbackSeats["nozomi-21"].seats;
    expect(rail).toHaveLength(80);
    expect(rail.find((seat) => seat.id === "3E")).toMatchObject({ position: "window" });
  });
});
