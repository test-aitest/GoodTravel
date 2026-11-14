import { afterAll, beforeAll, beforeEach, describe, expect, test } from "bun:test";
import { join } from "node:path";
import { accounts } from "./catalog";
import { startSites } from "./server";
import { seatsFor, takenSeats, travelOptions } from "./travel";

let sites: Awaited<ReturnType<typeof startSites>>;

beforeAll(async () => {
  sites = await startSites({ supplierPort: 0, storePort: 0, travelPort: 0, hostname: "127.0.0.1" });
});

afterAll(async () => {
  await sites.stop();
});

beforeEach(async () => {
  expect((await fetch(`${sites.travelUrl}/reset`, { method: "POST" })).status).toBe(200);
});

function account(client: "kumo" | "hinata") {
  const found = accounts.find((candidate) => candidate.client === client && candidate.site === "travel");
  if (!found) throw new Error(`no travel account for ${client}`);
  return found;
}

async function signIn(username: string, password: string) {
  const response = await fetch(`${sites.travelUrl}/login`, {
    method: "POST",
    redirect: "manual",
    body: new URLSearchParams({ username, password }),
  });
  return { response, cookie: response.headers.get("set-cookie")?.split(";")[0] ?? "" };
}

async function signedIn(client: "kumo" | "hinata" = "hinata") {
  const { username, password } = account(client);
  return (await signIn(username, password)).cookie;
}

async function page(path: string, cookie: string) {
  const response = await fetch(`${sites.travelUrl}${path}`, { headers: { cookie }, redirect: "manual" });
  return { response, html: await response.text() };
}

async function post(path: string, cookie: string, fields: Record<string, string>) {
  return fetch(`${sites.travelUrl}${path}`, { method: "POST", redirect: "manual", headers: { cookie }, body: new URLSearchParams(fields) });
}

const search = (to: string) => `/search?from=Tokyo&to=${to}&date=2026-11-21`;
const headings = (html: string, level: number) =>
  [...html.matchAll(new RegExp(`<h${level}[^>]*>([^<]*)</h${level}>`, "g"))].map((match) => match[1]);
const seatButtons = (html: string) =>
  [...html.matchAll(/<button class="seat"[^>]*aria-label="([^"]+)"([^>]*)>/g)].map((match) => ({ name: match[1], disabled: match[2].includes(" disabled") }));
const optionItems = (html: string) =>
  [...html.matchAll(/<li class="option"[^>]*><div><h2>([^<]+)<\/h2><p>([^<]+)<\/p><\/div><a href="([^"]+)">([^<]+)<\/a><\/li>/g)].map((match) => ({
    name: match[1],
    text: match[2],
    href: match[3].replaceAll("&amp;", "&"),
    link: match[4],
  }));

function holdOf(response: Response) {
  return new URL(response.headers.get("location") ?? "", sites.travelUrl).searchParams.get("hold") ?? "";
}

async function holdSeat(cookie: string, option: string, seat: string) {
  return post("/hold", cookie, { option, seat, fullName: "Aoi Tanaka", email: "aoi@hinata-skincare.example" });
}

async function book(cookie: string, option: string, seat: string) {
  const hold = await holdSeat(cookie, option, seat);
  const review = hold.headers.get("location") ?? "";
  const holdId = holdOf(hold);
  const confirm = await post("/confirm", cookie, { hold: holdId });
  return { hold, review, holdId, confirm, booked: confirm.headers.get("location") ?? "" };
}

