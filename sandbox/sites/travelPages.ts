import { documentOf, escapeHtml } from "./pages";
import {
  destinations,
  kindLabels,
  optionLine,
  seatLayouts,
  seatName,
  railCar,
  travelDate,
  travelOrigin,
  priceText,
  type Hold,
  type Seat,
  type TravelOption,
} from "./travel";

const css = `
:root{--ink:#14213d;--muted:#5b6475;--line:#dfe5ee;--brand:#0b63c5;--brand-soft:#e8f1fc;--sun:#f5a524;--taken:#c9ced8;--bg:#f4f7fb}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Arial,sans-serif;font-size:15px;line-height:1.45;color:var(--ink);background:var(--bg)}
.top{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:12px 24px;background:#fff;border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:8px;font-weight:700;font-size:18px;color:var(--ink);text-decoration:none}
.brand-mark{width:18px;height:18px;border-radius:50%;background:radial-gradient(circle at 35% 35%,#ffd36b,var(--sun))}
.tag{color:var(--muted);font-size:13px}
.account{margin-left:auto;color:var(--muted);font-size:13px}
.signout{font:inherit;font-size:13px;color:var(--brand);background:transparent;border:1px solid var(--line);border-radius:999px;padding:4px 12px;cursor:pointer}
main{max-width:960px;margin:24px auto;padding:0 16px;display:flex;flex-direction:column;gap:16px}
.narrow{max-width:520px}
h1{margin:0;font-size:26px;letter-spacing:-.01em}
h2{margin:0;font-size:18px}
.lede{margin:0;color:var(--muted)}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px}
.stack{display:flex;flex-direction:column;gap:12px}
.field{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted)}
.field input,.field select{font:inherit;font-size:15px;color:var(--ink);padding:9px 10px;border:1px solid #c8d0dc;border-radius:8px;background:#fff}
.search{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;align-items:end}
.primary{font:inherit;font-weight:600;padding:10px 20px;border:0;border-radius:999px;background:var(--brand);color:#fff;cursor:pointer}
.error{margin:0;padding:10px 12px;border-radius:8px;background:#fdecea;border:1px solid #f1b5ae;color:#8a1c14}
.options{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:12px}
.option{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 20px}
.option p{margin:2px 0 0;color:var(--muted)}
.option a{margin-left:auto;font-weight:600;color:var(--brand);text-decoration:none;border:1px solid var(--brand);border-radius:999px;padding:8px 16px}
.back{color:var(--brand)}
.seat-layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:16px;align-items:start}
.seatmap{display:flex;flex-direction:column;gap:6px;align-items:center;overflow-x:auto}
.seat-row{display:grid;gap:6px;align-items:center}
.flight .seat-row{grid-template-columns:28px repeat(3,36px) 18px repeat(3,36px) 28px}
.rail .seat-row{grid-template-columns:28px repeat(3,36px) 18px repeat(2,36px) 28px}
.row-number,.column-letter{text-align:center;font-size:12px;color:var(--muted)}
.exit-mark{font-size:10px;font-weight:700;color:#b54708;text-align:center}
.seat{width:36px;height:36px;font:inherit;font-size:12px;border-radius:8px 8px 5px 5px;border:1px solid #9fb9dc;background:var(--brand-soft);color:var(--brand);cursor:pointer}
.seat:disabled{background:var(--taken);border-color:var(--taken);color:transparent;cursor:not-allowed}
.seat[aria-pressed="true"]{background:var(--brand);border-color:var(--brand);color:#fff}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin:0 0 4px}
.swatch{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:4px;vertical-align:-1px}
.panel{position:sticky;top:16px}
.ticket{display:flex;flex-direction:column;gap:6px;border-left:4px solid var(--sun)}
.ticket p{margin:0}
.route{font-size:20px;font-weight:700}
.total{font-weight:700}
.reference{margin:0;font-size:22px;font-weight:700;letter-spacing:.06em}
.hint{margin:0;color:var(--muted);font-size:13px}
.login{max-width:380px;margin:64px auto}
@media (max-width:720px){.seat-layout{grid-template-columns:1fr}.panel{position:static}.top{padding:12px 16px}}
`;

const fullDate = (date: string) =>
  new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(
    new Date(`${date}T00:00:00Z`),
  );

