# Travel demo (second scene) — shared spec, 2026-11-14 15:30

Story: ClientOps also books business trips for a client's staff. The ops lead talks to the worker over a full-screen overlay with no buttons; the trip moves forward with big photos and a seat map. The worker runs on Hinata Skincare's own computer (Daytona), signs in to a travel site with Hinata's stored login (never shown to Claude), reads real pages with Lightpanda, and books only after the person holds to confirm (AgentField approval).

## Mock travel site: Hikari Travel (fictional)
- Served by `bun sandbox/sites/server.ts` on port 3003, host name `travel.hikari-travel.example` (same pattern as the other demo sites), plain HTML, no scripts needed to read it.
- Fixed catalog, deterministic, global (user decision 15:40: rail and international flights mixed; English; amounts with a currency code; fictional airlines). From Tokyo on 2026-11-21, prices in JPY:
  - to Kyoto: rail `nozomi-21` Nozomi 21, 08:00 → 10:15, JPY 14,170; rail `nozomi-27` Nozomi 27, 09:00 → 11:12, JPY 14,170.
  - to Seoul: flight `ka-712` Kestrel Air KA 712, 08:30 → 11:05, JPY 38,400; flight `ns-104` Northstar NS 104, 10:15 → 12:50, JPY 35,900.
  - to Singapore: flight `ka-631` Kestrel Air KA 631, 08:05 → 14:35, JPY 72,800; flight `mr-12` Meridian MR 12, 10:40 → 17:20, JPY 68,500.
  - Kinds are `rail` and `flight` (not shinkansen). The demo path is Singapore on KA 631 with the airplane seat map.
- Pages (accessible names are the contract the worker reads):
  - `GET /login` — textbox "Username", textbox "Password", button "Sign in". Same demo credentials pattern as the other sites.
  - `GET /search?from=Tokyo&to=<Kyoto|Seoul|Singapore>&date=2026-11-21` — heading "Trips to <To>"; one list "Options"; each listitem has heading "<Name>" and text "<kind> · <HH:MM> → <HH:MM> · JPY <price>" and link "Choose seats for <Name>" → `/seats?option=<id>`.
  - `GET /seats?option=<id>` — heading "Seats on <Name>"; grid of buttons. Rail: car 7, rows 1–16, seats A B C | D E (A and E window). Flight: rows 1–30, seats A B C | D E F (A and F window, exit rows 12 and 13). Each seat button's name: "Seat <row><letter>, <window|middle|aisle>[, exit row], <available|taken>"; taken seats are disabled. Fixed set of taken seats per option.
  - `POST /hold` (form on the seat page after choosing: fields "Full name", "Email", hidden option and seat, button "Hold seat") → `GET /review?hold=<id>`: heading "Review your booking", summary text, button "Confirm booking".
  - `POST /confirm` → `GET /booked?ref=<6 letters>`: heading "Booking confirmed", text "Reference <REF>".
  - `POST /reset` — clears holds and bookings (demo reset), like the other sites.
- The passenger's full name and email live in Hinata's vault as values; the worker types placeholders that the computer fills, exactly like the store logins today. Claude never sees them.

## Worker (agent) jobs
- `trip_search(client_id, to, date)`: sign in, open the search, read the options, open each option's seat page, read the seats, ask Claude (pinned model, effort medium, json_schema) for one suggested seat per option from the seat list only (role/position/availability; no names, no prices needed) with a reason code from a fixed list (`window`, `aisle`, `quiet-front`, `exit-legroom`, `together`), fall back to a rule when Claude is off. Emits `trip.options` with options, seats and the suggestion, then ends. No writes.
- `trip_book(client_id, option, seat)`: sign in, open the seat page, choose the seat, fill name/email from the vault, Hold seat, read the review, then pause for the person (AgentField approval, signed webhook, same route as stock approvals) with a `trip.review` event; on OK press Confirm booking, read the reference, emit `trip.booked`; on Stop leave the hold unconfirmed.
- Events go through the same SSE path as today. Event shapes are owned by src/lib/events.ts (screen) and the agent contract file agent/tests/contract/trip_job_notes.json.

## Screen
- Route `/travel`. Full-screen photo background, Astryx Overlay conversation, no visible buttons except the hold-to-confirm ticket. Scenes: 1 greeting ("Where is the trip?"), 2 destination photo or video cards (Kyoto, Seoul, Singapore; tap or type), 3 ride cards (live options with photo of the shinkansen or plane), 4 seat map (tap a seat; the worker's suggestion pulses with its reason), 5 ticket card, hold to confirm (this is the approval), 6 booked ticket with reference and "Email it to me" (a mailto draft; we never send mail ourselves).
- Playback mode first (fixed events matching the contract), then live.
