import { readdir } from "node:fs/promises";
import { join } from "node:path";

export type IssueStatus = "todo" | "doing" | "blocked" | "staged";

const statusLine = /^状態: .*$/m;
const completionHeading = "## 完了の条件";

export function assertIssueId(id: string) {
  if (!/^\d{3}$/.test(id)) throw new Error(`Issue number must be three digits, got "${id}"`);
}

export async function findIssueFile(issuesDir: string, id: string) {
  assertIssueId(id);
  const match = (await readdir(issuesDir)).find((name) => name.startsWith(`${id}-`) && name.endsWith(".md"));
  if (!match) throw new Error(`No issue file for ${id} in ${issuesDir}`);
  return join(issuesDir, match);
}

export function setIssueStatus(markdown: string, status: IssueStatus) {
  if (!statusLine.test(markdown)) throw new Error("Issue file has no 状態 line");
  return markdown.replace(statusLine, `状態: ${status}`);
}

export function issueTitle(markdown: string) {
  const heading = markdown.match(/^# \d{3} (.+)$/m);
  if (!heading) throw new Error("Issue file has no '# NNN title' heading");
  return heading[1].trim();
}

export function uncheckedConditions(markdown: string) {
  const start = markdown.indexOf(completionHeading);
  if (start === -1) return [];
  const rest = markdown.slice(start + completionHeading.length);
  const nextSection = rest.search(/^## /m);
  const section = nextSection === -1 ? rest : rest.slice(0, nextSection);
  return [...section.matchAll(/^- \[ \] (.+)$/gm)].map((match) => match[1].trim());
}

export function setIndexStatus(indexMarkdown: string, id: string, status: IssueStatus) {
  const row = new RegExp(`^(\\| \\[${id}\\]\\(.*\\| )[^|]*( \\|)$`, "m");
  if (!row.test(indexMarkdown)) throw new Error(`Issue ${id} is not in the index`);
  return indexMarkdown.replace(row, `$1${status}$2`);
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const twoDigits = (value: number) => String(value).padStart(2, "0");

export function localDate(at: Date) {
  return `${at.getFullYear()}-${twoDigits(at.getMonth() + 1)}-${twoDigits(at.getDate())}`;
}

export function localTime(at: Date) {
  return `${twoDigits(at.getHours())}:${twoDigits(at.getMinutes())}`;
}

export function formatLogLine(entry: { at: Date; id: string; title: string; snapshot: string; bytes: number }) {
  return `| ${localDate(entry.at)} ${localTime(entry.at)} | ${entry.id} ${entry.title} | ${entry.snapshot} | ${formatBytes(entry.bytes)} |`;
}
