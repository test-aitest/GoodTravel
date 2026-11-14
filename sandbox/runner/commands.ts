import { BrowserGone, CdpError, type Tab } from "./cdp";
import { locate, type Locator } from "./locate";
import { placeholdersIn, resolvePlaceholders, scrub, secretsIn, type Environment } from "./placeholders";
import { sanitizeSnapshot, snapshotMaxBytes } from "./sanitize";
import { elementsOf, parseTextTree, tablesOf, textOf, type PageElement, type SemanticNode } from "./tree";
import type { Vault } from "./vault";

export type Target = { ref: number } | Locator;

export type Command =
  | { command: "open"; url: string; snapshot?: boolean }
  | { command: "tree"; mode: "interactive" | "full"; snapshot?: boolean }
  | { command: "find"; role: string; name: string; form?: string | null }
  | { command: "click"; target: Target; waitFor?: string; timeoutMs?: number; snapshot?: boolean }
  | { command: "fill"; target: Target; text: string; snapshot?: boolean }
  | { command: "wait"; selector: string; timeoutMs?: number }
  | { command: "readTable"; next?: Locator; waitFor?: string; maxPages?: number; table?: number }
  | { command: "snapshot"; maxBytes?: number }
  | { command: "script"; source: string; timeoutMs?: number }
  | { command: "close" };

export type CommandName = Command["command"];

export type ErrorCode =
  | "bad-command"
  | "no-page"
  | "browser-unreachable"
  | "navigate-failed"
  | "not-found"
  | "not-unique"
  | "placeholder-unset"
  | "vault-unreadable"
  | "fill-not-applied"
  | "wait-failed"
  | "next-page-unchanged"
  | "too-many-pages"
  | "cdp-error"
  | "script-timeout"
  | "runner-error";

export type RunnerError = { code: ErrorCode; message: string; count?: number; missing?: string[] };
export type PageInfo = { url: string; title: string; status?: number };
export type ElementSummary = { ref: number; role: string; name: string; form: string | null };
export type PageSnapshot = { url: string; host: string; title: string; html: string; bytes: number; truncated: boolean };

type WithSnapshot = { snapshot?: PageSnapshot };

export type ScriptStep = { stepId: string; status: "done" | "failed"; tookMs: number; checks: number; pages: PageSnapshot[] };
export type ScriptFailure = { stepId: string | null; kind: "check" | "step"; code: string | null; message: string };
export type ScriptOutcome = "passed" | "check-failed" | "step-failed";
export type ScriptTable = { columns: string[]; rows: string[][]; pages: number };

export type RunnerResult =
  | ({ ok: true; command: "open"; page: PageInfo } & WithSnapshot)
  | ({ ok: true; command: "tree"; page: PageInfo; mode: "interactive" | "full"; text: string; elements: PageElement[]; cells: string[] } & WithSnapshot)
  | { ok: true; command: "find"; page: PageInfo; element: PageElement; selector: string }
  | ({ ok: true; command: "click"; page: PageInfo; element: ElementSummary } & WithSnapshot)
  | ({ ok: true; command: "fill"; page: PageInfo; element: ElementSummary } & WithSnapshot)
  | { ok: true; command: "wait"; page: PageInfo; selector: string; text: string }
  | { ok: true; command: "readTable"; page: PageInfo; columns: string[]; rows: string[][]; pages: number }
  | { ok: true; command: "snapshot"; page: PageInfo; snapshot: PageSnapshot }
  | {
      ok: true;
      command: "script";
      outcome: ScriptOutcome;
      tookMs: number;
      steps: ScriptStep[];
      tables: Record<string, ScriptTable>;
      failure?: ScriptFailure;
      result?: Record<string, unknown>;
    }
  | { ok: true; command: "close" }
  | { ok: false; command: CommandName | "unknown"; error: RunnerError };

export type Failure = Extract<RunnerResult, { ok: false }>;

export class RunnerFailure extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
    readonly extra: { count?: number; missing?: string[] } = {},
  ) {
    super(message);
  }
}

export function failure(command: CommandName | "unknown", code: ErrorCode, message: string, extra: { count?: number; missing?: string[] } = {}): Failure {
  return { ok: false, command, error: { code, message, ...extra } };
}

type Parsed = { ok: true; command: Command } | { ok: false; message: string };
type Fields = Record<string, unknown>;