describe("Hikari Travel sign-in", () => {
  test("shows Username, Password and Sign in", async () => {
    const { response, html } = await page("/login", "");
    expect(response.status).toBe(200);
    expect(html).toContain("Hikari Travel");
    expect(html).toMatch(/<label class="field">Username<input name="username"/);
    expect(html).toMatch(/<label class="field">Password<input name="password" type="password"/);
    expect(html).toMatch(/<button class="primary" type="submit">Sign in<\/button>/);
  });

  test("sends a visitor without a session to the sign-in page", async () => {
    for (const path of [search("Kyoto"), "/seats?option=ka-631", "/review?hold=x", "/booked?ref=ABCDEF", "/"]) {
      const { response } = await page(path, "");
      expect(response.status).toBe(303);
      expect(response.headers.get("location")).toBe("/login");
    }
  });

  test("signs in with Hinata's travel login and lands on the search", async () => {
    const { username, password } = account("hinata");
    const { response, cookie } = await signIn(username, password);
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/search");
    expect(cookie).toMatch(/^hikari_session=/);
    const { html } = await page("/search", cookie);
    expect(headings(html, 1)).toEqual(["Find a trip"]);
    expect(html).toContain("Signed in as Hinata Skincare");
  });

  test("refuses a wrong password and the store's login", async () => {
    const wrong = await signIn(account("hinata").username, "wrong");
    expect(wrong.response.status).toBe(401);
    expect(await wrong.response.text()).toContain("Incorrect username or password.");
    const store = accounts.find((candidate) => candidate.client === "hinata" && candidate.site === "store");
    expect((await signIn(store?.username ?? "", store?.password ?? "")).response.status).toBe(401);
  });

  test("a store session does not open the travel site", async () => {
    const store = accounts.find((candidate) => candidate.client === "hinata" && candidate.site === "store");
    const login = await fetch(`${sites.storeUrl}/login`, {
      method: "POST",
      redirect: "manual",
      body: new URLSearchParams({ username: store?.username ?? "", password: store?.password ?? "" }),
    });
    const storeCookie = login.headers.get("set-cookie")?.split(";")[0] ?? "";
    expect((await page(search("Kyoto"), storeCookie)).response.status).toBe(303);
  });

  test("signs out", async () => {
    const cookie = await signedIn();
    const out = await post("/logout", cookie, {});
    expect(out.status).toBe(303);
    expect((await page("/search", cookie)).response.status).toBe(303);
  });
});

describe("search results", () => {
  test("Kyoto lists the two Nozomi trains", async () => {
    const { response, html } = await page(search("Kyoto"), await signedIn());
    expect(response.status).toBe(200);
    expect(headings(html, 1)).toEqual(["Trips to Kyoto"]);
    expect(html.match(/<ul class="options" aria-label="Options">/g)).toHaveLength(1);
    expect(optionItems(html)).toEqual([
      { name: "Nozomi 21", text: "Rail · 08:00 → 10:15 · JPY 14,170", href: "/seats?option=nozomi-21", link: "Choose seats for Nozomi 21" },
      { name: "Nozomi 27", text: "Rail · 09:00 → 11:12 · JPY 14,170", href: "/seats?option=nozomi-27", link: "Choose seats for Nozomi 27" },
    ]);
  });

  test("Seoul lists Kestrel Air KA 712 and Northstar NS 104", async () => {
    const { html } = await page(search("Seoul"), await signedIn());
    expect(headings(html, 1)).toEqual(["Trips to Seoul"]);
    expect(optionItems(html)).toEqual([
      { name: "Kestrel Air KA 712", text: "Flight · 08:30 → 11:05 · JPY 38,400", href: "/seats?option=ka-712", link: "Choose seats for Kestrel Air KA 712" },
      { name: "Northstar NS 104", text: "Flight · 10:15 → 12:50 · JPY 35,900", href: "/seats?option=ns-104", link: "Choose seats for Northstar NS 104" },
    ]);
  });

  test("Singapore lists Kestrel Air KA 631 and Meridian MR 12", async () => {
    const { html } = await page(search("Singapore"), await signedIn());
    expect(headings(html, 1)).toEqual(["Trips to Singapore"]);
    expect(optionItems(html)).toEqual([
      { name: "Kestrel Air KA 631", text: "Flight · 08:05 → 14:35 · JPY 72,800", href: "/seats?option=ka-631", link: "Choose seats for Kestrel Air KA 631" },
      { name: "Meridian MR 12", text: "Flight · 10:40 → 17:20 · JPY 68,500", href: "/seats?option=mr-12", link: "Choose seats for Meridian MR 12" },
    ]);
  });

  test("another date or place finds nothing", async () => {
    const cookie = await signedIn();
    for (const path of ["/search?from=Tokyo&to=Kyoto&date=2026-11-22", "/search?from=Tokyo&to=Osaka&date=2026-11-21"]) {
      const { response, html } = await page(path, cookie);
      expect(response.status).toBe(200);
      expect(optionItems(html)).toEqual([]);
      expect(html).toContain("No trips found for this search.");
      expect(html).not.toContain('aria-label="Options"');
    }
  });

  test("escapes what was typed", async () => {
    const { html } = await page("/search?from=Tokyo&to=%3Cscript%3E&date=2026-11-21", await signedIn());
    expect(html).not.toContain("<script>");
  });
});

