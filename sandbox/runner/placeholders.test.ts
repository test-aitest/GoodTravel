import { describe, expect, test } from "bun:test";
import { hidden, placeholdersIn, resolvePlaceholders, scrub, secretsIn } from "./placeholders";

describe("placeholdersIn", () => {
  test("names every $LP_ placeholder once, in order", () => {
    expect(placeholdersIn("$LP_STORE_USERNAME")).toEqual(["LP_STORE_USERNAME"]);
    expect(placeholdersIn("$LP_A and $LP_B2 then $LP_A")).toEqual(["LP_A", "LP_B2"]);
  });

  test("finds nothing in plain text, in other variables or in a lone $LP_", () => {
    expect(placeholdersIn("6")).toEqual([]);
    expect(placeholdersIn("$HOME $STORE_USERNAME $LP_")).toEqual([]);
  });
});

describe("resolvePlaceholders", () => {
  test("puts the values from the environment in place and lists them as secrets", () => {
    const env = { LP_STORE_USERNAME: "demo-user", LP_STORE_PASSWORD: "demo-pass" };
    expect(resolvePlaceholders("$LP_STORE_USERNAME", env)).toEqual({
      ok: true,
      text: "demo-user",
      names: ["LP_STORE_USERNAME"],
      secrets: ["demo-user"],
    });
    expect(resolvePlaceholders("$LP_STORE_USERNAME:$LP_STORE_PASSWORD", env)).toMatchObject({ ok: true, text: "demo-user:demo-pass" });
  });

  test("leaves text without placeholders alone and adds no secrets", () => {
    expect(resolvePlaceholders("6", {})).toEqual({ ok: true, text: "6", names: [], secrets: [] });
  });

  test("refuses when any placeholder is unset or empty, naming each one and returning no text", () => {
    const resolved = resolvePlaceholders("$LP_STORE_USERNAME $LP_STORE_PASSWORD $LP_EMPTY", { LP_STORE_PASSWORD: "x", LP_EMPTY: "" });
    expect(resolved).toEqual({ ok: false, missing: ["LP_STORE_USERNAME", "LP_EMPTY"] });
  });

  test("does not read a value whose name only starts like a placeholder", () => {
    expect(resolvePlaceholders("$LP_STORE", { LP_STORE_USERNAME: "demo-user" })).toEqual({ ok: false, missing: ["LP_STORE"] });
  });
});

describe("secretsIn", () => {
  test("collects the non-empty LP_ values of an environment", () => {
    expect(secretsIn({ LP_A: "one", LP_B: "", PATH: "/usr/bin", HOME: "/home/daytona", LP_C: undefined })).toEqual(["one"]);
  });
});

describe("scrub", () => {
  test("replaces every secret in every string of a result, however deep", () => {
    const result = {
      tree: "11 [i] textbox 'Username' value='demo-user'",
      elements: [{ name: "Username", value: "demo-user" }, { name: "Note demo-user and demo-pass" }],
      count: 3,
      ok: true,
      missing: null,
    };
    expect(scrub(result, ["demo-user", "demo-pass"])).toEqual({
      tree: `11 [i] textbox 'Username' value='${hidden}'`,
      elements: [{ name: "Username", value: hidden }, { name: `Note ${hidden} and ${hidden}` }],
      count: 3,
      ok: true,
      missing: null,
    });
  });

  test("replaces a longer secret before a shorter one inside it", () => {
    expect(scrub("pass and password", ["pass", "password"])).toBe(`${hidden} and ${hidden}`);
  });

  test("ignores empty secrets and leaves the input untouched", () => {
    const input = { a: "abc" };
    expect(scrub(input, [""])).toEqual({ a: "abc" });
    scrub(input, ["abc"]);
    expect(input).toEqual({ a: "abc" });
  });
});
