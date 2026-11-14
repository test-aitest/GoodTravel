import { catalogs, type ClientId, type SiteId } from "./catalog";
import {
  notFoundPage,
  storeInventoryPage,
  storeFieldName,
  storeLoginPage,
  supplierLoginPage,
  supplierStockPage,
  type StoreMessage,
} from "./pages";
import { SitesState } from "./state";
import { destinations, findOption, optionsFor, travelDate, travelOrigin } from "./travel";
import { travelBookedPage, travelLoginPage, travelResultsPage, travelReviewPage, travelSearchPage, travelSeatsPage } from "./travelPages";

export type SitesOptions = { supplierPort: number; storePort: number; travelPort?: number; hostname: string; clients?: ClientId[] };

const cookieNames: Record<SiteId, string> = { supplier: "mss_session", store: "admin_session", travel: "hikari_session" };
const homePaths: Record<SiteId, string> = { supplier: "/stock", store: "/inventory", travel: "/search" };
const signInError: Record<SiteId, string> = {
  supplier: "The account ID or password is incorrect.",
  store: "Incorrect username or password.",
  travel: "Incorrect username or password.",
};

const html = (body: string, status = 200, headers: Record<string, string> = {}) =>
  new Response(body, { status, headers: { "content-type": "text/html; charset=utf-8", ...headers } });

const redirect = (location: string, headers: Record<string, string> = {}) =>
  new Response(null, { status: 303, headers: { location, ...headers } });

function sessionToken(request: Request, site: SiteId) {
  const cookies = request.headers.get("cookie") ?? "";
  const pair = cookies.split(";").map((part) => part.trim()).find((part) => part.startsWith(`${cookieNames[site]}=`));
  return pair?.slice(cookieNames[site].length + 1);
}

async function controlResponse(request: Request, path: string, state: SitesState) {
  if (path === "/__control" && request.method === "GET") {
    return Response.json({ switches: state.switches, day: state.day, clients: state.clients });
  }
  if (path === "/__control/reset" && request.method === "POST") {
    state.reset();
    return Response.json({ reset: true, day: state.day, switches: state.switches });
  }
  if (path === "/__control/day" && request.method === "POST") {
    const body = await request.json().catch(() => undefined);
    const day = state.setDay(typeof body === "object" && body !== null ? (body as { day?: unknown }).day : undefined);
    return day ? Response.json({ day }) : Response.json({ error: "Day must be 1, 2 or 3" }, { status: 400 });
  }
  if (path === "/__control/switches" && request.method === "POST") {
    const body = await request.json().catch(() => undefined);
    const switches = state.setSwitches(body);
    return switches ? Response.json({ switches }) : Response.json({ error: "Unknown switch or value" }, { status: 400 });
  }
  return undefined;
}