describe("seat maps", () => {
  test("a rail car has rows 1 to 16 with seats A B C D E, A and E by the window", async () => {
    const { response, html } = await page("/seats?option=nozomi-21", await signedIn());
    expect(response.status).toBe(200);
    expect(headings(html, 1)).toEqual(["Seats on Nozomi 21"]);
    expect(html).toContain("Car 7");
    const buttons = seatButtons(html);
    expect(buttons).toHaveLength(80);
    expect(buttons.slice(0, 5).map((button) => button.name)).toEqual([
      "Seat 1A, window, taken",
      "Seat 1B, middle, taken",
      "Seat 1C, aisle, taken",
      "Seat 1D, aisle, taken",
      "Seat 1E, window, taken",
    ]);
    expect(buttons.find((button) => button.name.startsWith("Seat 2C,"))?.name).toBe("Seat 2C, aisle, available");
    expect(buttons.at(-1)?.name).toBe("Seat 16E, window, available");
    expect(html).not.toContain("exit row");
  });

  test("a plane has rows 1 to 30 with seats A to F, A and F by the window, exit rows 12 and 13", async () => {
    const { html } = await page("/seats?option=ka-631", await signedIn());
    expect(headings(html, 1)).toEqual(["Seats on Kestrel Air KA 631"]);
    const buttons = seatButtons(html);
    expect(buttons).toHaveLength(180);
    const row12 = buttons.filter((button) => /^Seat 12[A-F],/.test(button.name)).map((button) => button.name);
    expect(row12).toEqual([
      "Seat 12A, window, exit row, taken",
      "Seat 12B, middle, exit row, taken",
      "Seat 12C, aisle, exit row, available",
      "Seat 12D, aisle, exit row, available",
      "Seat 12E, middle, exit row, available",
      "Seat 12F, window, exit row, taken",
    ]);
    expect(buttons.filter((button) => button.name.includes("exit row")).map((button) => button.name.slice(5, 8))).toEqual([
      "12A", "12B", "12C", "12D", "12E", "12F", "13A", "13B", "13C", "13D", "13E", "13F",
    ]);
    expect(buttons.find((button) => button.name.startsWith("Seat 30F,"))?.name).toBe("Seat 30F, window, taken");
  });

  test("taken seats are fixed per trip and disabled", async () => {
    const cookie = await signedIn();
    for (const option of travelOptions) {
      const { html } = await page(`/seats?option=${option.id}`, cookie);
      const buttons = seatButtons(html);
      const taken = buttons.filter((button) => button.name.endsWith(", taken"));
      expect(taken.map((button) => button.name.match(/^Seat (\w+),/)?.[1])).toEqual(takenSeats(option.id));
      expect(taken.every((button) => button.disabled)).toBe(true);
      expect(buttons.filter((button) => button.disabled)).toHaveLength(taken.length);
    }
    expect(travelOptions.map((option) => [option.id, takenSeats(option.id).length])).toEqual([
      ["nozomi-21", 35],
      ["nozomi-27", 20],
      ["ka-712", 38],
      ["ns-104", 26],
      ["ka-631", 60],
      ["mr-12", 84],
    ]);
  });

  test("every trip leaves a window, an aisle and two seats side by side; Meridian MR 12 has one window seat left", () => {
    for (const option of travelOptions) {
      const free = seatsFor(option).filter((seat) => !seat.taken);
      expect(free.some((seat) => seat.position === "window")).toBe(true);
      expect(free.some((seat) => seat.position === "aisle")).toBe(true);
      expect(free.some((seat) => free.some((other) => other.row === seat.row && other.letter === String.fromCharCode(seat.letter.charCodeAt(0) + 1)))).toBe(true);
      if (option.kind === "flight") expect(free.some((seat) => seat.exitRow)).toBe(true);
    }
    const meridian = travelOptions.find((option) => option.id === "mr-12");
    expect(meridian && seatsFor(meridian).filter((seat) => !seat.taken && seat.position === "window").map((seat) => seat.id)).toEqual(["29F"]);
  });

  test("choosing a seat shows Full name, Email and Hold seat with the option and seat", async () => {
    const { html } = await page("/seats?option=ka-631&seat=12C", await signedIn());
    expect(html).toMatch(/aria-label="Seat 12C, aisle, exit row, available" aria-pressed="true">/);
    expect(html).toContain('<input type="hidden" name="option" value="ka-631">');
    expect(html).toContain('<input type="hidden" name="seat" value="12C">');
    expect(html).toMatch(/<label class="field">Full name<input name="fullName"/);
    expect(html).toMatch(/<label class="field">Email<input name="email" type="email"/);
    expect(html).toMatch(/<button class="primary" type="submit">Hold seat<\/button>/);
  });

  test("before a seat is chosen, or when a taken seat is chosen, there is no hold form", async () => {
    const cookie = await signedIn();
    const none = await page("/seats?option=ka-631", cookie);
    expect(none.html).not.toContain("Hold seat");
    expect(none.html).toContain("Choose a seat on the map.");
    const taken = await page("/seats?option=ka-631&seat=1A", cookie);
    expect(taken.html).not.toContain("Hold seat");
    expect(taken.html).toContain("Seat 1A is taken. Choose another seat.");
  });

  test("an unknown trip is not found", async () => {
    expect((await page("/seats?option=xx-1", await signedIn())).response.status).toBe(404);
  });
});

