import { expect, test, type Page } from "@playwright/test";

const agentField = process.env.AGENTFIELD_SERVER ?? "http://localhost:8080";

async function clientOpsNodeIsActive() {
  const response = await fetch(`${agentField}/api/v1/nodes`).catch(() => null);
  if (!response?.ok) return false;
  const body = (await response.json()) as { nodes?: { id?: string; node_id?: string }[] };
  return (body.nodes ?? []).some((node) => node.id === "clientops" || node.node_id === "clientops");
}

async function addLogin(page: Page, site: string) {
  await page.getByRole("button", { name: `Add login for ${site}` }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Username").fill("demo-user");
  await dialog.getByLabel("Password").fill("demo-pass");
  await dialog.getByRole("button", { name: "Add login" }).click();
}

test("a real job runs through AgentField on the local computer and stops at the approval", async ({ page }) => {
  test.skip(
    process.env.CLIENTOPS_E2E_LIVE !== "1" || !(await clientOpsNodeIsActive()),
    "Set CLIENTOPS_E2E_LIVE=1 and run AgentField (`bun run agentfield`), the agent with CLIENTOPS_EXTERNALS=fake CLIENTOPS_COMPUTERS=local and Kumo's local vault, `bun run lightpanda` and the demo sites.",
  );
  test.setTimeout(120_000);

  await page.goto("/dev/live");
  await page.getByRole("button", { name: "Create its computer" }).click();
  await addLogin(page, "Maruyama Supply portal");
  await addLogin(page, "Kumo store admin");
  await page.getByRole("button", { name: "Write the first job" }).click();
  await page.getByText("Sync today's supplier stock to the store admin").click();

  await expect(page.getByRole("heading", { level: 1, name: "Supplier stock sync" })).toBeVisible({ timeout: 30_000 });
  const steps = page.getByRole("complementary", { name: "Activity" }).getByRole("list", { name: "Steps" });
  await expect(steps.getByRole("listitem")).toHaveCount(6);
  await expect(page.getByRole("group", { name: "Save 12 stock changes to the store admin?" })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTitle(/The page the worker saw on/)).toBeVisible();
});
