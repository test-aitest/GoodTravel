function readOptions(listName, linkPrefix) {
  const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const nameOf = (element) => clean(element.getAttribute("aria-label")) || clean(element.textContent);
  const list = [...document.querySelectorAll("ul, ol, [role=list]")].find((element) => clean(element.getAttribute("aria-label")) === listName);
  if (!list) return null;
  return [...list.querySelectorAll("li")].map((item) => {
    const link = [...item.querySelectorAll("a[href]")].find((anchor) => nameOf(anchor).startsWith(linkPrefix));
    const words = (node) => (node.nodeType === 3 ? String(node.nodeValue ?? "") : [...node.childNodes].map(words).join(" "));
    return [link ? nameOf(link).slice(linkPrefix.length) : "", clean(words(item)), link ? String(link.getAttribute("href")) : ""];
  });
}

function readSeats() {
  const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const nameOf = (element) => clean(element.getAttribute("aria-label")) || clean(element.textContent);
  const heading = document.querySelector("h1");
  const seats = [...document.querySelectorAll("button")].map(nameOf).filter((name) => /^Seat \d{1,2}[A-F], /.test(name));
  return { heading: heading ? clean(heading.textContent) : null, seats };
}

const tripOps = {
  options: (op) => {
    const rows = inPage(readOptions, op.list, op.link);
    check(rows !== null, "not-found", () => `there is no list named ${quoted(op.list)}`);
    check(rows.length > 0 && rows.every((row) => row[0] !== "" && row[2] !== ""), "no-rows", () => `the list ${quoted(op.list)} has no rides to choose seats for`);
    tables[op.into] = { columns: ["name", "text", "href"], rows, pages: 1 };
    emit({ clientops: "table", stepId: current, into: op.into, ...tables[op.into] });
  },
  seatMaps: async (op) => {
    const rows = [];
    for (const [name, , href] of tables[op.from].rows) {
      await page.goto(href.startsWith("/") ? op.base + href : href);
      const found = inPage(readSeats);
      check(found.heading === op.heading + name, "wrong-page", () => `the seat page says ${quoted(found.heading)}, expected ${quoted(op.heading + name)}`);
      check(found.seats.length > 0, "no-rows", () => `${name} shows no seats`);
      rows.push(...found.seats.map((seat) => [href, seat]));
    }
    tables[op.into] = { columns: ["href", "name"], rows, pages: tables[op.from].rows.length };
    emit({ clientops: "table", stepId: current, into: op.into, ...tables[op.into] });
  },
};
