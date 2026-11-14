import { destinations, reasonText, tripDay, type DestinationId, type Seat, type SeatSuggestion, type TripOption } from "./travelData";

export type Scene = "where" | "ride" | "seat" | "hold" | "booked";
export type Line = { from: "you" | "worker"; text: string };

export type TravelState = {
  scene: Scene;
  destination: DestinationId | null;
  options: readonly TripOption[] | null;
  pick: string | null;
  option: string | null;
  seats: readonly Seat[] | null;
  suggestion: SeatSuggestion | null;
  seat: string | null;
  isConfirming: boolean;
  reference: string | null;
  problem: string | null;
  lines: readonly Line[];
};

export type TravelAction =
  | { type: "said"; text: string }
  | { type: "destinationPicked"; destination: DestinationId }
  | { type: "optionsRead"; options: readonly TripOption[] }
  | { type: "optionPicked"; option: string }
  | { type: "seatsRead"; seats: readonly Seat[]; suggestion: SeatSuggestion }
  | { type: "seatPicked"; seat: string }
  | { type: "ticketAsked" }
  | { type: "held" }
  | { type: "booked"; reference: string }
  | { type: "failed"; message: string }
  | { type: "heard"; text: string }
  | { type: "replied"; text: string };

const greeting = "Where is the trip? Say it, or tap a place.";

export const initialTravelState: TravelState = {
  scene: "where",
  destination: null,
  options: null,
  pick: null,
  option: null,
  seats: null,
  suggestion: null,
  seat: null,
  isConfirming: false,
  reference: null,
  problem: null,
  lines: [{ from: "worker", text: greeting }],
};

const hints: Record<Scene, string> = {
  where: "Tell me a place, like Kyoto, Seoul or Singapore.",
  ride: "Say the earlier one, the later one, or its name.",
  seat: "Tap a free seat, say one like 20C, or say book it.",
  hold: "Hold the ticket to book it, or say go back.",
  booked: "It's booked. Say go back to plan another trip.",
};

function plain(text: string) {
  return text.toLowerCase().replaceAll("’", "'").replaceAll(/\s+/g, " ").trim();
}

function say(state: TravelState, line: Line): TravelState {
  return { ...state, lines: [...state.lines, line] };
}

function earliestArrival(options: readonly TripOption[]) {
  return options.toSorted((a, b) => a.arrive.localeCompare(b.arrive))[0];
}

function rideLine(options: readonly TripOption[], pick: TripOption) {
  const kind = pick.kind === "rail" ? "trains" : "flights";
  return `${options.length} ${kind} on ${tripDay}, read just now on your computer. I'd take ${pick.name}: it gets you there first.`;
}

function seatLine(suggestion: SeatSuggestion) {
  return `I'd take ${suggestion.seat}: ${reasonText[suggestion.reason]}. Tap any free seat to change it.`;
}

function pickDestination(state: TravelState, destination: DestinationId): TravelState {
  const name = destinations.find((candidate) => candidate.id === destination)?.name ?? destination;
  return say({ ...initialTravelState, lines: state.lines, destination, scene: "ride" }, { from: "worker", text: `Looking at trips to ${name} on Hikari Travel…` });
}

function pickOption(state: TravelState, option: string): TravelState {
  if (!state.options?.some((candidate) => candidate.id === option)) return state;
  return { ...state, option, scene: "seat", seats: null, suggestion: null, seat: null };
}

function pickSeat(state: TravelState, seat: string): TravelState {
  const found = state.seats?.find((candidate) => candidate.id === seat.toUpperCase());
  return found?.available ? { ...state, seat: found.id } : state;
}

function optionFromWords(state: TravelState, text: string) {
  const options = state.options ?? [];
  if (options.length === 0) return null;
  const byTime = options.toSorted((a, b) => a.depart.localeCompare(b.depart));
  const named = options.find((option) => {
    const name = plain(option.name);
    const number = name.split(" ").at(-1) ?? name;
    return text.includes(name) || text.includes(option.id) || new RegExp(String.raw`\b${number}\b`).test(text);
  });
  if (named) return named.id;
  if (/\b(earlier|earliest|first|morning)\b/.test(text)) return byTime[0]?.id ?? null;
  if (/\b(later|latest|second|last|afternoon)\b/.test(text)) return byTime.at(-1)?.id ?? null;
  if (/\b(cheaper|cheapest|cheap)\b/.test(text)) return options.toSorted((a, b) => a.price - b.price)[0]?.id ?? null;
  if (/\b(yes|ok|okay|that one|sounds good|your pick)\b/.test(text)) return state.pick;
  return null;
}

function seatFromWords(state: TravelState, text: string) {
  const named = /\b(\d{1,2}) ?([a-f])\b/.exec(text);
  if (named) return `${named[1]}${named[2]?.toUpperCase()}`;
  const position = /\b(window|aisle|middle)\b/.exec(text)?.[1];
  if (!position) return null;
  return state.seats?.find((seat) => seat.available && seat.position === position)?.id ?? null;
}

function heard(state: TravelState, text: string): TravelState {
  const words = plain(text);
  if (/\b(go back|start over|another trip|back)\b/.test(words)) return { ...initialTravelState, lines: state.lines };
  const place = destinations.find((destination) => words.includes(destination.name.toLowerCase()));
  if (place && place.id !== state.destination) return pickDestination(state, place.id);
  if (state.scene === "ride") {
    const option = optionFromWords(state, words);
    if (option) return pickOption(state, option);
  }
  if (state.scene === "seat") {
    const seat = seatFromWords(state, words);
    if (seat) {
      if (seat === state.seat && /\bbook\b/.test(words)) return { ...state, scene: "hold" };
      const picked = pickSeat(state, seat);
      return picked === state ? say(state, { from: "worker", text: `${seat} is taken. Try another one.` }) : picked;
    }
    if (/\b(book|yes|confirm|ok|okay|looks good|that's fine|fine)\b/.test(words) && state.seat) return { ...state, scene: "hold" };
  }
  return say(state, { from: "worker", text: hints[state.scene] });
}

export function travelReducer(state: TravelState, action: TravelAction): TravelState {
  switch (action.type) {
    case "said":
      return heard(say(state, { from: "you", text: action.text.trim() }), action.text);
    case "destinationPicked":
      return pickDestination(state, action.destination);
    case "optionsRead": {
      const pick = earliestArrival(action.options);
      if (!pick) return say({ ...state, options: action.options }, { from: "worker", text: "Nothing is free that day. Try another place." });
      return say({ ...state, options: action.options, pick: pick.id }, { from: "worker", text: rideLine(action.options, pick) });
    }
    case "optionPicked":
      return pickOption(state, action.option);
    case "seatsRead":
      return say({ ...state, seats: action.seats, suggestion: action.suggestion, seat: action.suggestion.seat }, { from: "worker", text: seatLine(action.suggestion) });
    case "seatPicked":
      return pickSeat(state, action.seat);
    case "ticketAsked":
      return state.seat ? { ...state, scene: "hold" } : state;
    case "held":
      return say({ ...state, isConfirming: true }, { from: "worker", text: "Booking it now on Hikari Travel…" });
    case "booked":
      return say({ ...state, scene: "booked", isConfirming: false, reference: action.reference }, { from: "worker", text: "Booked. I opened the booking page again and it says confirmed." });
    case "failed":
      return say({ ...state, isConfirming: false, problem: action.message }, { from: "worker", text: action.message });
    case "heard":
      return say(state, { from: "you", text: action.text.trim() });
    case "replied":
      return say(state, { from: "worker", text: action.text });
  }
}
