export type DestinationId = "kyoto" | "seoul" | "singapore";
export type RideKind = "rail" | "flight";
export type SeatPosition = "window" | "middle" | "aisle";
export type SeatReason = "window" | "aisle" | "quiet-front" | "exit-legroom" | "together";

export type Destination = { id: DestinationId; name: string; country: string; line: string; photo: string; tone: string };
export type TripOption = { id: string; name: string; kind: RideKind; depart: string; arrive: string; price: number; currency: string };
export type Seat = { id: string; row: number; letter: string; position: SeatPosition; exitRow: boolean; available: boolean };
export type SeatSuggestion = { seat: string; reason: SeatReason };

export const tripDate = "2026-11-21";
export const tripDay = "Sat 21 Nov";
export const tripFrom = "Tokyo";

export const destinations: readonly Destination[] = [
  { id: "kyoto", name: "Kyoto", country: "Japan", line: "2 h 15 by high-speed rail", photo: "/travel/photos/kyoto.jpg", tone: "#5B4636" },
  { id: "seoul", name: "Seoul", country: "South Korea", line: "2 h 35 by plane", photo: "/travel/photos/seoul.jpg", tone: "#3A4C66" },
  { id: "singapore", name: "Singapore", country: "Singapore", line: "7 h 30 by plane", photo: "/travel/photos/singapore.jpg", tone: "#1F5560" },
];

export const ridePhotos: Record<RideKind, string> = {
  rail: "/travel/photos/rail-fuji.jpg",
  flight: "/travel/photos/plane-clouds.jpg",
};

export const rideVideos: Record<RideKind, string> = {
  rail: "/travel/video/rail-fuji",
  flight: "/travel/video/plane-clouds",
};

export const playbackOptions: Record<DestinationId, readonly TripOption[]> = {
  kyoto: [
    { id: "nozomi-21", name: "Nozomi 21", kind: "rail", depart: "08:00", arrive: "10:15", price: 14170, currency: "JPY" },
    { id: "nozomi-27", name: "Nozomi 27", kind: "rail", depart: "09:00", arrive: "11:12", price: 14170, currency: "JPY" },
  ],
  seoul: [
    { id: "ka-712", name: "Kestrel Air KA 712", kind: "flight", depart: "08:30", arrive: "11:05", price: 38400, currency: "JPY" },
    { id: "ns-104", name: "Northstar NS 104", kind: "flight", depart: "10:15", arrive: "12:50", price: 35900, currency: "JPY" },
  ],
  singapore: [
    { id: "ka-631", name: "Kestrel Air KA 631", kind: "flight", depart: "08:05", arrive: "14:35", price: 72800, currency: "JPY" },
    { id: "mr-12", name: "Meridian MR 12", kind: "flight", depart: "10:40", arrive: "17:20", price: 68500, currency: "JPY" },
  ],
};

const layouts: Record<RideKind, { rows: number; letters: readonly string[]; positions: Record<string, SeatPosition>; exitRows: readonly number[] }> = {
  rail: { rows: 16, letters: ["A", "B", "C", "D", "E"], positions: { A: "window", B: "middle", C: "aisle", D: "aisle", E: "window" }, exitRows: [] },
  flight: { rows: 30, letters: ["A", "B", "C", "D", "E", "F"], positions: { A: "window", B: "middle", C: "aisle", D: "aisle", E: "middle", F: "window" }, exitRows: [12, 13] },
};

export function seatLetters(kind: RideKind) {
  return layouts[kind].letters;
}

export function seatRows(kind: RideKind) {
  return layouts[kind].rows;
}

function isTaken(optionId: string, row: number, letterIndex: number, id: string, keep: string) {
  const seed = [...optionId].reduce((sum, character) => sum + character.charCodeAt(0), 0);
  return id !== keep && (row * 7 + letterIndex * 5 + seed) % 4 === 0;
}

export function seatMap(option: TripOption, keep: string): Seat[] {
  const layout = layouts[option.kind];
  return Array.from({ length: layout.rows }, (_, index) => index + 1).flatMap((row) =>
    layout.letters.map((letter, letterIndex) => ({
      id: `${row}${letter}`,
      row,
      letter,
      position: layout.positions[letter] ?? "middle",
      exitRow: layout.exitRows.includes(row),
      available: !isTaken(option.id, row, letterIndex, `${row}${letter}`, keep),
    })),
  );
}

const suggestions: Record<RideKind, SeatSuggestion> = {
  rail: { seat: "3E", reason: "window" },
  flight: { seat: "12C", reason: "exit-legroom" },
};

export const playbackSeats: Record<string, { seats: Seat[]; suggestion: SeatSuggestion }> = Object.fromEntries(
  Object.values(playbackOptions)
    .flat()
    .map((option) => {
      const suggestion = suggestions[option.kind];
      return [option.id, { seats: seatMap(option, suggestion.seat), suggestion }];
    }),
);

export const reasonText: Record<SeatReason, string> = {
  window: "a window seat",
  aisle: "an aisle seat, easy to get out",
  "quiet-front": "near the front, where it is quieter",
  "exit-legroom": "an exit row seat with extra legroom",
  together: "seats together",
};

export function money(amount: number, currency: string) {
  return `${currency} ${new Intl.NumberFormat("en-US").format(amount)}`;
}

function minutesOf(time: string) {
  const [hours = 0, rest = 0] = time.split(":").map(Number);
  return hours * 60 + rest;
}

export function duration(depart: string, arrive: string) {
  const total = minutesOf(arrive) - minutesOf(depart);
  return `${Math.floor(total / 60)} h ${String(total % 60).padStart(2, "0")}`;
}

export const highlights: Record<DestinationId, readonly { name: string; crop: string }[]> = {
  kyoto: [
    { name: "Kiyomizu-dera", crop: "78% 40%" },
    { name: "Higashiyama views", crop: "20% 60%" },
    { name: "Autumn maples", crop: "40% 85%" },
  ],
  seoul: [
    { name: "Gyeongbokgung", crop: "50% 45%" },
    { name: "Palace courtyard", crop: "50% 95%" },
    { name: "Bugaksan ridge", crop: "10% 50%" },
  ],
  singapore: [
    { name: "Marina Bay", crop: "60% 40%" },
    { name: "ArtScience Museum", crop: "10% 70%" },
    { name: "Bay at night", crop: "50% 95%" },
  ],
};
