import { CdpError, type CdpParams, type Tab } from "../cdp";
import type { SemanticNode } from "../tree";
import { recorded, type RecordedPage, type RecordedPageName } from "./load";

export type FakeTabOptions = {
  start?: RecordedPageName;
  linkTargets?: Record<number, RecordedPageName>;
  statusText?: string;
  failWaitFor?: boolean;
  ignoreFill?: boolean;
  duplicateRef?: number;
  present?: Partial<Record<RecordedPageName, string[]>>;
  loadMs?: number;
};

export class FakeTab implements Tab {
  readonly calls: { method: string; params: CdpParams }[] = [];
  alive = true;
  closed = false;
  private page: RecordedPage | null;
  private pageName: RecordedPageName | null;
  private documentId = 1;
  private readyAt = 0;
  private filled = new Map<number, string>();

  constructor(private readonly options: FakeTabOptions = {}) {
    this.page = options.start ? recorded(options.start) : null;
    this.pageName = options.start ?? null;
  }

  callsTo(method: string) {
    return this.calls.filter((call) => call.method === method).map((call) => call.params);
  }

  async location() {
    return this.page ? { url: this.page.observed.url, title: this.page.observed.title } : { url: "about:blank", title: "" };
  }

  async close() {
    this.closed = true;
    this.alive = false;
  }

  private tree(): SemanticNode {
    if (!this.page) return { backendDOMNodeId: 1, nodeName: "root", xpath: "", nodeType: 9, children: [] };
    const duplicate = this.options.duplicateRef;
    const withFills = (node: SemanticNode): SemanticNode => ({
      ...node,
      ...(this.filled.has(node.backendDOMNodeId) ? { value: this.filled.get(node.backendDOMNodeId) } : {}),
      ...(node.children
        ? {
            children: node.children.flatMap((child) =>
              child.backendDOMNodeId === duplicate ? [withFills(child), { ...withFills(child), backendDOMNodeId: child.backendDOMNodeId + 100_000 }] : [withFills(child)],
            ),
          }
        : {}),
    });
    return withFills(this.page.jsonTree);
  }

  private findNode(ref: number) {
    const search = (node: SemanticNode): SemanticNode | undefined =>
      node.backendDOMNodeId === ref ? node : (node.children ?? []).map(search).find((found) => found !== undefined);
    return search(this.tree());
  }

  private go(name: RecordedPageName) {
    this.page = recorded(name);
    this.pageName = name;
    this.documentId += 1;
    this.filled.clear();
  }

  private async loaded(timeout: number | undefined, method: string, message: string) {
    const left = this.readyAt - performance.now();
    if (left <= 0) return;
    if (timeout !== undefined && timeout < left) {
      await Bun.sleep(timeout);
      throw new CdpError(method, -31998, message);
    }
    await Bun.sleep(left);
  }

  private async waitForSelector(params: CdpParams) {
    const timeout = (params.timeout as number | undefined) ?? 5000;
    const started = performance.now();
    await this.loaded(timeout, "LP.waitForSelector", "InternalError");
    const shown = this.pageName ? (this.options.present?.[this.pageName] ?? []) : [];
    if (shown.includes(params.selector as string)) return { backendNodeId: 338 };
    await Bun.sleep(Math.max(0, timeout - (performance.now() - started)));
    throw new CdpError("LP.waitForSelector", -31998, "InternalError");
  }

  async send(method: string, params: CdpParams = {}): Promise<unknown> {
    this.calls.push({ method, params });
    switch (method) {
      case "Page.navigate": {
        const names: RecordedPageName[] = ["store-login", "store-inventory", "supplier-stock-1", "supplier-stock-2", "supplier-stock-3"];
        const name = names.find((candidate) => recorded(candidate).observed.url === params.url);
        if (!name) return { frameId: "FID-0000000001", loaderId: "LID-0000000002", errorText: "CouldntConnect" };
        this.go(name);
        return { frameId: "FID-0000000001", loaderId: "LID-0000000001" };
      }
      case "LP.waitUntil":
        await this.loaded(params.timeout as number | undefined, method, "Timeout");
        return {};
      case "LP.getPageStatus":
        return { url: this.page?.observed.url ?? "about:blank", httpStatus: 200, jsErrors: 0 };
      case "LP.getSemanticTree": {
        if (params.maxDepth === 0) return { semanticTree: { backendDOMNodeId: this.documentId, nodeName: "root", xpath: "", nodeType: 9, children: [] } };
        if (params.backendNodeId !== undefined) return { semanticTree: this.options.statusText ?? "" };
        if (params.format === "text") return { semanticTree: params.interactiveOnly ? this.page?.interactiveText : this.page?.text };
        return { semanticTree: this.tree() };
      }
      case "LP.getNodeDetails": {
        const node = this.findNode(params.backendNodeId as number);
        if (!node) throw new CdpError(method, -31998, "InvalidNodeId");
        const masked = node.attributes?.type === "password" && node.value ? "*****" : node.value;
        return {
          nodeDetails: {
            backendNodeId: node.backendDOMNodeId,
            tagName: node.nodeName,
            role: node.role,
            name: node.name,
            ...(masked === undefined ? {} : { value: masked }),
            selector: node.attributes?.name ? `${node.nodeName}[name="${node.attributes.name}"]` : node.nodeName,
          },
        };
      }
      case "LP.clickNode": {
        const ref = params.backendNodeId as number;
        if (!this.findNode(ref)) throw new CdpError(method, -31998, "InvalidNodeId");
        const target = this.options.linkTargets?.[ref];
        if (target) {
          this.go(target);
          this.readyAt = performance.now() + (this.options.loadMs ?? 0);
        }
        return [];
      }
      case "LP.fillNode": {
        const ref = params.backendNodeId as number;
        if (!this.findNode(ref)) throw new CdpError(method, -31998, "InvalidNodeId");
        if (!this.options.ignoreFill) this.filled.set(ref, params.text as string);
        return [];
      }
      case "LP.waitForSelector":
        if (this.options.failWaitFor) throw new CdpError(method, -31998, "InternalError");
        if (this.options.present) return this.waitForSelector(params);
        return { backendNodeId: 338 };
      case "LP.dump":
        return { format: "html", content: this.page?.html ?? "<!DOCTYPE html><html><head></head><body></body></html>" };
      default:
        throw new Error(`FakeTab does not answer ${method}`);
    }
  }
}
