import { describe, expect, test } from "bun:test";
import { leftAloneText } from "./jobView";

describe("leftAloneText", () => {
  test("says nothing when every product was matched", () => {
    expect(leftAloneText(undefined)).toBeNull();
    expect(leftAloneText({ onlyInStore: 0, onlyAtSupplier: 0 })).toBeNull();
  });

  test("names the counts in plain words, one sentence each", () => {
    expect(leftAloneText({ onlyInStore: 2, onlyAtSupplier: 0 })).toBe("2 products weren't on the supplier's list. They stay as they are.");
    expect(leftAloneText({ onlyInStore: 1, onlyAtSupplier: 0 })).toBe("1 product wasn't on the supplier's list. It stays as it is.");
    expect(leftAloneText({ onlyInStore: 0, onlyAtSupplier: 1 })).toBe("1 product is only at the supplier. It wasn't added to the store.");
    expect(leftAloneText({ onlyInStore: 1, onlyAtSupplier: 3 })).toBe(
      "1 product wasn't on the supplier's list. 3 products are only at the supplier. Both stay as they are.",
    );
  });
});
