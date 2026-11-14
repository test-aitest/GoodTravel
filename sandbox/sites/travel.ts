import type { ClientId } from "./catalog";

export const travelHost = "travel.hikari-travel.example";
export const travelOrigin = "Tokyo";
export const travelDate = "2026-11-21";

export type Destination = "Kyoto" | "Seoul" | "Singapore";
export type RideKind = "rail" | "flight";
export type SeatPosition = "window" | "middle" | "aisle";

export const destinations: Destination[] = ["Kyoto", "Seoul", "Singapore"];

export type TravelOption = {
  id: string;
  kind: RideKind;
  name: string;
  from: string;
  to: Destination;
  date: string;
  departs: string;
  arrives: string;
  price: number;
};

export type Seat = { id: string; row: number; letter: string; position: SeatPosition; exitRow: boolean; taken: boolean };

type OptionRow = [id: string, kind: RideKind, name: string, to: Destination, departs: string, arrives: string, price: number];

const optionRows: OptionRow[] = [
  ["nozomi-21", "rail", "Nozomi 21", "Kyoto", "08:00", "10:15", 14170],
  ["nozomi-27", "rail", "Nozomi 27", "Kyoto", "09:00", "11:12", 14170],
  ["ka-712", "flight", "Kestrel Air KA 712", "Seoul", "08:30", "11:05", 38400],
  ["ns-104", "flight", "Northstar NS 104", "Seoul", "10:15", "12:50", 35900],
  ["ka-631", "flight", "Kestrel Air KA 631", "Singapore", "08:05", "14:35", 72800],
  ["mr-12", "flight", "Meridian MR 12", "Singapore", "10:40", "17:20", 68500],
];

export const travelOptions: TravelOption[] = optionRows.map(([id, kind, name, to, departs, arrives, price]) => ({
  id,
  kind,
  name,
  from: travelOrigin,
  to,
  date: travelDate,
  departs,
  arrives,
  price,
}));

export const kindLabels: Record<RideKind, string> = { rail: "Rail", flight: "Flight" };

export const railCar = 7;
export const exitRows = [12, 13];

type Layout = { rows: number; letters: string[]; positions: Record<string, SeatPosition> };

export const seatLayouts: Record<RideKind, Layout> = {
  rail: {
    rows: 16,
    letters: ["A", "B", "C", "D", "E"],
    positions: { A: "window", B: "middle", C: "aisle", D: "aisle", E: "window" },
  },
  flight: {
    rows: 30,
    letters: ["A", "B", "C", "D", "E", "F"],
    positions: { A: "window", B: "middle", C: "aisle", D: "aisle", E: "middle", F: "window" },
  },
};

const takenSeatsByOption: Record<string, string> = {
  "nozomi-21":
    "1A 1B 1C 1D 1E 2A 2B 2E 3A 3C 3D 3E 4A 4E 5A 5B 5E 6D 6E 7A 7E 8A 8C 9E 10A 10B 11E 12A 12D 13E 14A 14B 15C 15E 16A",
  "nozomi-27": "1A 1E 2D 2E 3A 4B 4C 5E 6A 7D 8E 9A 9B 10C 11A 12E 13D 14A 15E 16B",
  "ka-631":
    "1A 1B 1C 1D 1E 1F 2A 2C 2D 2F 3A 3F 4A 4B 4E 4F 5A 5D 6F 7A 7B 7C 8F 9A 9E 10A 10F 11C 11D 12A 12B 12F 13A 13F 14A 14F 15B 15C 16A 16F 17D 18A 18F 19B 19E 20A 21F 22A 22C 23F 24A 24D 25F 26A 27C 27F 28A 29E 30A 30F",
  "ka-712":
    "1A 1F 2A 2B 2F 3C 3D 4A 4F 5E 6A 6F 7B 8A 8D 9F 10A 11E 12A 13F 14C 15A 15F 16D 17A 18F 19A 20E 21C 22F 23A 24B 25F 26A 27D 28F 29B 30C",
  "mr-12":
    "1A 1B 1C 1D 1E 1F 2A 2B 2C 2D 2E 2F 3A 3B 3E 3F 4A 4C 4D 4F 5A 5F 6A 6B 6E 6F 7A 7F 8A 8C 8D 8F 9A 9F 10A 10B 10F 11A 11F 12A 12B 12C 12F 13A 13D 13E 13F 14A 14F 15A 15F 16A 16E 16F 17A 17F 18A 18B 18F 19A 19F 20A 20F 21A 21D 21F 22A 22F 23A 23F 24A 24F 25A 25F 26A 26C 26F 27A 27F 28A 28F 29A 30A 30F",
  "ns-104": "1C 1D 2A 3F 4B 5A 6E 7F 8A 9C 10D 11F 12E 13B 14A 15F 17C 18A 20F 21B 23A 24E 26F 27A 28C 29F",
};

