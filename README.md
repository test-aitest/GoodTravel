# ClientOps

AI workers that run each client's operations on that client's own cloud computer, ask before every write, and replay learned steps without AI.

## How it works

A person asks for a job in the chat. The worker does it on that client's own computer, stops before any write and asks, then saves the steps so the next run needs no AI. Only the one step that broke climbs a ladder: saved script → find again by role and name → Claude → you.

| Part | What it does here | Code |
|---|---|---|
| AgentField | Runs the `clientops` agent node: the `run_job` and `undo_sync` reasoners, the `replay_script` and `refind_by_tree` skills, `app.pause()` for every approval (signed webhook), client-scoped memory for saved steps and undo copies, and notes that become the screen's events | `agent/clientops/node.py`, `approval.py`, `records.py`; `src/lib/agentfieldClient.ts`, `agentfieldRecords.ts`, `jobStream.ts` |
| Daytona | One computer per client, made from a pinned snapshot, holding that client's logins in a vault the AI never reads, with a network allow list of that client's sites only | `agent/clientops/computers.py`, `boundary.py`, `sandbox/image/image.json` |
| Lightpanda | The browser inside each computer: reads pages as a semantic tree for the first run, and replays saved steps as a PandaScript with no AI | `sandbox/runner/`, `agent/clientops/page.py`, `compile_script.py`, `pandascript.js`, `replay.py`, `refind.py` |
| Claude Opus 5.5 | Picks elements on the first run and repairs one broken step; it sees only element roles and names, never values, logins or other clients | `agent/clientops/plan.py`, `repair.py`, `claude_call.py` |
| Screen | Chat console, approvals, Activity with the page the worker saw, What changed, daily runs | `src/console/`, `src/components/`, `src/lib/events.ts` |
| MCP | Ask the same worker from Claude or other MCP clients; approvals stay on the screen | `src/mcp/`, `src/app/api/mcp/` |

## Commands