function siteHandler(site: SiteId, state: SitesState) {
  const loginPage = (error?: string) =>
    site === "supplier" ? supplierLoginPage(state.switches, error) : storeLoginPage(state.switches, error);
  const field = (form: FormData, name: string) => form.get(name) ?? (site === "store" ? form.get(storeFieldName(true, name)) : null);

  return async (request: Request) => {
    const url = new URL(request.url);
    const path = url.pathname;

    const control = await controlResponse(request, path, state);
    if (control) return control;
    if (path === "/favicon.ico") return new Response(null, { status: 204 });

    if (path === "/login" && request.method === "GET") return html(loginPage());
    if (path === "/login" && request.method === "POST") {
      const form = await request.formData();
      const token = state.signIn(site, String(field(form, "username") ?? ""), String(field(form, "password") ?? ""));
      if (!token) return html(loginPage(signInError[site]), 401);
      return redirect(homePaths[site], {
        "set-cookie": `${cookieNames[site]}=${token}; Path=/; HttpOnly; SameSite=Lax`,
      });
    }

    const token = sessionToken(request, site);
    if (path === "/logout" && request.method === "POST") {
      state.signOut(token);
      return redirect("/login", { "set-cookie": `${cookieNames[site]}=; Path=/; Max-Age=0` });
    }

    const client = state.clientFor(site, token);
    if (!client) return redirect("/login");
    const catalog = catalogs[client];

    if (path === "/") return redirect(homePaths[site]);

    if (site === "supplier" && path === "/stock" && request.method === "GET") {
      const requestedPage = Number.parseInt(url.searchParams.get("page") ?? "1", 10) || 1;
      return html(supplierStockPage(state.switches, catalog, state.supplierStock(client), requestedPage));
    }

    if (site === "store" && path === "/inventory" && request.method === "GET") {
      const saved = url.searchParams.get("saved");
      const message: StoreMessage | undefined =
        saved !== null && /^\d+$/.test(saved)
          ? { kind: "saved", changed: Number(saved), allLocations: url.searchParams.get("scope") === "all-locations" }
          : undefined;
      return html(storeInventoryPage(state.switches, catalog, state.storeStock(client), message));
    }

    if (site === "store" && path === "/inventory" && request.method === "POST") {
      const form = await request.formData();
      const submitted = new Map<string, string>();
      for (const [name, value] of form.entries()) {
        if (name.startsWith("available-")) submitted.set(name.slice("available-".length), String(value));
      }
      const scope = field(form, "action") === "all-locations" ? "all-locations" : "this-location";
      const result = state.saveStock(client, submitted, scope);
      if (!result.ok) {
        return html(storeInventoryPage(state.switches, catalog, state.storeStock(client), { kind: "error", text: result.error }), 400);
      }
      return redirect(`/inventory?saved=${result.changed}${scope === "all-locations" ? "&scope=all-locations" : ""}`);
    }

    return html(notFoundPage(), 404);
  };
}

const text = (form: FormData, name: string) => String(form.get(name) ?? "");

function travelHandler(state: SitesState) {
  const bookings = state.travel;
  return async (request: Request) => {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path === "/favicon.ico") return new Response(null, { status: 204 });
    if (path === "/reset" && request.method === "POST") {
      bookings.reset();
      return Response.json({ reset: true });
    }

    if (path === "/login" && request.method === "GET") return html(travelLoginPage());
    if (path === "/login" && request.method === "POST") {
      const form = await request.formData();
      const token = state.signIn("travel", text(form, "username"), text(form, "password"));
      if (!token) return html(travelLoginPage(signInError.travel), 401);
      return redirect(homePaths.travel, { "set-cookie": `${cookieNames.travel}=${token}; Path=/; HttpOnly; SameSite=Lax` });
    }

    const token = sessionToken(request, "travel");
    if (path === "/logout" && request.method === "POST") {
      state.signOut(token);
      return redirect("/login", { "set-cookie": `${cookieNames.travel}=; Path=/; Max-Age=0` });
    }

    const client = state.clientFor("travel", token);
    if (!client) return redirect("/login");
    const clientName = catalogs[client].clientName;

    if (path === "/") return redirect(homePaths.travel);

    if (path === "/search" && request.method === "GET") {
      const to = url.searchParams.get("to");
      if (!to) return html(travelSearchPage(clientName));
      const from = url.searchParams.get("from") ?? travelOrigin;
      const date = url.searchParams.get("date") ?? travelDate;
      const known = destinations.find((destination) => destination === to);
      return html(travelResultsPage(clientName, { from, to, date }, known ? optionsFor(from, known, date) : []));
    }

    if (path === "/seats" && request.method === "GET") {
      const option = findOption(url.searchParams.get("option"));
      if (!option) return html(notFoundPage(), 404);
      return html(travelSeatsPage(clientName, option, bookings.seats(option), url.searchParams.get("seat") ?? undefined));
    }

    if (path === "/hold" && request.method === "POST") {
      const form = await request.formData();
      const result = bookings.hold(client, text(form, "option"), text(form, "seat"), text(form, "fullName"), text(form, "email"));
      if (result.ok) return redirect(`/review?hold=${result.hold.id}`);
      const option = findOption(text(form, "option"));
      if (!option) return html(notFoundPage(), result.status);
      return html(travelSeatsPage(clientName, option, bookings.seats(option), text(form, "seat"), result.error), result.status);
    }

    if (path === "/review" && request.method === "GET") {
      const hold = bookings.holdFor(client, url.searchParams.get("hold"));
      if (!hold) return html(notFoundPage(), 404);
      if (hold.reference) return redirect(`/booked?ref=${hold.reference}`);
      const option = findOption(hold.optionId);
      const seat = option && bookings.seats(option).find((candidate) => candidate.id === hold.seat);
      if (!option || !seat) return html(notFoundPage(), 404);
      return html(travelReviewPage(clientName, option, hold, seat));
    }

    if (path === "/confirm" && request.method === "POST") {
      const form = await request.formData();
      const holdId = text(form, "hold");
      const result = bookings.confirm(client, holdId);
      if (result.ok) return redirect(`/booked?ref=${result.reference}`);
      const hold = bookings.holdFor(client, holdId);
      const option = hold && findOption(hold.optionId);
      const seat = option && bookings.seats(option).find((candidate) => candidate.id === hold.seat);
      if (!hold || !option || !seat) return html(notFoundPage(), result.status);
      return html(travelReviewPage(clientName, option, hold, seat, result.error), result.status);
    }

    if (path === "/booked" && request.method === "GET") {
      const booking = bookings.bookingFor(client, url.searchParams.get("ref"));
      const option = booking && findOption(booking.optionId);
      const seat = option && bookings.seats(option).find((candidate) => candidate.id === booking.seat);
      if (!booking?.reference || !option || !seat) return html(notFoundPage(), 404);
      return html(travelBookedPage(clientName, option, booking.reference, seat));
    }

    return html(notFoundPage(), 404);
  };
}

