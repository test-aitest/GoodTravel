import { relative } from "node:path";
import { restoreSnapshot } from "./snapshot";

const [id, name] = process.argv.slice(2);
if (!id) {
  console.error("Usage: bun run restore NNN [snapshot-file-name]");
  process.exit(1);
}

try {
  const destination = await restoreSnapshot({ root: process.cwd(), id, name });
  console.log(`Restored into ${relative(process.cwd(), destination)}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}
