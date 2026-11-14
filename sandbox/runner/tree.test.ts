import { describe, expect, test } from "bun:test";
import { catalogs } from "../sites/catalog";
import { elementsOf, parseTextTree, tablesOf, textOf, type SemanticNode } from "./tree";
import { recorded, recordedCall } from "./fixtures/load";

const inventory = recorded("store-inventory");
const login = recorded("store-login");
const supplierPages = [recorded("supplier-stock-1"), recorded("supplier-stock-2"), recorded("supplier-stock-3")];

describe("parseTextTree", () => {
  test("reads every line of the recorded interactive store tree with id, depth, role, name and value", () => {
    const nodes = parseTextTree(inventory.interactiveText!);
    expect(nodes[0]).toEqual({
      id: 22,
      depth: 0,
      interactive: false,
      role: "RootWebArea",
      name: "Inventory - Kumo Coffee - Store Admin",
      value: null,
      text: null,
    });
    const fields = nodes.filter((node) => node.role === "spinbutton");
    expect(fields).toHaveLength(42);
    expect(fields[0]).toMatchObject({ depth: 1, interactive: true, name: "Available for Ethiopia Guji 200g", value: "18" });
    expect(nodes.at(-1)).toMatchObject({ interactive: true, role: "button", name: "Save changes", value: null });
  });

  test("reads text lines and role-only lines of the recorded full login tree", () => {
    const nodes = parseTextTree(login.text!);
    expect(nodes.find((node) => node.role === "form")).toMatchObject({ depth: 1, name: null, text: null, interactive: false });
    expect(nodes.find((node) => node.text === "Sign in to manage your store.")).toMatchObject({ role: null, depth: 3 });
    expect(nodes.filter((node) => node.interactive).map((node) => [node.role, node.name])).toEqual([
      ["textbox", "Username"],
      ["textbox", "Password"],
      ["button", "Sign in"],
    ]);
  });

  test("keeps quotes inside names and values, which Lightpanda does not escape", () => {
    const [button, field] = parseTextTree(" 5 [i] button 'Today's \\\"best\\\" deal'\n 6 [i] textbox 'It's a 'name'' value='a'bc'\n");
    expect(button).toMatchObject({ role: "button", name: "Today's \\\"best\\\" deal", value: null });
    expect(field).toMatchObject({ role: "textbox", name: "It's a 'name'", value: "a'bc" });
  });

  test("reads the status message the store shows after saving, as recorded", () => {
    const status = recordedCall("store-save", "LP.getSemanticTree", { format: "text", backendNodeId: 338 }) as { semanticTree: string };
    expect(textOf(parseTextTree(status.semanticTree))).toBe("Saved. 1 product updated.");
  });

  test("ignores blank lines and rejects lines that are not tree lines", () => {
    expect(parseTextTree("\n\n")).toEqual([]);
    expect(() => parseTextTree("hello world")).toThrow("line 1");
  });
});

