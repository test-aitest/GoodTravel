import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { listSnapshotFiles, restoreSnapshot, snapshotFileName } from "./snapshot";
import { stageIssue } from "./stage";

const issueBody = (box: "x" | " ") => `# 001 プロジェクトの土台

状態: doing
段階: M0 土台
依存: なし

## 完了の条件
- [${box}] stage・restore が動く

## 結果（実装後に書く）
- 日付:
`;

const indexBody = `| 番号 | 課題 | 段階 | 依存 | 状態 |
|---|---|---|---|---|
| [001](001-foundation.md) | プロジェクトの土台 | M0 土台 | なし | doing |
`;

const logBody = `# ステージングの記録

| 日時 | 課題 | 写し | メモ |
|---|---|---|---|
`;

const projectFiles: Record<string, string> = {
  "issues/001-foundation.md": issueBody("x"),
  "issues/INDEX.md": indexBody,
  "issues/LOG.md": logBody,
  "src/app/page.tsx": "export default function Page() { return null; }\n",
  "agent/pyproject.toml": "[project]\nname = \"agent\"\n",
  "agent/.venv/lib/site.py": "venv\n",
  "agent/tests/__pycache__/x.pyc": "cache\n",
  "node_modules/pkg/index.js": "module\n",
  "src/node_modules/nested/index.js": "nested module\n",
  ".next/server/page.js": "built\n",
  "snapshots/000-20261101-000000.tar.gz": "older snapshot\n",
  "test-results/result.json": "{}\n",
  "playwright-report/index.html": "<html></html>\n",
  ".env.local": "ANTHROPIC_API_KEY=secret\n",
  ".env": "SECRET=1\n",
  ".env.example": "ANTHROPIC_API_KEY=\n",
  ".DS_Store": "finder\n",
  "tsconfig.tsbuildinfo": "{}\n",
  "design/project/Main.dc.html": "<html></html>\n",
};

let root: string;

async function writeProject(files: Record<string, string>) {
  for (const [path, body] of Object.entries(files)) {
    await mkdir(dirname(join(root, path)), { recursive: true });
    await writeFile(join(root, path), body);
  }
}

async function archiveEntries(file: string) {
  const listing = Bun.spawnSync(["tar", "-tzf", file]);
  expect(listing.exitCode).toBe(0);
  return listing.stdout.toString().trim().split("\n").toSorted();
}

beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), "clientops-stage-"));
  await writeProject(projectFiles);
});

afterEach(async () => {
  await rm(root, { recursive: true, force: true });
});

const at = new Date(2026, 10, 14, 15, 30, 5);

describe("snapshotFileName", () => {
  test("is the issue number and local date and time", () => {
    expect(snapshotFileName("001", at)).toBe("001-20261114-153005.tar.gz");
  });
});

describe("listSnapshotFiles", () => {
  test("keeps source and the example env, leaves out builds, caches, snapshots and secrets", async () => {
    expect(await listSnapshotFiles(root)).toEqual([
      ".env.example",
      "agent/pyproject.toml",
      "design/project/Main.dc.html",
      "issues/001-foundation.md",
      "issues/INDEX.md",
      "issues/LOG.md",
      "src/app/page.tsx",
    ]);
  });
});

describe("evidence images", () => {
  test("stay in issues/evidence but are left out of snapshots", async () => {
    await writeProject({ "issues/evidence/007-0-setup.png": "image\n", "src/evidence/kept.ts": "export {};\n" });
    const files = await listSnapshotFiles(root);
    expect(files).not.toContain("issues/evidence/007-0-setup.png");
    expect(files).toContain("src/evidence/kept.ts");
  });
});

describe("vendor binaries", () => {
  test("stay in vendor but are left out of snapshots", async () => {
    await writeProject({ "vendor/lightpanda/lightpanda": "binary\n" });
    const files = await listSnapshotFiles(root);
    expect(files.some((file) => file.startsWith("vendor/"))).toBe(false);
    expect(files).toContain("src/app/page.tsx");
  });
});

