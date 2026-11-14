export class CdpError extends Error {
  constructor(
    readonly method: string,
    readonly code: number,
    readonly reason: string,
  ) {
    super(`${method} failed: ${reason} (${code})`);
  }
}

export class BrowserGone extends Error {}

export type CdpParams = Record<string, unknown>;

export interface Tab {
  send(method: string, params?: CdpParams, timeoutMs?: number): Promise<unknown>;
  location(): Promise<{ url: string; title: string }>;
  close(): Promise<void>;
  readonly alive: boolean;
}

type Pending = {
  method: string;
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

const defaultTimeoutMs = 30_000;

export class CdpConnection {
  private nextId = 0;
  private pending = new Map<number, Pending>();
  private closed = false;

  private constructor(private readonly socket: WebSocket) {
    socket.addEventListener("message", (event) => this.receive(String(event.data)));
    socket.addEventListener("close", () => this.shutDown(new BrowserGone("The browser closed the connection")));
  }

  static open(url: string, timeoutMs = 5_000): Promise<CdpConnection> {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(url);
      const timer = setTimeout(() => {
        socket.close();
        reject(new BrowserGone(`No browser answered at ${url} within ${timeoutMs} ms`));
      }, timeoutMs);
      socket.addEventListener("open", () => {
        clearTimeout(timer);
        resolve(new CdpConnection(socket));
      });
      socket.addEventListener("error", () => {
        clearTimeout(timer);
        reject(new BrowserGone(`No browser is serving CDP at ${url}`));
      });
    });
  }

  get open() {
    return !this.closed;
  }

  send(method: string, params: CdpParams = {}, sessionId?: string, timeoutMs = defaultTimeoutMs): Promise<unknown> {
    if (this.closed) return Promise.reject(new BrowserGone("The browser connection is closed"));
    this.nextId += 1;
    const id = this.nextId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new CdpError(method, -1, `no answer within ${timeoutMs} ms`));
      }, timeoutMs);
      this.pending.set(id, { method, resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }

  close() {
    if (!this.closed) this.socket.close();
    this.shutDown(new BrowserGone("The browser connection is closed"));
  }

  private receive(data: string) {
    const message = JSON.parse(data) as { id?: number; result?: unknown; error?: { code: number; message: string } };
    if (message.id === undefined) return;
    const waiting = this.pending.get(message.id);
    if (!waiting) return;
    this.pending.delete(message.id);
    clearTimeout(waiting.timer);
    if (message.error) waiting.reject(new CdpError(waiting.method, message.error.code, message.error.message));
    else waiting.resolve(message.result);
  }

  private shutDown(reason: Error) {
    this.closed = true;
    for (const waiting of this.pending.values()) {
      clearTimeout(waiting.timer);
      waiting.reject(reason);
    }
    this.pending.clear();
  }
}

export class BrowserTab implements Tab {
  private constructor(
    private readonly connection: CdpConnection,
    private readonly browserContextId: string,
    private readonly targetId: string,
    private readonly sessionId: string,
  ) {}

  static async open(cdpUrl: string): Promise<BrowserTab> {
    const connection = await CdpConnection.open(cdpUrl);
    const { browserContextId } = (await connection.send("Target.createBrowserContext")) as { browserContextId: string };
    const { targetId } = (await connection.send("Target.createTarget", { url: "about:blank", browserContextId })) as {
      targetId: string;
    };
    const { sessionId } = (await connection.send("Target.attachToTarget", { targetId, flatten: true })) as { sessionId: string };
    await connection.send("Page.enable", {}, sessionId);
    return new BrowserTab(connection, browserContextId, targetId, sessionId);
  }

  get alive() {
    return this.connection.open;
  }

  send(method: string, params: CdpParams = {}, timeoutMs?: number) {
    return this.connection.send(method, params, this.sessionId, timeoutMs);
  }

  async location() {
    const { targetInfo } = (await this.connection.send("Target.getTargetInfo", { targetId: this.targetId })) as {
      targetInfo: { url: string; title: string };
    };
    return { url: targetInfo.url, title: targetInfo.title };
  }

  async close() {
    if (this.connection.open) {
      await this.connection.send("Target.closeTarget", { targetId: this.targetId }).catch(() => undefined);
      await this.connection.send("Target.disposeBrowserContext", { browserContextId: this.browserContextId }).catch(() => undefined);
    }
    this.connection.close();
  }
}