function header(clientName?: string) {
  const account = clientName
    ? `<span class="account">Signed in as ${escapeHtml(clientName)}</span><form method="post" action="/logout"><button class="signout">Sign out</button></form>`
    : "";
  return `<header class="top"><a class="brand" href="/search"><span class="brand-mark" aria-hidden="true"></span>Hikari Travel</a><span class="tag">Business trips</span>${account}</header>`;
}

const page = (title: string, clientName: string | undefined, body: string) =>
  documentOf(`${title} - Hikari Travel`, css, `${header(clientName)}\n${body}`);

const alertOf = (error?: string) => (error ? `<p class="error" role="alert">${escapeHtml(error)}</p>` : "");

export function travelLoginPage(error?: string) {
  return page(
    "Sign in",
    undefined,
    `<main class="login">
<form class="card stack" method="post" action="/login">
<h1>Sign in</h1>
<p class="lede">Business travel for your team.</p>
${alertOf(error)}
<label class="field">Username<input name="username" autocomplete="username" required></label>
<label class="field">Password<input name="password" type="password" autocomplete="current-password" required></label>
<button class="primary" type="submit">Sign in</button>
</form>
</main>`,
  );
}

export function travelSearchPage(clientName: string) {
  const choices = destinations.map((destination) => `<option>${destination}</option>`).join("");
  return page(
    "Find a trip",
    clientName,
    `<main>
<h1>Find a trip</h1>
<form class="card search" method="get" action="/search">
<label class="field">From<select name="from"><option>${travelOrigin}</option></select></label>
<label class="field">To<select name="to">${choices}</select></label>
<label class="field">Date<input type="date" name="date" value="${travelDate}"></label>
<button class="primary" type="submit">Search</button>
</form>
</main>`,
  );
}

export function travelResultsPage(clientName: string, search: { from: string; to: string; date: string }, options: TravelOption[]) {
  const items = options
    .map(
      (option) =>
        `<li class="option" data-option="${escapeHtml(option.id)}"><div><h2>${escapeHtml(option.name)}</h2><p>${escapeHtml(optionLine(option))}</p></div><a href="/seats?option=${escapeHtml(option.id)}">Choose seats for ${escapeHtml(option.name)}</a></li>`,
    )
    .join("\n");
  const list = options.length > 0 ? `<ul class="options" aria-label="Options">\n${items}\n</ul>` : `<p class="card">No trips found for this search.</p>`;
  const date = /^\d{4}-\d{2}-\d{2}$/.test(search.date) ? fullDate(search.date) : search.date;
  return page(
    `Trips to ${search.to}`,
    clientName,
    `<main>
<h1>Trips to ${escapeHtml(search.to)}</h1>
<p class="lede">${escapeHtml(search.from)} → ${escapeHtml(search.to)} · ${escapeHtml(date)} · 1 adult</p>
${list}
<a class="back" href="/search">Change search</a>
</main>`,
  );
}

function seatButton(seat: Seat, chosen?: string) {
  const pressed = !seat.taken && seat.id === chosen ? ' aria-pressed="true"' : "";
  const disabled = seat.taken ? " disabled" : "";
  return `<button class="seat" type="submit" name="seat" value="${seat.id}" aria-label="${escapeHtml(seatName(seat))}"${pressed}${disabled}>${seat.letter}</button>`;
}

const withAisle = (cells: string[], aisle: string) => [...cells.slice(0, 3), aisle, ...cells.slice(3)];

function seatMap(option: TravelOption, seats: Seat[], chosen?: string) {
  const layout = seatLayouts[option.kind];
  const letters = withAisle(
    layout.letters.map((letter) => `<span class="column-letter">${letter}</span>`),
    "<span></span>",
  ).join("");
  const rows = Array.from({ length: layout.rows }, (_, index) => index + 1)
    .map((row) => {
      const inRow = seats.filter((seat) => seat.row === row);
      const exit = inRow.some((seat) => seat.exitRow) ? '<span class="exit-mark" aria-hidden="true">EXIT</span>' : "<span></span>";
      const buttons = withAisle(
        inRow.map((seat) => seatButton(seat, chosen)),
        "<span></span>",
      ).join("");
      return `<div class="seat-row"><span class="row-number" aria-hidden="true">${row}</span>${buttons}${exit}</div>`;
    })
    .join("\n");
  return `<form class="card seatmap ${option.kind}" method="get" action="/seats" aria-label="Seat map">
<input type="hidden" name="option" value="${escapeHtml(option.id)}">
<div class="seat-row" aria-hidden="true"><span></span>${letters}<span></span></div>
${rows}
</form>`;
}