const commandFields: Record<CommandName, { required: string[]; optional: string[] }> = {
  open: { required: ["url"], optional: ["snapshot"] },
  tree: { required: ["mode"], optional: ["snapshot"] },
  find: { required: ["role", "name"], optional: ["form"] },
  click: { required: ["target"], optional: ["waitFor", "timeoutMs", "snapshot"] },
  fill: { required: ["target", "text"], optional: ["snapshot"] },
  wait: { required: ["selector"], optional: ["timeoutMs"] },
  readTable: { required: [], optional: ["next", "waitFor", "maxPages", "table"] },
  snapshot: { required: [], optional: ["maxBytes"] },
  script: { required: ["source"], optional: ["timeoutMs"] },
  close: { required: [], optional: [] },
};

const isObject = (value: unknown): value is Fields => typeof value === "object" && value !== null && !Array.isArray(value);
const isText = (value: unknown): value is string => typeof value === "string" && value.trim() !== "";
const isCount = (value: unknown) => Number.isInteger(value) && (value as number) > 0;

function locatorProblem(value: unknown, label: string) {
  if (!isObject(value)) return `${label} must be an object`;
  const extra = Object.keys(value).filter((key) => !["role", "name", "form"].includes(key));
  if (extra.length > 0) return `${label} has unknown fields: ${extra.join(", ")}`;
  if (!isText(value.role) || typeof value.name !== "string") return `${label} needs a role and a name`;
  if (value.form !== undefined && value.form !== null && typeof value.form !== "string") return `${label}.form must be text or null`;
  return null;
}

function targetProblem(value: unknown) {
  if (isObject(value) && "ref" in value) {
    return Object.keys(value).length === 1 && isCount(value.ref) ? null : "target.ref must be a positive whole number on its own";
  }
  return locatorProblem(value, "target");
}

function fieldProblem(name: CommandName, fields: Fields): string | null {
  switch (name) {
    case "open": {
      if (typeof fields.url !== "string") return "url must be text";
      let protocol: string;
      try {
        protocol = new URL(fields.url).protocol;
      } catch {
        return `url is not a URL: ${fields.url}`;
      }
      return protocol === "http:" || protocol === "https:" ? null : `Only http and https pages can be opened, not ${protocol}`;
    }
    case "tree":
      return fields.mode === "interactive" || fields.mode === "full" ? null : "mode must be interactive or full";
    case "find":
      return locatorProblem({ role: fields.role, name: fields.name, ...(fields.form === undefined ? {} : { form: fields.form }) }, "find");
    case "click":
      return targetProblem(fields.target) ?? (fields.waitFor !== undefined && !isText(fields.waitFor) ? "waitFor must be a CSS selector" : null);
    case "fill":
      return targetProblem(fields.target) ?? (typeof fields.text === "string" ? null : "text must be text");
    case "wait":
      return isText(fields.selector) ? null : "selector must be a CSS selector";
    case "readTable":
      if (fields.next !== undefined) return locatorProblem(fields.next, "next");
      return fields.waitFor !== undefined && !isText(fields.waitFor) ? "waitFor must be a CSS selector" : null;
    case "script":
      return isText(fields.source) ? null : "source must be the text of a script";
    case "snapshot":
    case "close":
      return null;
  }
}

export function parseCommand(input: unknown): Parsed {
  if (!isObject(input)) return { ok: false, message: "A command is a JSON object" };
  const name = input.command;
  if (typeof name !== "string" || !Object.hasOwn(commandFields, name)) {
    return { ok: false, message: `Unknown command ${JSON.stringify(name)}. Known: ${Object.keys(commandFields).join(", ")}` };
  }
  const known = commandFields[name as CommandName];
  const keys = Object.keys(input).filter((key) => key !== "command");
  const missing = known.required.filter((key) => input[key] === undefined);
  if (missing.length > 0) return { ok: false, message: `${name} needs ${missing.join(", ")}` };
  const unknown = keys.filter((key) => !known.required.includes(key) && !known.optional.includes(key));
  if (unknown.length > 0) return { ok: false, message: `${name} does not take ${unknown.join(", ")}` };
  for (const key of ["timeoutMs", "maxPages", "table", "maxBytes"]) {
    if (input[key] !== undefined && !isCount(input[key])) return { ok: false, message: `${key} must be a positive whole number` };
  }
  if (input.snapshot !== undefined && typeof input.snapshot !== "boolean") return { ok: false, message: "snapshot must be true or false" };
  const problem = fieldProblem(name as CommandName, input);
  return problem ? { ok: false, message: problem } : { ok: true, command: input as Command };
}