describe("hold, review, confirm, booked", () => {
  test("Hold seat leads to the review, Confirm booking to a 6-letter reference", async () => {
    const cookie = await signedIn();
    const { hold, review, holdId, confirm, booked } = await book(cookie, "ka-631", "12C");
    expect(hold.status).toBe(303);
    expect(review).toMatch(/^\/review\?hold=[a-z0-9]+$/);
    expect(holdId.length).toBeGreaterThan(0);

    expect(confirm.status).toBe(303);
    expect(booked).toMatch(/^\/booked\?ref=[A-Z]{6}$/);
    const reference = booked.slice("/booked?ref=".length);
    const done = await page(booked, cookie);
    expect(done.response.status).toBe(200);
    expect(headings(done.html, 1)).toEqual(["Booking confirmed"]);
    expect(done.html).toContain(`<p class="reference">Reference ${reference}</p>`);
    expect(done.html).toContain("Seat 12C, aisle, exit row");
  });

  test("the review shows the trip, the seat and the price, and one Confirm booking button", async () => {
    const cookie = await signedIn();
    const hold = await holdSeat(cookie, "nozomi-21", "2C");
    const { response, html } = await page(hold.headers.get("location") ?? "", cookie);
    expect(response.status).toBe(200);
    expect(headings(html, 1)).toEqual(["Review your booking"]);
    expect(html).toContain('<section class="card ticket" aria-label="Booking summary">');
    for (const line of ["Tokyo → Kyoto", "Nozomi 21 · Rail · Sat, 21 Nov 2026", "08:00 → 10:15", "Seat 2C, aisle", "1 adult", "Total JPY 14,170"]) {
      expect(html).toContain(`>${line}</p>`);
    }
    expect(html.match(/<button class="primary" type="submit">Confirm booking<\/button>/g)).toHaveLength(1);
  });

  test("the passenger's name and email are not echoed back on the review or booked page", async () => {
    const cookie = await signedIn();
    const { review, booked } = await book(cookie, "ka-712", "2C");
    for (const path of [review, booked]) {
      const { html } = await page(path, cookie);
      expect(html).not.toContain("Aoi Tanaka");
      expect(html).not.toContain("aoi@hinata-skincare.example");
    }
  });

  test("a booked seat shows as taken and cannot be held again", async () => {
    const cookie = await signedIn();
    await book(cookie, "ka-631", "12C");
    const { html } = await page("/seats?option=ka-631", cookie);
    expect(html).toContain('aria-label="Seat 12C, aisle, exit row, taken" disabled');
    const again = await holdSeat(cookie, "ka-631", "12C");
    expect(again.status).toBe(409);
    expect(await again.text()).toContain("Seat 12C is taken. Choose another seat.");
  });

  test("a hold that is never confirmed leaves the seat free", async () => {
    const cookie = await signedIn();
    expect((await holdSeat(cookie, "ka-631", "12D")).status).toBe(303);
    const { html } = await page("/seats?option=ka-631", cookie);
    expect(html).toContain('aria-label="Seat 12D, aisle, exit row, available"');
  });

  test("confirming twice keeps the same reference; a second hold on the same seat is refused at confirm", async () => {
    const cookie = await signedIn();
    const first = await holdSeat(cookie, "mr-12", "29F");
    const second = await holdSeat(cookie, "mr-12", "29F");
    const confirmed = await post("/confirm", cookie, { hold: holdOf(first) });
    const repeat = await post("/confirm", cookie, { hold: holdOf(first) });
    expect(repeat.headers.get("location")).toBe(confirmed.headers.get("location"));
    const late = await post("/confirm", cookie, { hold: holdOf(second) });
    expect(late.status).toBe(409);
    expect(await late.text()).toContain("Seat 29F was just booked by someone else. Choose another seat.");
  });

  test("refuses a hold without a name, with a bad email, or for a taken seat", async () => {
    const cookie = await signedIn();
    const noName = await post("/hold", cookie, { option: "ka-631", seat: "12C", fullName: " ", email: "a@b.example" });
    expect(noName.status).toBe(400);
    expect(await noName.text()).toContain("Enter the passenger&#39;s full name.");
    const badEmail = await post("/hold", cookie, { option: "ka-631", seat: "12C", fullName: "Aoi Tanaka", email: "nope" });
    expect(badEmail.status).toBe(400);
    expect(await badEmail.text()).toContain("Enter a valid email address.");
    expect((await holdSeat(cookie, "ka-631", "1A")).status).toBe(409);
    expect((await holdSeat(cookie, "ka-631", "31A")).status).toBe(400);
  });

  test("another client cannot open the hold or the booking", async () => {
    const hinata = await signedIn("hinata");
    const kumo = await signedIn("kumo");
    const { review, booked } = await book(hinata, "ns-104", "1A");
    expect((await page(review, kumo)).response.status).toBe(404);
    expect((await page(booked, kumo)).response.status).toBe(404);
  });

  test("the review of a confirmed hold goes to the booked page", async () => {
    const cookie = await signedIn();
    const { review, booked } = await book(cookie, "nozomi-27", "1B");
    const { response } = await page(review, cookie);
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe(booked);
  });
});