function seatSummary(seat: Seat) {
  return `Seat ${seat.id}, ${seat.position}${seat.exitRow ? ", exit row" : ""}`;
}

function holdPanel(option: TravelOption, chosen?: Seat, error?: string) {
  if (!chosen || chosen.taken) {
    return `<section class="card stack panel" aria-label="Your seat">
<h2>Your seat</h2>
${alertOf(error)}
<p class="hint">Choose a seat on the map.</p>
</section>`;
  }
  return `<section class="card stack panel" aria-label="Passenger details">
<h2>Passenger details</h2>
<p>${escapeHtml(seatSummary(chosen))} · ${priceText(option.price)}</p>
${alertOf(error)}
<form class="stack" method="post" action="/hold">
<input type="hidden" name="option" value="${escapeHtml(option.id)}">
<input type="hidden" name="seat" value="${chosen.id}">
<label class="field">Full name<input name="fullName" autocomplete="name" required></label>
<label class="field">Email<input name="email" type="email" autocomplete="email" required></label>
<button class="primary" type="submit">Hold seat</button>
<p class="hint">Nothing is charged until you confirm.</p>
</form>
</section>`;
}

export function travelSeatsPage(clientName: string, option: TravelOption, seats: Seat[], chosenId?: string, error?: string) {
  const chosen = seats.find((seat) => seat.id === chosenId);
  const problem = error ?? (chosenId && !chosen ? "Choose a seat on the map." : chosen?.taken ? `Seat ${chosen.id} is taken. Choose another seat.` : undefined);
  const place = option.kind === "rail" ? `Car ${railCar} · Reserved` : "Economy";
  return page(
    `Seats on ${option.name}`,
    clientName,
    `<main>
<h1>Seats on ${escapeHtml(option.name)}</h1>
<p class="lede">${escapeHtml(option.from)} → ${escapeHtml(option.to)} · ${escapeHtml(fullDate(option.date))} · ${escapeHtml(optionLine(option))} · ${place}</p>
<p class="legend" aria-hidden="true"><span><span class="swatch" style="background:#e8f1fc;border:1px solid #9fb9dc"></span>Available</span><span><span class="swatch" style="background:#c9ced8"></span>Taken</span><span><span class="swatch" style="background:#0b63c5"></span>Your seat</span>${option.kind === "flight" ? "<span>EXIT · extra legroom</span>" : ""}</p>
<div class="seat-layout">
${seatMap(option, seats, chosen?.id)}
${holdPanel(option, chosen, problem)}
</div>
<a class="back" href="/search?from=${encodeURIComponent(option.from)}&amp;to=${encodeURIComponent(option.to)}&amp;date=${option.date}">Back to trips</a>
</main>`,
  );
}

function summary(option: TravelOption, seat: Seat) {
  return `<section class="card ticket" aria-label="Booking summary">
<p class="route">${escapeHtml(option.from)} → ${escapeHtml(option.to)}</p>
<p>${escapeHtml(option.name)} · ${kindLabels[option.kind]} · ${escapeHtml(fullDate(option.date))}</p>
<p>${option.departs} → ${option.arrives}</p>
<p>${escapeHtml(seatSummary(seat))}</p>
<p>1 adult</p>
<p class="total">Total ${priceText(option.price)}</p>
</section>`;
}

export function travelReviewPage(clientName: string, option: TravelOption, hold: Hold, seat: Seat, error?: string) {
  return page(
    "Review your booking",
    clientName,
    `<main class="narrow">
<h1>Review your booking</h1>
${alertOf(error)}
${summary(option, seat)}
<form method="post" action="/confirm">
<input type="hidden" name="hold" value="${escapeHtml(hold.id)}">
<button class="primary" type="submit">Confirm booking</button>
</form>
<p class="hint">Your seat is held. Nothing is charged until you confirm.</p>
<a class="back" href="/seats?option=${escapeHtml(option.id)}">Change seat</a>
</main>`,
  );
}

export function travelBookedPage(clientName: string, option: TravelOption, reference: string, seat: Seat) {
  return page(
    "Booking confirmed",
    clientName,
    `<main class="narrow">
<h1>Booking confirmed</h1>
<p class="reference">Reference ${escapeHtml(reference)}</p>
${summary(option, seat)}
<p class="hint">Keep this reference for check-in.</p>
<a class="back" href="/search">Book another trip</a>
</main>`,
  );
}