describe("stageIssue", () => {
  test("marks the issue staged, saves a snapshot and logs one line", async () => {
    const result = await stageIssue({ root, id: "001", at });

    expect(result.snapshot).toBe("snapshots/001-20261114-153005.tar.gz");
    expect(result.bytes).toBeGreaterThan(0);
    expect(await readFile(join(root, "issues/001-foundation.md"), "utf8")).toContain("状態: staged\n");
    expect(await readFile(join(root, "issues/INDEX.md"), "utf8")).toContain("| なし | staged |");

    const log = await readFile(join(root, "issues/LOG.md"), "utf8");
    expect(log.trimEnd().split("\n").at(-1)).toMatch(
      /^\| 2026-11-14 15:30 \| 001 プロジェクトの土台 \| snapshots\/001-20261114-153005\.tar\.gz \| \d+(\.\d)? (B|KB) \|$/,
    );

    const entries = await archiveEntries(join(root, result.snapshot));
    expect(entries).toEqual(await listSnapshotFiles(root));
    expect(entries).not.toContain(".env.local");
    expect(entries.some((entry) => entry.includes("node_modules"))).toBe(false);
  });

  test("the snapshot holds the issue already marked staged", async () => {
    const result = await stageIssue({ root, id: "001", at });
    const restored = await restoreSnapshot({ root, id: "001" });
    expect(restored).toBe(join(root, "snapshots/restored/001-20261114-153005"));
    expect(await readFile(join(restored, "issues/001-foundation.md"), "utf8")).toContain("状態: staged\n");
    expect(result.bytes).toBeLessThan(5 * 1024 * 1024);
  });

  test("refuses while a completion condition is unchecked and changes nothing", async () => {
    await writeFile(join(root, "issues/001-foundation.md"), issueBody(" "));

    await expect(stageIssue({ root, id: "001", at })).rejects.toThrow("stage・restore が動く");

    expect(await readFile(join(root, "issues/001-foundation.md"), "utf8")).toBe(issueBody(" "));
    expect(await readFile(join(root, "issues/INDEX.md"), "utf8")).toBe(indexBody);
    expect(await readFile(join(root, "issues/LOG.md"), "utf8")).toBe(logBody);
    expect(await readdir(join(root, "snapshots"))).toEqual(["000-20261101-000000.tar.gz"]);
  });

  test("refuses a snapshot over the size limit and puts the issue files back", async () => {
    await expect(stageIssue({ root, id: "001", at, limitBytes: 10 })).rejects.toThrow("limit");

    expect(await readFile(join(root, "issues/001-foundation.md"), "utf8")).toBe(issueBody("x"));
    expect(await readFile(join(root, "issues/INDEX.md"), "utf8")).toBe(indexBody);
    expect(await readFile(join(root, "issues/LOG.md"), "utf8")).toBe(logBody);
    expect(await readdir(join(root, "snapshots"))).toEqual(["000-20261101-000000.tar.gz"]);
  });

  test("refuses an issue number that has no file", async () => {
    await expect(stageIssue({ root, id: "042", at })).rejects.toThrow("042");
  });
});

describe("restoreSnapshot", () => {
  test("unpacks the newest snapshot of the issue into a new folder and leaves current files alone", async () => {
    await stageIssue({ root, id: "001", at });
    await writeFile(join(root, "src/app/page.tsx"), "changed after staging\n");
    await stageIssue({ root, id: "001", at: new Date(2026, 10, 14, 16, 0, 0) });

    const restored = await restoreSnapshot({ root, id: "001" });

    expect(restored).toBe(join(root, "snapshots/restored/001-20261114-160000"));
    expect(await readFile(join(restored, "src/app/page.tsx"), "utf8")).toBe("changed after staging\n");
    expect(await readFile(join(root, "src/app/page.tsx"), "utf8")).toBe("changed after staging\n");
  });

  test("restores a named snapshot", async () => {
    await stageIssue({ root, id: "001", at });
    const restored = await restoreSnapshot({ root, id: "001", name: "001-20261114-153005.tar.gz" });
    expect(await readFile(join(restored, "src/app/page.tsx"), "utf8")).toBe(projectFiles["src/app/page.tsx"]);
  });

  test("refuses to unpack over a folder that already exists", async () => {
    await stageIssue({ root, id: "001", at });
    await restoreSnapshot({ root, id: "001" });
    await expect(restoreSnapshot({ root, id: "001" })).rejects.toThrow("exists");
  });

  test("refuses when the issue has no snapshot", async () => {
    await expect(restoreSnapshot({ root, id: "001" })).rejects.toThrow("001");
    expect(existsSync(join(root, "snapshots/restored"))).toBe(false);
  });
});
