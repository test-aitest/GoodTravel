export const snapshotMaxBytes = 262_144;

export type Snapshot = { html: string; bytes: number; truncated: boolean };

export type SnapshotOptions = { maxBytes?: number; values?: Readonly<Record<string, string>> };

const removedElements = ["script", "noscript", "iframe", "frame", "frameset", "object", "embed", "applet", "portal", "base"];
const urlAttributes = new Set(["href", "src", "action", "formaction", "xlink:href", "data", "poster", "background", "srcset", "ping"]);
const blankedInputTypes = new Set(["password", "hidden"]);

const namedReferences: Record<string, string> = { colon: ":", tab: "\t", newline: "\n" };

function character(codePoint: number) {
  return codePoint <= 0x10ffff ? String.fromCodePoint(codePoint) : "�";
}

function decodeReferences(value: string) {
  return value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);?/gi, (reference, body: string) => {
    const lower = body.toLowerCase();
    if (lower.startsWith("#x")) return character(Number.parseInt(lower.slice(2), 16));
    if (lower.startsWith("#")) return character(Number.parseInt(lower.slice(1), 10));
    return namedReferences[lower] ?? reference;
  });
}

function unsafeUrl(value: string) {
  const compact = [...decodeReferences(value)]
    .filter((letter) => letter.codePointAt(0)! > 0x20)
    .join("")
    .toLowerCase();
  if (compact.startsWith("javascript:") || compact.startsWith("vbscript:")) return true;
  return compact.startsWith("data:") && !compact.startsWith("data:image/");
}

function cleanAttributes(element: HTMLRewriterTypes.Element) {
  const names = [...element.attributes].map(([name]) => name);
  for (const name of names) {
    const lower = name.toLowerCase();
    const value = element.getAttribute(name) ?? "";
    if (lower.startsWith("on") || lower === "srcdoc" || (urlAttributes.has(lower) && unsafeUrl(value))) element.removeAttribute(name);
  }
}

function capped(html: string, maxBytes: number): Snapshot {
  const encoded = new TextEncoder().encode(html);
  if (encoded.length <= maxBytes) return { html, bytes: encoded.length, truncated: false };
  const prefix = new TextDecoder().decode(encoded.slice(0, maxBytes)).replace(/�+$/, "");
  const cut = prefix.slice(0, prefix.lastIndexOf(">") + 1);
  return { html: cut, bytes: new TextEncoder().encode(cut).length, truncated: true };
}

export function sanitizeSnapshot(html: string, options: SnapshotOptions = {}): Snapshot {
  const values = options.values ?? {};
  const rewriter = new HTMLRewriter()
    .on(removedElements.join(","), {
      element(element) {
        element.remove();
      },
    })
    .on("meta[http-equiv]", {
      element(element) {
        if ((element.getAttribute("http-equiv") ?? "").toLowerCase() === "refresh") element.remove();
      },
    })
    .on("link", {
      element(element) {
        const rel = (element.getAttribute("rel") ?? "").toLowerCase().split(/\s+/);
        if (!rel.includes("stylesheet")) element.remove();
      },
    })
    .on("*", { element: cleanAttributes })
    .on("input", {
      element(element) {
        const type = (element.getAttribute("type") ?? "text").toLowerCase();
        const name = element.getAttribute("name");
        if (blankedInputTypes.has(type)) {
          if (element.hasAttribute("value")) element.setAttribute("value", "");
        } else if (name !== null && Object.hasOwn(values, name)) {
          element.setAttribute("value", values[name]);
        }
      },
    });
  return capped(rewriter.transform(html), options.maxBytes ?? snapshotMaxBytes);
}