export type Prepared = { ok: true; command: Command; secrets: string[] } | { ok: false; result: Failure };

export function prepareCommand(command: Command, vault: Vault, env: Environment): Prepared {
  const known = [...new Set([...(vault.ok ? Object.values(vault.values) : []), ...secretsIn(env)])];
  if (command.command !== "fill" || placeholdersIn(command.text).length === 0) return { ok: true, command, secrets: known };
  if (!vault.ok && vault.problem !== "missing") return { ok: false, result: failure("fill", "vault-unreadable", vault.message) };
  const resolved = resolvePlaceholders(command.text, vault.ok ? vault.values : {});
  if (!resolved.ok) {
    const names = resolved.missing.join(" and ");
    const verb = resolved.missing.length === 1 ? "is" : "are";
    const why = vault.ok ? "" : ` (${vault.message})`;
    return {
      ok: false,
      result: failure("fill", "placeholder-unset", `${names} ${verb} not in the vault on this computer${why}`, { missing: resolved.missing }),
    };
  }
  return { ok: true, command: { ...command, text: resolved.text }, secrets: [...new Set([...resolved.secrets, ...known])] };
}

function summary(element: PageElement): ElementSummary {
  return { ref: element.ref, role: element.role, name: element.name, form: element.form };
}

function describeLocator(locator: Locator, count: number) {
  const role = count > 1 ? `${locator.role}s` : locator.role;
  const where = locator.form === undefined ? "" : locator.form === null ? " outside any form" : ` in form ${locator.form}`;
  return `${count === 0 ? "No" : count} ${role} named "${locator.name}"${where} on this page`;
}

const sameRows = (a: readonly string[][], b: readonly string[][]) => JSON.stringify(a) === JSON.stringify(b);
const typedValueTypes = new Set(["password", "hidden", "checkbox", "radio", "file"]);
const defaultMaxPages = 50;
const waitMarginMs = 5_000;
const defaultWaitMs = 15_000;
const loadedPageWaitMs = 5_000;
const lookAgainMs = 100;

export class Runner {
  private tab: Tab | null = null;
  private secrets = new Set<string>();

  constructor(private readonly openTab: () => Promise<Tab>) {}

  async run(command: Command, secrets: readonly string[] = []): Promise<RunnerResult> {
    for (const secret of secrets) if (secret) this.secrets.add(secret);
    let result: RunnerResult;
    try {
      result = await this.execute(command);
    } catch (error) {
      result = this.failed(command.command, error);
    }
    return scrub(result, [...this.secrets]);
  }

  private failed(command: CommandName, error: unknown): Failure {
    if (error instanceof RunnerFailure) return failure(command, error.code, error.message, error.extra);
    if (error instanceof BrowserGone) {
      this.tab = null;
      return failure(command, "browser-unreachable", error.message);
    }
    if (error instanceof CdpError) return failure(command, "cdp-error", error.message);
    return failure(command, "runner-error", error instanceof Error ? error.message : String(error));
  }

  private requireTab() {
    if (!this.tab?.alive) throw new RunnerFailure("no-page", "Open a page first");
    return this.tab;
  }

