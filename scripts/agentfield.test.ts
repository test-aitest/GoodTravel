import { describe, expect, test } from "bun:test";
import packageJson from "../package.json";

describe("the AgentField control plane command", () => {
  test("keeps jobs that wait for an OK when the worker restarts", () => {
    expect(packageJson.scripts["agentfield:container"]).toContain("-e AGENTFIELD_AGENT_ORPHAN_REAP_ENABLED=false");
  });

  test("answers only this computer, not the rest of the network", () => {
    expect(packageJson.scripts["agentfield:container"]).toContain("-p 127.0.0.1:8080:8080");
  });

  test("keeps its data, sends no telemetry and takes signed approvals", () => {
    const command = packageJson.scripts["agentfield:container"];
    for (const part of ["-v clientops-agentfield-data:/data", "-e AGENTFIELD_TELEMETRY_ENABLED=false", "-e AGENTFIELD_APPROVAL_WEBHOOK_SECRET", "agentfield/control-plane:v0.1.145@sha256:"]) {
      expect(command).toContain(part);
    }
  });
});

describe("the agent node command", () => {
  test("loads .env.local, so keys put there reach the agent", () => {
    expect(packageJson.scripts.agent).toBe("bun --env-file=.env.local run agent:node");
    expect(packageJson.scripts["agent:node"]).toContain("uv run --directory agent python serve.py");
    expect(packageJson.scripts["agent:node"]).toContain("CLIENTOPS_SCHEDULER=on");
  });
});
