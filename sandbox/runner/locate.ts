import type { PageElement } from "./tree";

export type Locator = { role: string; name: string; form?: string | null };

export type Located = { ok: true; element: PageElement } | { ok: false; count: number };

const spaced = (text: string) => text.replace(/\s+/g, " ").trim();

export function locate(elements: readonly PageElement[], locator: Locator): Located {
  const name = spaced(locator.name);
  const matches = elements.filter(
    (element) =>
      element.role === locator.role &&
      spaced(element.name) === name &&
      (locator.form === undefined || element.form === locator.form),
  );
  return matches.length === 1 ? { ok: true, element: matches[0] } : { ok: false, count: matches.length };
}
