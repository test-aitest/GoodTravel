import { appendFile, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import {
  findIssueFile,
  formatBytes,
  formatLogLine,
  issueTitle,
  setIndexStatus,
  setIssueStatus,
  uncheckedConditions,
} from "./issues";
import { SNAPSHOT_LIMIT_BYTES, createSnapshot, listSnapshotFiles, snapshotFileName } from "./snapshot";

export async function stageIssue(options: { root: string; id: string; at: Date; limitBytes?: number }) {
  const issuesDir = join(options.root, "issues");
  const issuePath = await findIssueFile(issuesDir, options.id);
  const indexPath = join(issuesDir, "INDEX.md");
  const logPath = join(issuesDir, "LOG.md");

  const issue = await readFile(issuePath, "utf8");
  const unchecked = uncheckedConditions(issue);
  if (unchecked.length > 0) throw new Error(`Unchecked completion conditions: ${unchecked.join(" / ")}`);

  const index = await readFile(indexPath, "utf8");
  const stagedIssue = setIssueStatus(issue, "staged");
  const stagedIndex = setIndexStatus(index, options.id, "staged");

  await writeFile(issuePath, stagedIssue);
  await writeFile(indexPath, stagedIndex);
  try {
    const snapshot = await createSnapshot({
      root: options.root,
      files: await listSnapshotFiles(options.root),
      name: snapshotFileName(options.id, options.at),
      limitBytes: options.limitBytes ?? SNAPSHOT_LIMIT_BYTES,
    });
    const line = formatLogLine({ at: options.at, id: options.id, title: issueTitle(issue), snapshot: snapshot.path, bytes: snapshot.bytes });
    const log = await readFile(logPath, "utf8");
    await appendFile(logPath, `${log.endsWith("\n") ? "" : "\n"}${line}\n`);
    return { snapshot: snapshot.path, bytes: snapshot.bytes };
  } catch (error) {
    await writeFile(issuePath, issue);
    await writeFile(indexPath, index);
    throw error;
  }
}

const checks = ["typecheck", "lint", "test", "test:ui"];

async function runChecks(root: string) {
  for (const script of checks) {
    console.log(`\n> bun run ${script}`);
    const run = Bun.spawnSync(["bun", "run", script], { cwd: root, stdout: "inherit", stderr: "inherit" });
    if (run.exitCode !== 0) throw new Error(`bun run ${script} failed, not staging`);
  }
}

if (import.meta.main) {
  const id = process.argv[2];
  if (!id) {
    console.error("Usage: bun run stage NNN");
    process.exit(1);
  }
  const root = process.cwd();
  try {
    await findIssueFile(join(root, "issues"), id);
    await runChecks(root);
    const result = await stageIssue({ root, id, at: new Date() });
    console.log(`\nStaged ${id}: ${result.snapshot} (${formatBytes(result.bytes)})`);
  } catch (error) {
    console.error(`\n${error instanceof Error ? error.message : error}`);
    process.exit(1);
  }
}