export function startSites(options: SitesOptions) {
  const state = new SitesState(options.clients ?? ["kumo", "hinata"]);
  const supplier = Bun.serve({ port: options.supplierPort, hostname: options.hostname, fetch: siteHandler("supplier", state) });
  const store = Bun.serve({ port: options.storePort, hostname: options.hostname, fetch: siteHandler("store", state) });
  const travel = Bun.serve({ port: options.travelPort ?? 0, hostname: options.hostname, fetch: travelHandler(state) });
  return {
    state,
    supplierUrl: `http://${options.hostname}:${supplier.port}`,
    storeUrl: `http://${options.hostname}:${store.port}`,
    travelUrl: `http://${options.hostname}:${travel.port}`,
    travelAddress: `${options.hostname}:${travel.port}`,
    async stop() {
      await Promise.all([supplier.stop(true), store.stop(true), travel.stop(true)]);
    },
  };
}

function clientsFrom(value: string | undefined): ClientId[] | undefined {
  if (!value) return undefined;
  const names = value.split(",").map((name) => name.trim());
  const known = Object.keys(catalogs);
  const unknown = names.filter((name) => !known.includes(name));
  if (unknown.length > 0) throw new Error(`Unknown client in SITES_CLIENTS: ${unknown.join(", ")}`);
  return names as ClientId[];
}

if (import.meta.main) {
  const started = performance.now();
  const storePort = Number(process.env.STORE_PORT ?? 3002);
  const sites = startSites({
    supplierPort: Number(process.env.SUPPLIER_PORT ?? 3001),
    storePort,
    travelPort: Number(process.env.TRAVEL_PORT ?? (storePort === 0 ? 0 : storePort + 1)),
    hostname: process.env.SITES_HOST ?? "127.0.0.1",
    clients: clientsFrom(process.env.SITES_CLIENTS),
  });
  console.log(
    `Sites ready in ${Math.round(performance.now() - started)} ms · supplier ${sites.supplierUrl} · store ${sites.storeUrl} · travel ${sites.travelAddress}`,
  );
}
