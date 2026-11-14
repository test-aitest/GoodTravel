const page = new Page();
const elements = {};
const tables = {};
const said = {};
let current = null;
let checks = 0;

const emit = (body) => console.log(JSON.stringify(body));
const quoted = (text) => JSON.stringify(text);
const fail = (code, detail) => {
  throw new Error(`check failed: ${code}: ${detail}`);
};
const check = (passed, code, detail) => {
  checks += 1;
  if (!passed) fail(code, detail());
};
const inPage = (fn, ...args) => JSON.parse(page.evaluate(`JSON.stringify((${fn.toString()})(...${JSON.stringify(args)}))`));

function locate(css, text) {
  const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const nameOf = (element) => clean(element.getAttribute("aria-label")) || clean(element.textContent) || clean(element.value);
  const pathOf = (element) => {
    const parts = [];
    for (let node = element; node && node.parentElement; node = node.parentElement) {
      let index = 1;
      for (let sibling = node.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
        if (sibling.tagName === node.tagName) index += 1;
      }
      parts.unshift(`${node.tagName.toLowerCase()}:nth-of-type(${index})`);
    }
    return `html > ${parts.join(" > ")}`;
  };
  const all = [...document.querySelectorAll(css)];
  const hits = text === null ? all : all.filter((element) => nameOf(element) === text);
  const selector = hits.length === 1 ? (all.length === 1 ? css : pathOf(hits[0])) : null;
  return { count: hits.length, all: all.length, names: all.slice(0, 3).map(nameOf), selector };
}

function readTable() {
  const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const table = document.querySelector("table");
  if (!table) return null;
  const cellText = (cell) => {
    const field = cell.querySelector("input, select, textarea");
    return clean(field ? field.value : cell.textContent);
  };
  const columns = [...table.querySelectorAll("thead th")].map(cellText);
  const rows = [...table.querySelectorAll("tbody tr")]
    .map((row) => [...row.children].filter((cell) => cell.tagName === "TD").map(cellText))
    .filter((cells) => cells.length > 0);
  return { columns, rows };
}

function valueAt(selector) {
  const field = document.querySelector(selector);
  return field ? String(field.value ?? "") : null;
}

function filledIn(selector) {
  const field = document.querySelector(selector);
  const value = field ? String(field.value ?? "") : "";
  return { filled: value !== "" && !value.startsWith("$" + "LP_") };
}

function textAt(selector) {
  const found = document.querySelector(selector);
  return found ? String(found.textContent ?? "").replace(/\s+/g, " ").trim() : null;
}

function pathname() {
  return location.pathname;
}

function snapshot() {
  const skipped = new Set(["password", "hidden", "checkbox", "radio", "file"]);
  const values = {};
  for (const field of document.querySelectorAll("input[name]")) {
    if (!skipped.has(String(field.getAttribute("type") ?? "text").toLowerCase())) values[field.getAttribute("name")] = String(field.value ?? "");
  }
  return { url: location.href, title: document.title, html: document.documentElement.outerHTML, values };
}

const describe = (target) => (target.text === null ? target.css : `${target.css} named ${quoted(target.text)}`);

const one = (target) => {
  const found = inPage(locate, target.css, target.text);
  checks += 1;
  if (found.count === 1) return found.selector;
  if (found.count === 0 && target.text !== null && found.all === 1) {
    fail("wrong-name", `${target.css} says ${quoted(found.names[0])}, expected ${quoted(target.text)}`);
  }
  fail(found.count === 0 ? "not-found" : "not-one", `${describe(target)} matched ${found.count} elements, expected 1`);
};

const readAll = (op) => {
  const rows = [];
  let columns = null;
  let previous = null;
  let pages = 0;
  for (;;) {
    const table = inPage(readTable);
    pages += 1;
    check(table !== null, "not-found", () => `page ${pages} has no table`);
    const shown = JSON.stringify(table.rows);
    if (previous !== null) check(shown !== previous, "same-rows", () => `page ${pages} showed the same rows as page ${pages - 1}`);
    previous = shown;
    columns ??= table.columns;
    rows.push(...table.rows);
    if (op.next === null) break;
    const next = inPage(locate, op.next.css, op.next.text);
    if (next.count === 0) break;
    check(next.count === 1, "not-one", () => `${describe(op.next)} matched ${next.count} elements, expected 1`);
    check(pages < plan.maxPages, "too-many-pages", () => `there are more than ${plan.maxPages} pages`);
    page.click(next.selector);
  }
  check(rows.length > 0, "no-rows", () => "the table has no rows");
  tables[op.into] = { columns, rows, pages };
  emit({ clientops: "table", stepId: current, into: op.into, ...tables[op.into] });
};

const run = {
  goto: async (op) => {
    await page.goto(op.url);
  },
  one: (op) => {
    elements[op.as] = one(op);
  },
  fill: (op) => {
    page.fill(elements[op.el], op.value);
    if (op.secret) {
      check(inPage(filledIn, elements[op.el]).filled, "not-taken", () => `${op.el} was not filled from the vault`);
    } else {
      check(inPage(valueAt, elements[op.el]) === op.value, "not-taken", () => `${op.el} did not take ${quoted(op.value)}`);
    }
  },
  click: (op) => {
    page.click(elements[op.el]);
  },
  path: (op) => {
    const path = inPage(pathname);
    check(path === op.is, op.code, () => `the page is ${path}, expected ${op.is}`);
  },
  table: (op) => readAll(op),
  page: () => emit({ clientops: "page", stepId: current, ...inPage(snapshot) }),
  fields: (op) => {
    const found = op.items.map((item) => ({ ...item, selector: one({ css: item.css, text: null }) }));
    for (const item of found) {
      const value = inPage(valueAt, item.selector);
      check(value === item.expect, "store-changed", () => `${item.sku} is ${quoted(value)} now, expected ${quoted(item.expect)}`);
    }
    for (const item of found) {
      page.fill(item.selector, item.value);
      const value = inPage(valueAt, item.selector);
      check(value === item.value, "not-taken", () => `${item.sku} is ${quoted(value)} after filling, expected ${quoted(item.value)}`);
    }
  },
  says: (op) => {
    const text = inPage(textAt, elements[op.el]);
    check(text !== null && new RegExp(op.pattern, "i").test(text), op.code, () => `${op.el} says ${quoted(text)}`);
    said[op.into] = text;
  },
};

for (const step of plan.steps) {
  current = step.stepId;
  checks = 0;
  const started = Date.now();
  emit({ clientops: "step", stepId: current, phase: "start" });
  for (const op of step.ops) await run[op.op](op);
  emit({ clientops: "step", stepId: current, phase: "done", tookMs: Date.now() - started, checks });
}

return { clientops: "result", said };