  private async execute(command: Command): Promise<RunnerResult> {
    switch (command.command) {
      case "close":
        await this.tab?.close();
        this.tab = null;
        this.secrets.clear();
        return { ok: true, command: "close" };
      case "open": {
        if (!this.tab?.alive) this.tab = await this.openTab();
        const tab = this.tab;
        const navigation = (await tab.send("Page.navigate", { url: command.url })) as { errorText?: string };
        if (navigation.errorText) throw new RunnerFailure("navigate-failed", `Could not open ${command.url}: ${navigation.errorText}`);
        await tab.send("LP.waitUntil", { until: "load" });
        return this.withSnapshot(tab, command.snapshot, { ok: true, command: "open", page: await this.pageInfo(tab) });
      }
      case "tree": {
        const tab = this.requireTab();
        const textParams = command.mode === "interactive" ? { format: "text", interactiveOnly: true } : { format: "text" };
        const { semanticTree: text } = (await tab.send("LP.getSemanticTree", textParams)) as { semanticTree: string };
        const root = await this.semanticTree(tab);
        const cells = [...new Set(tablesOf(root).flatMap((table) => table.rows.flat()).filter((cell) => cell !== ""))];
        return this.withSnapshot(tab, command.snapshot, {
          ok: true,
          command: "tree",
          page: await this.pageInfo(tab),
          mode: command.mode,
          text,
          elements: elementsOf(root),
          cells,
        });
      }
      case "find": {
        const tab = this.requireTab();
        const locator: Locator = { role: command.role, name: command.name, ...(command.form === undefined ? {} : { form: command.form }) };
        const element = await this.resolve(tab, locator);
        const { nodeDetails } = (await tab.send("LP.getNodeDetails", { backendNodeId: element.ref })) as { nodeDetails: { selector?: string } };
        return { ok: true, command: "find", page: await this.pageInfo(tab), element, selector: nodeDetails.selector ?? "" };
      }
      case "click": {
        const tab = this.requireTab();
        const element = await this.resolve(tab, command.target);
        const clickedOn = await this.documentOf(tab);
        await tab.send("LP.clickNode", { backendNodeId: element.ref });
        if (command.waitFor) {
          await this.waitAfterClick(tab, command.waitFor, command.timeoutMs, clickedOn);
          await tab.send("LP.waitUntil", { until: "load" });
        }
        return this.withSnapshot(tab, command.snapshot, { ok: true, command: "click", page: await this.pageInfo(tab), element: summary(element) });
      }
      case "fill": {
        const tab = this.requireTab();
        const element = await this.resolve(tab, command.target);
        await tab.send("LP.fillNode", { backendNodeId: element.ref, text: command.text });
        const { nodeDetails } = (await tab.send("LP.getNodeDetails", { backendNodeId: element.ref })) as { nodeDetails: { value?: string } };
        const masked = element.inputType === "password" && command.text !== "" && nodeDetails.value === "*****";
        if (nodeDetails.value !== command.text && !masked) {
          throw new RunnerFailure("fill-not-applied", `The ${element.role} "${element.name}" did not take the new value`);
        }
        return this.withSnapshot(tab, command.snapshot, { ok: true, command: "fill", page: await this.pageInfo(tab), element: summary(element) });
      }
      case "wait": {
        const tab = this.requireTab();
        const backendNodeId = await this.waitFor(tab, command.selector, command.timeoutMs);
        const { semanticTree } = (await tab.send("LP.getSemanticTree", { format: "text", backendNodeId })) as { semanticTree: string };
        return { ok: true, command: "wait", page: await this.pageInfo(tab), selector: command.selector, text: textOf(parseTextTree(semanticTree)) };
      }
      case "readTable":
        return this.readTable(this.requireTab(), command);
      case "snapshot": {
        const tab = this.requireTab();
        return { ok: true, command: "snapshot", page: await this.pageInfo(tab), snapshot: await this.snapshotOf(tab, command.maxBytes) };
      }
      case "script":
        throw new RunnerFailure("bad-command", "A saved script runs on its own with lightpanda run, not on the open page");
    }
  }

  private async readTable(tab: Tab, command: Extract<Command, { command: "readTable" }>): Promise<RunnerResult> {
    const index = command.table ?? 1;
    const maxPages = command.maxPages ?? defaultMaxPages;
    const rows: string[][] = [];
    let columns: string[] = [];
    let previous: string[][] | null = null;
    for (let pages = 1; ; pages += 1) {
      const root = await this.semanticTree(tab);
      const table = tablesOf(root)[index - 1];
      if (!table) throw new RunnerFailure("not-found", `No table ${index} on this page`, { count: 0 });
      if (previous && sameRows(previous, table.rows)) {
        throw new RunnerFailure("next-page-unchanged", `Page ${pages} showed the same rows as page ${pages - 1}`);
      }
      if (pages === 1) columns = table.columns;
      rows.push(...table.rows);
      previous = table.rows;
      const next = command.next ? locate(elementsOf(root), command.next) : ({ ok: false, count: 0 } as const);
      if (!next.ok && next.count === 0) return { ok: true, command: "readTable", page: await this.pageInfo(tab), columns, rows, pages };
      if (!next.ok) throw new RunnerFailure("not-unique", describeLocator(command.next!, next.count), { count: next.count });
      if (pages >= maxPages) throw new RunnerFailure("too-many-pages", `There are more than ${maxPages} pages`);
      await tab.send("LP.clickNode", { backendNodeId: next.element.ref });
      await this.waitFor(tab, command.waitFor ?? "table");
      await tab.send("LP.waitUntil", { until: "load" });
    }
  }