describe("reset", () => {
  test("POST /reset clears holds and bookings", async () => {
    const cookie = await signedIn();
    const { review, booked } = await book(cookie, "ka-631", "12C");
    const pending = await holdSeat(cookie, "ka-631", "12D");

    const reset = await fetch(`${sites.travelUrl}/reset`, { method: "POST" });
    expect(reset.status).toBe(200);
    expect(await reset.json()).toEqual({ reset: true });

    expect((await page(review, cookie)).response.status).toBe(404);
    expect((await page(booked, cookie)).response.status).toBe(404);
    expect((await page(pending.headers.get("location") ?? "", cookie)).response.status).toBe(404);
    const { html } = await page("/seats?option=ka-631", cookie);
    expect(html).toContain('aria-label="Seat 12C, aisle, exit row, available"');
  });

  test("the stock sites' reset leaves travel bookings alone, and travel's reset leaves the stock alone", async () => {
    const cookie = await signedIn();
    const { booked } = await book(cookie, "ka-631", "12C");
    expect((await fetch(`${sites.storeUrl}/__control/reset`, { method: "POST" })).status).toBe(200);
    expect((await page(booked, cookie)).response.status).toBe(200);

    await fetch(`${sites.storeUrl}/__control/day`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ day: 2 }) });
    await fetch(`${sites.travelUrl}/reset`, { method: "POST" });
    expect((await (await fetch(`${sites.storeUrl}/__control`)).json()).day).toBe(2);
    await fetch(`${sites.storeUrl}/__control/reset`, { method: "POST" });
  });
});

describe("travel site hygiene", () => {
  test("no page refers to another host", async () => {
    const cookie = await signedIn();
    const { review, booked } = await book(cookie, "ka-631", "12C");
    const paths = ["/login", "/search", search("Singapore"), "/seats?option=ka-631&seat=12D", "/seats?option=nozomi-21", review, booked];
    for (const path of paths) {
      const { html } = await page(path, path === "/login" ? "" : cookie);
      expect(html).not.toMatch(/(https?:)?\/\/[a-z]/i);
      expect(html).not.toMatch(/<script/i);
    }
  });

  test("answers the browser's icon request without an error", async () => {
    expect((await fetch(`${sites.travelUrl}/favicon.ico`)).status).toBe(204);
  });

  test("starts with the other sites from the same command and answers within 1 second", async () => {
    const started = performance.now();
    const server = Bun.spawn(["bun", join(import.meta.dir, "server.ts")], {
      env: { ...process.env, SUPPLIER_PORT: "0", STORE_PORT: "0", TRAVEL_PORT: "0" },
      stdout: "pipe",
      stderr: "inherit",
    });
    try {
      const reader = server.stdout.getReader();
      let output = "";
      while (!output.includes("\n")) {
        const { value, done } = await reader.read();
        if (done) break;
        output += new TextDecoder().decode(value);
      }
      const travel = output.match(/ · travel (127\.0\.0\.1:\d+)/)?.[1];
      expect(travel).toBeDefined();
      expect((await fetch(`http://${travel}/login`)).status).toBe(200);
      expect(performance.now() - started).toBeLessThan(1000);
    } finally {
      server.kill();
      await server.exited;
    }
  });
});
