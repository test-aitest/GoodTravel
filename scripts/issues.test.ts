import { describe, expect, test } from "bun:test";
import {
  formatBytes,
  formatLogLine,
  issueTitle,
  setIndexStatus,
  setIssueStatus,
  uncheckedConditions,
} from "./issues";

const issue = `# 001 プロジェクトの土台（Git を使わない運用）

状態: doing
段階: M0 土台
依存: なし

## 完了の条件
- [x] build・test・test:ui が通る
- [ ] stage・restore が動く
- [X] 依存が最新

## 結果（実装後に書く）
- [ ] this box is outside the conditions
`;

const index = `| 番号 | 課題 | 段階 | 依存 | 状態 |
|---|---|---|---|---|
| [001](001-foundation.md) | プロジェクトの土台 | M0 土台 | なし | doing |
| [002](002-demo-sites.md) | デモ用サイト | M0 土台 | 001 | todo |
`;

describe("setIssueStatus", () => {
  test("replaces only the status line", () => {
    const staged = setIssueStatus(issue, "staged");
    expect(staged).toContain("状態: staged\n");
    expect(staged).not.toContain("状態: doing");
    expect(staged.replace("状態: staged", "状態: doing")).toBe(issue);
  });

  test("refuses a file without a status line", () => {
    expect(() => setIssueStatus("# 009 x\n", "staged")).toThrow("状態");
  });
});

describe("issueTitle", () => {
  test("reads the title after the number", () => {
    expect(issueTitle(issue)).toBe("プロジェクトの土台（Git を使わない運用）");
  });
});

describe("uncheckedConditions", () => {
  test("lists unchecked boxes inside the completion section only", () => {
    expect(uncheckedConditions(issue)).toEqual(["stage・restore が動く"]);
  });

  test("is empty when every condition is checked", () => {
    expect(uncheckedConditions(issue.replace("- [ ] stage", "- [x] stage"))).toEqual([]);
  });
});

describe("setIndexStatus", () => {
  test("changes the status cell of the matching row only", () => {
    const updated = setIndexStatus(index, "001", "staged");
    expect(updated).toContain("| [001](001-foundation.md) | プロジェクトの土台 | M0 土台 | なし | staged |");
    expect(updated).toContain("| [002](002-demo-sites.md) | デモ用サイト | M0 土台 | 001 | todo |");
  });

  test("refuses an id that is not in the table", () => {
    expect(() => setIndexStatus(index, "099", "staged")).toThrow("099");
  });
});

describe("formatLogLine", () => {
  test("writes one table row with local time, issue, snapshot and size", () => {
    const line = formatLogLine({
      at: new Date(2026, 10, 14, 9, 5, 7),
      id: "001",
      title: "プロジェクトの土台",
      snapshot: "snapshots/001-20261114-090507.tar.gz",
      bytes: 1536,
    });
    expect(line).toBe(
      "| 2026-11-14 09:05 | 001 プロジェクトの土台 | snapshots/001-20261114-090507.tar.gz | 1.5 KB |",
    );
  });
});

describe("formatBytes", () => {
  test("uses B, KB and MB", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});