  private async semanticTree(tab: Tab) {
    return ((await tab.send("LP.getSemanticTree", {})) as { semanticTree: SemanticNode }).semanticTree;
  }

  private async resolve(tab: Tab, target: Target): Promise<PageElement> {
    const elements = elementsOf(await this.semanticTree(tab));
    if ("ref" in target) {
      const element = elements.find((candidate) => candidate.ref === target.ref);
      if (!element) throw new RunnerFailure("not-found", `No element ${target.ref} on this page`, { count: 0 });
      return element;
    }
    const located = locate(elements, target);
    if (located.ok) return located.element;
    throw new RunnerFailure(located.count === 0 ? "not-found" : "not-unique", describeLocator(target, located.count), { count: located.count });
  }

  private async waitFor(tab: Tab, selector: string, timeoutMs?: number) {
    try {
      const params = timeoutMs === undefined ? { selector } : { selector, timeout: timeoutMs };
      const found = (await tab.send("LP.waitForSelector", params, (timeoutMs ?? defaultWaitMs) + waitMarginMs)) as { backendNodeId: number };
      return found.backendNodeId;
    } catch (error) {
      if (error instanceof CdpError) throw new RunnerFailure("wait-failed", `Nothing matching ${selector} appeared`);
      throw error;
    }
  }

  private async documentOf(tab: Tab) {
    return ((await tab.send("LP.getSemanticTree", { maxDepth: 0 })) as { semanticTree: SemanticNode }).semanticTree.backendDOMNodeId;
  }

  private async reached(tab: Tab, until: "domcontentloaded" | "settled", timeout: number) {
    try {
      await tab.send("LP.waitUntil", { until, timeout }, timeout + waitMarginMs);
      return true;
    } catch (error) {
      if (error instanceof CdpError) return false;
      throw error;
    }
  }

  private async shown(tab: Tab, selector: string, timeout: number) {
    try {
      return ((await tab.send("LP.waitForSelector", { selector, timeout }, timeout + waitMarginMs)) as { backendNodeId: number }).backendNodeId;
    } catch (error) {
      if (error instanceof CdpError) return null;
      throw error;
    }
  }

  private async waitAfterClick(tab: Tab, selector: string, timeoutMs: number | undefined, clickedOn: number) {
    let deadline = performance.now() + (timeoutMs ?? defaultWaitMs);
    const left = () => Math.max(1, Math.ceil(deadline - performance.now()));
    const parsed = await this.reached(tab, "domcontentloaded", left());
    if (parsed && (await this.documentOf(tab)) === clickedOn) return this.waitFor(tab, selector, timeoutMs);
    if (timeoutMs === undefined) deadline = Math.min(deadline, performance.now() + loadedPageWaitMs);
    while (performance.now() < deadline) {
      const found = await this.shown(tab, selector, Math.min(lookAgainMs, left()));
      if (found !== null) return found;
      if (parsed && (await this.reached(tab, "settled", 1))) {
        const late = await this.shown(tab, selector, 1);
        if (late !== null) return late;
        break;
      }
    }
    throw new RunnerFailure("wait-failed", `Nothing matching ${selector} appeared`);
  }

  private async pageInfo(tab: Tab): Promise<PageInfo> {
    const { url, title } = await tab.location();
    const status = (await tab.send("LP.getPageStatus")) as { httpStatus?: number };
    return status.httpStatus === undefined ? { url, title } : { url, title, status: status.httpStatus };
  }

  private async snapshotOf(tab: Tab, maxBytes = snapshotMaxBytes): Promise<PageSnapshot> {
    const { content } = (await tab.send("LP.dump", { format: "html", strip: { js: true } })) as { content: string };
    const values = Object.fromEntries(
      elementsOf(await this.semanticTree(tab)).flatMap((element) =>
        element.tag === "input" && element.fieldName && element.value !== undefined && !typedValueTypes.has(element.inputType ?? "text")
          ? [[element.fieldName, element.value]]
          : [],
      ),
    );
    const { url, title } = await tab.location();
    return { url, host: new URL(url).host, title, ...sanitizeSnapshot(content, { maxBytes, values }) };
  }

  private async withSnapshot<T extends RunnerResult>(tab: Tab, wanted: boolean | undefined, result: T): Promise<T> {
    return wanted ? { ...result, snapshot: await this.snapshotOf(tab) } : result;
  }
}