describe("elementsOf", () => {
  test("lists the 45 things a person can use on the store inventory page, each with the form it belongs to", () => {
    const elements = elementsOf(inventory.jsonTree);
    expect(elements).toHaveLength(45);
    expect(elements.filter((element) => element.role === "spinbutton")).toHaveLength(42);
    expect(elements.find((element) => element.name === "Sign out")).toMatchObject({ role: "button", form: "/logout" });
    expect(elements.find((element) => element.name === "Save changes")).toMatchObject({
      role: "button",
      form: "/inventory",
      tag: "button",
    });
    expect(elements.find((element) => element.name === "Available for Ethiopia Guji 200g")).toEqual({
      ref: 62,
      role: "spinbutton",
      name: "Available for Ethiopia Guji 200g",
      value: "18",
      form: "/inventory",
      tag: "input",
      fieldName: "available-KC-ETH-200",
      inputType: "number",
    });
  });

  test("marks elements outside any form with form null", () => {
    const pager = elementsOf(supplierPages[0].jsonTree).filter((element) => element.role === "link");
    expect(pager.map((element) => [element.name, element.form])).toEqual([
      ["2", null],
      ["3", null],
      ["Next", null],
    ]);
  });

  test("leaves out the hidden note on the supplier page because it is not something to use", () => {
    const elements = elementsOf(recorded("supplier-stock-hidden-note").jsonTree);
    expect(JSON.stringify(elements)).not.toContain("AI assistants");
    expect(elements.map((element) => element.name)).toEqual(["Sign out", "2", "3", "Next"]);
  });

  test("names a form without an action by where it sits", () => {
    const root: SemanticNode = {
      backendDOMNodeId: 1,
      nodeName: "root",
      xpath: "",
      nodeType: 9,
      children: [
        {
          backendDOMNodeId: 2,
          nodeName: "form",
          xpath: "/html[1]/body[1]/form[1]",
          nodeType: 1,
          role: "form",
          isInteractive: false,
          children: [
            { backendDOMNodeId: 3, nodeName: "button", xpath: "/html[1]/body[1]/form[1]/button[1]", nodeType: 1, role: "button", name: "Go", isInteractive: true },
          ],
        },
      ],
    };
    expect(elementsOf(root)[0].form).toBe("/html[1]/body[1]/form[1]");
  });
});

const cell = (row: number, column: number, text: string): SemanticNode => ({
  backendDOMNodeId: row * 10 + column,
  nodeName: "text",
  xpath: `/html[1]/body[1]/table[1]/tbody[1]/tr[${row}]/td[${column}]/text()[1]`,
  nodeType: 3,
  nodeValue: text,
});
const header = (column: number, name: string): SemanticNode => ({
  backendDOMNodeId: 100 + column,
  nodeName: "th",
  xpath: `/html[1]/body[1]/table[1]/thead[1]/tr[1]/th[${column}]`,
  nodeType: 1,
  role: "columnheader",
  name,
});

describe("tablesOf", () => {
  test("reads the first supplier page as 20 rows under 5 columns", () => {
    const [table] = tablesOf(supplierPages[0].jsonTree);
    expect(table.columns).toEqual(["SKU", "Product", "Pack", "Available", "Status"]);
    expect(table.rows).toHaveLength(20);
    expect(table.rows[0]).toEqual(["KC-ETH-200", "Ethiopia Guji 200g", "200 g bag", "6", "Low"]);
  });

  test("the three supplier pages together hold the 42 products with the supplier's numbers", () => {
    const rows = supplierPages.flatMap((page) => tablesOf(page.jsonTree)[0].rows);
    expect(rows).toHaveLength(42);
    expect(Object.fromEntries(rows.map((row) => [row[0], Number(row[3])]))).toEqual(catalogs.kumo.supplierAvailable);
  });

  test("reads the store inventory form as 42 rows, taking the number from each input", () => {
    const [table] = tablesOf(inventory.jsonTree);
    expect(table.columns).toEqual(["SKU", "Product", "Available"]);
    expect(table.rows).toHaveLength(42);
    expect(Object.fromEntries(table.rows.map((row) => [row[0], Number(row[2])]))).toEqual(catalogs.kumo.storeAvailable);
  });

  test("keeps an empty cell in its column instead of shifting the row", () => {
    const root: SemanticNode = {
      backendDOMNodeId: 1,
      nodeName: "root",
      xpath: "",
      nodeType: 9,
      children: [header(1, "SKU"), header(2, "Note"), header(3, "Available"), cell(1, 1, "A-1"), cell(1, 3, "4"), cell(2, 1, "A-2"), cell(2, 2, "new"), cell(2, 3, "0")],
    };
    expect(tablesOf(root)).toEqual([{ columns: ["SKU", "Note", "Available"], rows: [["A-1", "", "4"], ["A-2", "new", "0"]] }]);
  });

  test("finds no table on the sign-in page", () => {
    expect(tablesOf(login.jsonTree)).toEqual([]);
  });
});
