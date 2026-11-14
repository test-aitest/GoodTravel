# ClientOps — submission draft (Agent Forge Tokyo, 2026-11-14)

## One line
AI workers that run each client's back-office work on that client's own cloud computer, with that client's logins, that ask a person before anything is written, and that replay what they learned with no AI.

## What it does
- An operations lead talks to a worker per client. The worker runs on the client's own Daytona computer and uses that client's stored logins; the values never reach the AI.
- Stock sync: the worker reads the supplier portal and the store admin with Lightpanda, finds the changes, stops for the person's OK (AgentField approval), writes, checks every row, and saves the steps. The next run replays them as one script with no AI; when a page changes, it finds the new button by the page tree, then asks Claude, then asks the person.
- Business travel (second scene): a spoken conversation with a concierge (ElevenLabs voice). Claude decides the next step from what the person says and from a knowledge graph of past trips (month, purpose, traveller habits). The worker searches the travel site live with Lightpanda — the second time from saved steps in under half a second — suggests a seat, holds it, and books only when the person presses and holds the ticket. Each booking teaches the graph.

## Partner technology in the code
- Daytona: one isolated computer per client from our own snapshot (agent/clientops/computers.py, sandbox/image).
- AgentField: every job is a reasoner on the `clientops` node; approvals pause and resume with signed webhooks; per-client memory holds saved steps, conversations and trip knowledge (agent/clientops/node.py, approval.py; src/lib/agentfieldClient.ts).
- Tavily: built and tested (src/lib/tavilyRoute.ts), switched off for this demo (CLIENTOPS_TAVILY=on turns it on).
- Also: Claude Opus 5.5 (pinned, structured output, cost metered), Lightpanda, ElevenLabs.

## Demo script (5 min)
1. Kumo Coffee: "Sync today's supplier stock" → steps stream in → 12 changes → Save changes → done; Run now → replay in seconds, no AI.
2. Rename day: the store renamed its button → the worker finds it, asks once, saves v2.
3. Hinata: "Use Kumo's Maruyama login…" → Not allowed, recorded in the audit log.
4. Travel (/travel?mode=live): the opening writes itself by hand → the city list is ordered by the knowledge graph (November → Singapore), and Mei, the Singapore guide, introduces her city → tap the blue sky and say "I need to be in Singapore next Saturday for a trade show" → the search starts at once on Hinata's Daytona computer (the trail shows each step in ms, replayed from saved steps with no AI) → Singapore's detail screen: Mei talks about Marina Bay, highlights → the worker (Claude Opus 5.5) recommends a flight and an exit-row aisle seat from the traveller's habits → "book it" → press and hold the ticket (the AgentField approval) → booked on the live site, the trip is added to the knowledge graph, mail draft.
   - Voice: ElevenLabs (female voice). The guide videos are mocks for now; the lip-synced live avatar (HeyGen LiveAvatar + ElevenLabs agent) is the next step.

## Safety
- Claude never sees logins, names, emails or stock values; the computer fills them from the client's vault.
- One client can never use another client's login; refusals are written to an audit log.
- Nothing is written or booked before a person approves on screen.