| Command | What it does |
|---|---|
| `bun run dev` | Start the app on http://localhost:3000 |
| `bun run build` | Production build (includes the Next.js type check) |
| `bun run typecheck` | TypeScript 7 type check |
| `bun run lint` | Lint |
| `bun run test` | Unit tests (`*.test.ts`) and the agent's Python tests |
| `bun run test:ui` | Playwright UI tests (`e2e/*.pw.ts`) against a production build on port 3100, in Google Chrome |
| `bun run stage NNN` | Run every check, then mark issue NNN staged, save `snapshots/NNN-YYYYMMDD-HHMMSS.tar.gz` and add a line to `issues/LOG.md` |
| `bun run restore NNN [file]` | Unpack the newest (or named) snapshot of issue NNN into `snapshots/restored/`, leaving current files alone |
| `bun run agentfield` | Start the AgentField control plane (v0.1.145, pinned) in Docker on port 8080 of this computer only (127.0.0.1, not the network) with telemetry off. Reads `.env.local` to pass `AGENTFIELD_APPROVAL_WEBHOOK_SECRET` through. Jobs, waiting approvals and client memory (undo copies) are kept in the Docker volume `clientops-agentfield-data`, so they survive a restart. Reaping of a restarted worker's jobs is off (`AGENTFIELD_AGENT_ORPHAN_REAP_ENABLED=false`), so a job waiting for an OK survives a worker restart of any length; a worker that crashes mid-step leaves its running job until AgentField's 30-minute stale timeout |
| `bun run demo:reset` | Put the demo back at the start (same as the Reset demo button on `/dev/demo`): stops jobs waiting for an OK, has each worker forget its saved steps, undo copies and pending approvals, puts the demo sites back at day 1 with every switch off, and turns daily runs off. Computers and logins stay. Works only while the agent runs with `CLIENTOPS_DEMO_RESET=on` |
| `bun run agentfield:stop` | Stop the control plane. The volume and everything in it stay |
| `bun run agentfield:reset` | Delete the volume `clientops-agentfield-data` to start clean (stop the control plane first; Docker refuses while it runs). This forgets every job, waiting approval and undo copy |
| `bun run check` | Check the real services with the keys in `.env.local`: an AgentField execution of `clientops.setup_status`, its telemetry and webhook secret, one Claude Opus 5.5 call, Kumo's Daytona computer and Lightpanda's tree of the sign-in page. Stops with one line if a key is missing. Saves the real responses under `CLIENTOPS_DATA_DIR/check/` (secrets hidden) and compares them field by field with the contract samples |
| `bun run mcp` | Start the ClientOps MCP server on stdio, for Claude or another MCP client on this computer. It talks to the running app (`bun run dev`) and offers four tools only: `start_job`, `job_status`, `waiting_approvals` (with the screen's link) and `saved_procedures`. Approving, undoing and stopping stay on the screen, done by a person |

## Layout

| Folder | Contents |
|---|---|
| `src/` | Screens and server (Next.js App Router, Astryx) |
| `agent/` | The operations agent on AgentField (Python, `uv`) |
| `sandbox/` | What runs inside each client's computer: demo sites, scripts |
| `e2e/` | Playwright UI tests |
| `scripts/` | Stage and restore |
| `issues/` | One file per issue, the index and the staging log |
| `snapshots/` | Staged snapshots and restored copies (never inside a snapshot) |
| `design/` | Design canvas sources |

## Secrets

Copy `.env.example` to `.env.local` and fill in the values yourself. Snapshots never include `.env` files other than `.env.example`, and leave out the evidence images in `issues/evidence/`.

## Settings

| Variable | Meaning |
|---|---|
| `CLIENTOPS_EXTERNALS` | `fake` uses the documented mocks for Daytona and Claude. Anything else uses the real services and stops if a key is missing |
| `CLIENTOPS_DAYTONA_TIER` | Daytona organisation tier (default 1). On tiers 1 and 2 the organisation-wide network limit applies; per-client allow lists take effect from tier 3 |
| `DAYTONA_SANDBOX_ALLOW_LIST` | `on` sends each client's allow list when its computer is created and refuses a found computer whose list differs (tier 3 and up). Anything else or unset: no per-computer list, because organisations with an organisation-wide network limit reject `domainAllowList` |
| `CLIENTOPS_DATA_DIR` | Where the audit log and settings live (default `$XDG_STATE_HOME/clientops` or `~/.local/state/clientops`). Must be outside the project |
| `AGENTFIELD_API_KEY` | Leave it empty while AgentField listens only on this computer (`bun run agentfield` binds 127.0.0.1 and runs it without a key). Only when AgentField must be reachable from elsewhere, make a long random string yourself (for example `openssl rand -hex 32`; AgentField does not issue one) and give the same value to AgentField, the app and the agent |
| `AGENTFIELD_APPROVAL_WEBHOOK_SECRET` | Shared secret that signs each approval the app sends to AgentField (HMAC-SHA256 of the body). Put it in `.env.local`; the app reads it on the server only, and `bun run agentfield` passes it to AgentField. Without it, Save changes answers with a message and nothing is approved |
| `CLIENTOPS_JOBS` | `playback` replays the recorded demo jobs on the screen only (no AgentField needed). Unset means real jobs through AgentField. `/dev/live` always uses real jobs |
| `CLIENTOPS_COMPUTERS` | `local` runs the client's computer on this Mac (local Lightpanda and demo sites). Unset or `daytona` uses Daytona |
| `CLIENTOPS_SCHEDULER` | `on` makes the agent node start each client's daily run once someone turns it on with Change (off by default; 18:00 in the client's time zone, Asia/Tokyo unless set). `bun run agent` sets it; nodes started any other way, such as by tests, never schedule. Each run goes as far as the approval and waits; nothing is written without it. While an earlier run of the same client still waits for approval, the new slot is skipped and recorded. Schedules live in `schedules.json` and each slot's record in `schedule-runs/<client>/` under `CLIENTOPS_DATA_DIR`, so a slot runs once even after a restart |
| `CLIENTOPS_AGENT_HOST` / `CLIENTOPS_AGENT_PORT` | Where the agent node listens (default `127.0.0.1:8001`, this computer only). AgentField in Docker still reaches it through `host.docker.internal`. Set the host to `0.0.0.0` only on a private network, since the node's endpoints have no key of their own |
| `CLIENTOPS_DEMO_RESET` | `on` lets the agent node reset the demo (`bun run demo:reset`, `/dev/demo`). Leave it unset anywhere real work is done, since a reset forgets every client's saved steps |
| `CLIENTOPS_APP_URL` | Where the MCP server (`bun run mcp`) finds the app (default `http://localhost:3000`). Its answers link people to this address to approve. The server and the app share a key in `mcp-token` under `CLIENTOPS_DATA_DIR` (made by the MCP server on its first start, readable only by you); the app's `/api/mcp/*` paths refuse any call without it and any call from a browser |

Per-client settings live in `settings.json` under `CLIENTOPS_DATA_DIR`: `{"version": 1, "clients": {"kumo": {"useClaude": true, "claudeMonthlyCapUsd": 10}}}`. `claudeMonthlyCapUsd` is the most Claude may cost for that client in a calendar month (UTC), in US dollars; unset means 10, and 0 means Claude is never asked. Once this month's spending plus one more call (about $0.05) would go over it, the step goes to a person, the same as with Claude turned off. Each finished job's Claude cost and computer time are added to `costs/YYYY-MM.jsonl` under `CLIENTOPS_DATA_DIR` (one line per job, a second one if the agent restarted while the job waited; monthly totals per client are read from there), and the job's own totals arrive on screen as a `cost.recorded` event.

## MCP

To let Claude Code ask for jobs, add the server once from the project folder: `claude mcp add clientops -- bun run --cwd /path/to/clientops mcp`. For Claude Desktop, put the same command (`bun`, with arguments `run`, `--cwd`, `/path/to/clientops`, `mcp`) under `mcpServers` in its config. The app must be running. A job started this way stops at the approval like any other; the person approves it on the screen.
