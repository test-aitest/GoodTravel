import { describe, expect, test } from "bun:test";
import { realpathSync } from "node:fs";
import { mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { isMcpCaller, MCP_TOKEN_FILE, McpTokenError, mcpToken, mcpTokenPath } from "./mcpKey";

async function dataDir() {
  return join(realpathSync(await mkdtemp(join(tmpdir(), "clientops-mcp-token-"))), "state");
}

const headers = (values: Record<string, string>) => ({ get: (name: string) => values[name.toLowerCase()] ?? null });

describe("the MCP key file", () => {
  test("is made once in the private data directory, readable only by this user", async () => {
    const directory = await dataDir();
    const env = { CLIENTOPS_DATA_DIR: directory };

    const token = await mcpToken(env);

    expect(token).toMatch(/^[0-9a-f]{64}$/);
    expect(mcpTokenPath(env)).toBe(join(directory, MCP_TOKEN_FILE));
    expect((await readFile(join(directory, MCP_TOKEN_FILE), "utf8")).trim()).toBe(token);
    expect((await stat(join(directory, MCP_TOKEN_FILE))).mode & 0o777).toBe(0o600);
    expect((await stat(directory)).mode & 0o777).toBe(0o700);
    expect(await mcpToken(env)).toBe(token);
  });

  test("is the same for the app and the MCP server even when both make it at once", async () => {
    const env = { CLIENTOPS_DATA_DIR: await dataDir() };

    const tokens = await Promise.all(Array.from({ length: 12 }, () => mcpToken(env)));

    expect(new Set(tokens).size).toBe(1);
    expect((await readFile(mcpTokenPath(env), "utf8")).trim()).toBe(tokens[0]);
  });

  test("refuses a key file that is not a key instead of quietly replacing it", async () => {
    const env = { CLIENTOPS_DATA_DIR: await dataDir() };
    await mcpToken(env);
    await writeFile(mcpTokenPath(env), "not-a-key\n");

    await expect(mcpToken(env)).rejects.toBeInstanceOf(McpTokenError);
  });

  test("is never inside the project, so it can't end up in a snapshot", async () => {
    await expect(mcpToken({ CLIENTOPS_DATA_DIR: join(process.cwd(), "state") })).rejects.toThrow("outside the project");
  });
});

describe("isMcpCaller", () => {
  const key = "ab".repeat(32);

  test("accepts the key sent as a bearer token", () => {
    expect(isMcpCaller(headers({ authorization: `Bearer ${key}` }), key)).toBe(true);
    expect(isMcpCaller(headers({ authorization: `bearer ${key}` }), key)).toBe(true);
  });

  test("refuses a missing, different, shortened or padded key", () => {
    expect(isMcpCaller(headers({}), key)).toBe(false);
    expect(isMcpCaller(headers({ authorization: `Bearer ${"cd".repeat(32)}` }), key)).toBe(false);
    expect(isMcpCaller(headers({ authorization: `Bearer ${key.slice(2)}` }), key)).toBe(false);
    expect(isMcpCaller(headers({ authorization: `Bearer ${key} ` }), key)).toBe(false);
    expect(isMcpCaller(headers({ authorization: `Basic ${key}` }), key)).toBe(false);
    expect(isMcpCaller(headers({ authorization: key }), key)).toBe(false);
  });
});
