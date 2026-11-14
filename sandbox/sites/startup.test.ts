import { expect, test } from "bun:test";
import { join } from "node:path";

test("the sites answer within 1 second of starting the process", async () => {
  const started = performance.now();
  const server = Bun.spawn(["bun", join(import.meta.dir, "server.ts")], {
    env: { ...process.env, SUPPLIER_PORT: "0", STORE_PORT: "0" },
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
    const urls = [...output.matchAll(/http:\/\/127\.0\.0\.1:\d+/g)].map((match) => match[0]);
    expect(urls).toHaveLength(2);
    const statuses = await Promise.all(urls.map(async (url) => (await fetch(`${url}/login`)).status));
    const elapsed = performance.now() - started;

    expect(statuses).toEqual([200, 200]);
    expect(elapsed).toBeLessThan(1000);
  } finally {
    server.kill();
    await server.exited;
  }
});
