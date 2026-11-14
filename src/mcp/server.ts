import { serveStdio } from "@modelcontextprotocol/server/stdio";
import { mcpToken } from "../lib/mcpKey";
import { clientOpsMcpServer } from "./tools";

const appUrl = process.env.CLIENTOPS_APP_URL || "http://localhost:3000";

await mcpToken();

serveStdio(() => clientOpsMcpServer({ appUrl, key: () => mcpToken(), fetch }));
