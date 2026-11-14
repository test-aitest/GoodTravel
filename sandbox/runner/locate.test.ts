import { describe, expect, test } from "bun:test";
import { locate } from "./locate";
import { elementsOf, type PageElement } from "./tree";
import { recorded } from "./fixtures/load";

const inventory = elementsOf(recorded("store-inventory").jsonTree);
const decoy = elementsOf(recorded("store-inventory-decoy").jsonTree);

const button = (ref: number, name: string, form: string | null): PageElement => ({ ref, role: "button", name, form, tag: "button" });

describe("locate", () => {
  test("returns the one element with that role, name and form", () => {
    expect(locate(inventory, { role: "button", name: "Save changes", form: "/inventory" })).toEqual({
      ok: true,
      element: { ref: 353, role: "button", name: "Save changes", form: "/inventory", tag: "button", fieldName: "action" },
    });
  });

  test("tells the save button apart from the look-alike that saves to all locations", () => {
    const found = locate(decoy, { role: "button", name: "Save changes", form: "/inventory" });
    expect(found.ok && found.element.ref).toBe(687);
    expect(decoy.filter((element) => element.role === "button" && element.form === "/inventory")).toHaveLength(2);
  });

  test("finds one field among the 42 by its name", () => {
    const found = locate(inventory, { role: "spinbutton", name: "Available for Kenya Nyeri 200g", form: "/inventory" });
    expect(found.ok && found.element).toMatchObject({ ref: 104, value: "9", fieldName: "available-KC-KEN-200" });
  });

  test("reports zero candidates when the button was renamed", () => {
    const renamed = inventory.map((element) => (element.name === "Save changes" ? { ...element, name: "Update inventory" } : element));
    expect(locate(renamed, { role: "button", name: "Save changes", form: "/inventory" })).toEqual({ ok: false, count: 0 });
  });

  test("reports how many matched when more than one does, and returns none of them", () => {
    const page = [button(1, "Sign out", "/logout"), button(2, "Sign out", "/logout")];
    expect(locate(page, { role: "button", name: "Sign out", form: "/logout" })).toEqual({ ok: false, count: 2 });
  });

  test("the form narrows the match: left out means any form, null means outside every form", () => {
    const page = [button(1, "Delete", "/orders/7"), button(2, "Delete", "/customers/3"), button(3, "Delete", null)];
    expect(locate(page, { role: "button", name: "Delete" })).toEqual({ ok: false, count: 3 });
    expect(locate(page, { role: "button", name: "Delete", form: "/customers/3" })).toMatchObject({ ok: true, element: { ref: 2 } });
    expect(locate(page, { role: "button", name: "Delete", form: null })).toMatchObject({ ok: true, element: { ref: 3 } });
  });

  test("matches the name exactly, apart from runs of spaces", () => {
    expect(locate(inventory, { role: "button", name: "save changes", form: "/inventory" })).toEqual({ ok: false, count: 0 });
    expect(locate(inventory, { role: "button", name: "Save", form: "/inventory" })).toEqual({ ok: false, count: 0 });
    expect(locate(inventory, { role: "button", name: "  Save   changes ", form: "/inventory" }).ok).toBe(true);
  });

  test("the role has to match too", () => {
    expect(locate(inventory, { role: "link", name: "Save changes" })).toEqual({ ok: false, count: 0 });
  });
});
