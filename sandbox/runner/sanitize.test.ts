import { describe, expect, test } from "bun:test";
import { sanitizeSnapshot, snapshotMaxBytes } from "./sanitize";
import { recorded } from "./fixtures/load";

const page = (body: string, head = "") => `<!doctype html><html><head>${head}</head><body>${body}</body></html>`;

describe("sanitizeSnapshot", () => {
  test("removes inline and external scripts, including inside SVG", () => {
    const { html } = sanitizeSnapshot(
      page(
        `<p>Stock</p><script>steal()</script><script src="https://cdn.example/app.js"></script><svg><script>alert(1)</script></svg>`,
        `<script type="module" src="/main.js"></script>`,
      ),
    );
    expect(html).not.toContain("<script");
    expect(html).not.toContain("steal()");
    expect(html).not.toContain("cdn.example");
    expect(html).not.toContain("/main.js");
    expect(html).toContain("<p>Stock</p>");
  });

  test("removes every event handler attribute, whatever its case", () => {
    const { html } = sanitizeSnapshot(page(`<body onload="go()"><button onclick="pay()" class="b">Pay</button><img src="x.png" OnError="leak()"></body>`));
    expect(html.toLowerCase()).not.toMatch(/\son[a-z]+=/);
    expect(html).toContain(`<button class="b">Pay</button>`);
  });

  test("removes frames, plugins, noscript, base and refresh tags, and script-like links", () => {
    const { html } = sanitizeSnapshot(
      page(
        `<iframe src="https://evil.example"></iframe><frameset><frame src="a.html"></frameset><object data="x.swf"></object><embed src="x.swf"><noscript><img src="https://track.example/p.gif"></noscript><p>kept</p>`,
        `<base href="https://evil.example/"><meta http-equiv="refresh" content="0;url=https://evil.example"><link rel="modulepreload" href="/m.js"><link rel="preload" as="script" href="/p.js"><link rel="stylesheet" href="/site.css"><meta charset="utf-8">`,
      ),
    );
    for (const gone of ["<iframe", "<frame", "<object", "<embed", "<noscript", "track.example", "<base", "refresh", "/m.js", "/p.js"]) {
      expect(html).not.toContain(gone);
    }
    expect(html).toContain(`<link rel="stylesheet" href="/site.css">`);
    expect(html).toContain(`<meta charset="utf-8">`);
    expect(html).toContain("<p>kept</p>");
  });

  test("removes javascript, vbscript and non-image data URLs and srcdoc, keeping ordinary links", () => {
    const { html } = sanitizeSnapshot(
      page(
        `<a href=" JaVaScRiPt:alert(1)">x</a><a href="vbscript:msgbox">y</a><a href="data:text/html,<b>">z</a><img src="data:image/png;base64,AAAA"><form action="javascript:send()"><button formaction="javascript:go()">Go</button></form><div srcdoc="<b>"></div><a href="/stock?page=2">Next</a>`,
      ),
    );
    expect(html.toLowerCase()).not.toContain("javascript:");
    expect(html.toLowerCase()).not.toContain("vbscript:");
    expect(html).not.toContain("data:text/html");
    expect(html).not.toContain("srcdoc");
    expect(html).toContain(`<img src="data:image/png;base64,AAAA">`);
    expect(html).toContain(`<a href="/stock?page=2">Next</a>`);
  });

  test("sees through character references that spell out a javascript URL", () => {
    const { html } = sanitizeSnapshot(
      page(`<a href="&#106;avascript:alert(1)">a</a><a href="java&#x09;script:alert(2)">b</a><a href="javascript&colon;alert(3)">c</a><a href="&#X6A;avascript&#58;alert(4)">d</a>`),
    );
    expect(html).not.toContain("href");
    expect(html).toContain("<a>a</a><a>b</a><a>c</a><a>d</a>");
  });

  test("blanks password and hidden input values and keeps the others", () => {
    const { html } = sanitizeSnapshot(
      page(`<input type="password" name="password" value="hunter2"><input type="HIDDEN" name="csrf" value="tok-123"><input name="q" value="coffee">`),
    );
    expect(html).not.toContain("hunter2");
    expect(html).not.toContain("tok-123");
    expect(html).toContain(`<input name="q" value="coffee">`);
  });

  test("shows what the worker typed by writing the current values into the fields, never into password or hidden ones", () => {
    const { html } = sanitizeSnapshot(
      page(
        `<input class="qty" name="available-KC-ETH-200" value="18"><input name="note"><input type="password" name="password"><input type="hidden" name="csrf" value="t">`,
      ),
      { values: { "available-KC-ETH-200": "6", note: "restock", password: "typed-secret", csrf: "changed" } },
    );
    expect(html).toContain(`<input class="qty" name="available-KC-ETH-200" value="6">`);
    expect(html).toContain(`<input name="note" value="restock">`);
    expect(html).not.toContain("typed-secret");
    expect(html).not.toContain("changed");
  });

  test("keeps the recorded store pages intact apart from the password field", () => {
    const login = recorded("store-login").html!;
    const { html, truncated } = sanitizeSnapshot(login);
    expect(truncated).toBe(false);
    expect(html).toContain("<title>Sign in - Store Admin</title>");
    expect(html).toContain(`<input name="password" type="password" autocomplete="current-password" required>`);
    const inventory = sanitizeSnapshot(recorded("store-inventory").html!);
    expect(inventory.html.match(/<input class="qty"/g)).toHaveLength(42);
    expect(inventory.bytes).toBe(new TextEncoder().encode(inventory.html).length);
  });

  test("cuts a page that is too big at a tag boundary and says so", () => {
    const rows = Array.from({ length: 2000 }, (_, index) => `<tr><td>Row ${index}</td></tr>`).join("");
    const { html, bytes, truncated } = sanitizeSnapshot(page(`<table>${rows}</table>`), { maxBytes: 4096 });
    expect(truncated).toBe(true);
    expect(bytes).toBeLessThanOrEqual(4096);
    expect(html.endsWith(">")).toBe(true);
    expect(html).toContain("<td>Row 0</td>");
  });

  test("has a default cap of 256 KiB", () => {
    expect(snapshotMaxBytes).toBe(262_144);
  });
});