export function findOption(id: string | null | undefined) {
  return travelOptions.find((option) => option.id === id);
}

export function optionsFor(from: string, to: string, date: string) {
  return travelOptions.filter((option) => option.from === from && option.to === to && option.date === date);
}

export function takenSeats(optionId: string) {
  return (takenSeatsByOption[optionId] ?? "").split(" ").filter((seat) => seat.length > 0);
}

export function seatsFor(option: TravelOption, booked: ReadonlySet<string> = new Set()): Seat[] {
  const layout = seatLayouts[option.kind];
  const taken = new Set([...takenSeats(option.id), ...booked]);
  return Array.from({ length: layout.rows }, (_, index) => index + 1).flatMap((row) =>
    layout.letters.map((letter) => {
      const id = `${row}${letter}`;
      return {
        id,
        row,
        letter,
        position: layout.positions[letter],
        exitRow: option.kind === "flight" && exitRows.includes(row),
        taken: taken.has(id),
      };
    }),
  );
}

export function seatName(seat: Seat) {
  return `Seat ${seat.id}, ${seat.position}${seat.exitRow ? ", exit row" : ""}, ${seat.taken ? "taken" : "available"}`;
}

export const priceText = (amount: number) => `JPY ${amount.toLocaleString("en-US")}`;

export function optionLine(option: TravelOption) {
  return `${kindLabels[option.kind]} · ${option.departs} → ${option.arrives} · ${priceText(option.price)}`;
}

export type Hold = {
  id: string;
  client: ClientId;
  optionId: string;
  seat: string;
  fullName: string;
  email: string;
  reference?: string;
};

export type HoldResult = { ok: true; hold: Hold } | { ok: false; status: number; error: string };
export type ConfirmResult = { ok: true; reference: string } | { ok: false; status: number; error: string };

const referenceLetters = "ABCDEFGHJKLMNPQRSTUVWXYZ";
const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function randomReference() {
  return Array.from(crypto.getRandomValues(new Uint8Array(6)), (byte) => referenceLetters[byte % referenceLetters.length]).join("");
}

export class TravelBookings {
  private holds = new Map<string, Hold>();

  reset() {
    this.holds.clear();
  }

  bookedSeats(optionId: string) {
    return new Set([...this.holds.values()].filter((hold) => hold.optionId === optionId && hold.reference).map((hold) => hold.seat));
  }

  seats(option: TravelOption) {
    return seatsFor(option, this.bookedSeats(option.id));
  }

  hold(client: ClientId, optionId: string, seatId: string, fullName: string, email: string): HoldResult {
    const option = findOption(optionId);
    if (!option) return { ok: false, status: 404, error: "This trip is no longer offered." };
    const seat = this.seats(option).find((candidate) => candidate.id === seatId);
    if (!seat) return { ok: false, status: 400, error: "Choose a seat on the map." };
    if (seat.taken) return { ok: false, status: 409, error: `Seat ${seat.id} is taken. Choose another seat.` };
    const name = fullName.trim();
    const address = email.trim();
    if (name.length === 0 || name.length > 100) return { ok: false, status: 400, error: "Enter the passenger's full name." };
    if (!emailPattern.test(address) || address.length > 200) return { ok: false, status: 400, error: "Enter a valid email address." };
    const hold: Hold = { id: crypto.randomUUID().replaceAll("-", "").slice(0, 10), client, optionId, seat: seat.id, fullName: name, email: address };
    this.holds.set(hold.id, hold);
    return { ok: true, hold };
  }

  holdFor(client: ClientId, id: string | null) {
    const hold = id ? this.holds.get(id) : undefined;
    return hold?.client === client ? hold : undefined;
  }

  confirm(client: ClientId, holdId: string): ConfirmResult {
    const hold = this.holdFor(client, holdId);
    if (!hold) return { ok: false, status: 404, error: "This hold has expired. Choose your seat again." };
    if (hold.reference) return { ok: true, reference: hold.reference };
    if (this.bookedSeats(hold.optionId).has(hold.seat)) {
      return { ok: false, status: 409, error: `Seat ${hold.seat} was just booked by someone else. Choose another seat.` };
    }
    const used = new Set([...this.holds.values()].flatMap((other) => (other.reference ? [other.reference] : [])));
    let reference = randomReference();
    while (used.has(reference)) reference = randomReference();
    hold.reference = reference;
    return { ok: true, reference };
  }

  bookingFor(client: ClientId, reference: string | null) {
    return [...this.holds.values()].find((hold) => hold.client === client && hold.reference !== undefined && hold.reference === reference);
  }
}
