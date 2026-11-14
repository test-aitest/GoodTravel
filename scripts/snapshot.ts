import { existsSync } from "node:fs";
import { mkdir, readdir, rename, stat, unlink } from "node:fs/promises";
import { join, relative } from "node:path";
import { assertIssueId, localDate } from "./issues";

export const SNAPSHOT_LIMIT_BYTES = 5 * 1024 * 1024;
export const SNAPSHOT_DIR = "snapshots";
export const RESTORE_DIR = join(SNAPSHOT_DIR, "restored");

const excludedDirectories = new Set([
  ".git",
  ".next",
  ".venv",
  ".pytest_cache",
  ".ruff_cache",
  "__pycache__",
  "blob-report",
  "coverage",
  "node_modules",
  "playwright-report",
  "test-results",
  "vendor",
  SNAPSHOT_DIR,
]);

const excludedPaths = new Set([join("issues", "evidence")]);

function isExcludedFile(name: string) {
  if (name === ".DS_Store" || name.endsWith(".tsbuildinfo")) return true;
  return name.startsWith(".env") && name !== ".env.example";
}

export async function listSnapshotFiles(root: string) {
  const files: string[] = [];
  async function walk(directory: string) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) {
        if (!excludedDirectories.has(entry.name) && !excludedPaths.has(relative(root, path))) await walk(path);
      } else if (entry.isFile() && !isExcludedFile(entry.name)) {
        files.push(relative(root, path));
      }
    }
  }
  await walk(root);
  return files.toSorted();
}

export function snapshotFileName(id: string, at: Date) {
  const time = [at.getHours(), at.getMinutes(), at.getSeconds()].map((part) => String(part).padStart(2, "0")).join("");
  return `${id}-${localDate(at).replaceAll("-", "")}-${time}.tar.gz`;
}

const tarEnvironment = { ...process.env, COPYFILE_DISABLE: "1" };

export async function createSnapshot(options: { root: string; files: string[]; name: string; limitBytes: number }) {
  const directory = join(options.root, SNAPSHOT_DIR);
  await mkdir(directory, { recursive: true });
  const target = join(directory, options.name);
  if (existsSync(target)) throw new Error(`Snapshot ${options.name} already exists`);
  const partial = `${target}.partial`;

  const archive = Bun.spawnSync(["tar", "--null", "--no-xattrs", "-czf", partial, "-C", options.root, "-T", "-"], {
    stdin: new Blob([options.files.join("\0")]),
    env: tarEnvironment,
  });
  if (archive.exitCode !== 0) {
    if (existsSync(partial)) await unlink(partial);
    throw new Error(`tar failed: ${archive.stderr.toString().trim()}`);
  }

  const { size } = await stat(partial);
  if (size > options.limitBytes) {
    await unlink(partial);
    throw new Error(`Snapshot is ${size} bytes, over the ${options.limitBytes} byte limit`);
  }
  await rename(partial, target);
  return { path: join(SNAPSHOT_DIR, options.name), bytes: size };
}

export async function restoreSnapshot(options: { root: string; id: string; name?: string }) {
  assertIssueId(options.id);
  const directory = join(options.root, SNAPSHOT_DIR);
  const available = existsSync(directory)
    ? (await readdir(directory)).filter((name) => name.startsWith(`${options.id}-`) && name.endsWith(".tar.gz")).toSorted()
    : [];
  const name = options.name ?? available.at(-1);
  if (!name || !available.includes(name)) throw new Error(`No snapshot for ${options.id}${options.name ? ` named ${options.name}` : ""}`);

  const destination = join(options.root, RESTORE_DIR, name.replace(/\.tar\.gz$/, ""));
  if (existsSync(destination)) throw new Error(`${relative(options.root, destination)} already exists`);
  await mkdir(destination, { recursive: true });

  const unpack = Bun.spawnSync(["tar", "-xzf", join(directory, name), "-C", destination], { env: tarEnvironment });
  if (unpack.exitCode !== 0) throw new Error(`tar failed: ${unpack.stderr.toString().trim()}`);
  return destination;
}
