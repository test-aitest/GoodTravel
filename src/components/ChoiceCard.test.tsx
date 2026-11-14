import { afterEach, describe, expect, mock, test } from "bun:test";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ChoiceCard } from "./ChoiceCard";

afterEach(cleanup);

const candidates = [
  { ref: "1016", name: "Update inventory" },
  { ref: "1100", name: "Save changes to all locations" },
];

function show(onPick = mock((_ref: string | null) => {})) {
  render(<ChoiceCard question="Which button saves the changes now?" detail="It used Save changes before." candidates={candidates} confirmLabel="Use this button" onPick={onPick} />);
  return { onPick, card: screen.getByRole("group", { name: "Which button saves the changes now?" }) };
}

describe("ChoiceCard", () => {
  test("asks the question and lists each element by its name, with nothing picked yet", () => {
    const { card } = show();
    expect(within(card).getByText("It used Save changes before.")).toBeTruthy();
    const options = within(card).getAllByRole("radio");
    expect(options.map((option) => option.closest("[role=radiogroup]") !== null)).toEqual([true, true]);
    expect(within(card).getByText("Update inventory")).toBeTruthy();
    expect(within(card).getByText("Save changes to all locations")).toBeTruthy();
    expect(options.some((option) => (option as HTMLInputElement).checked)).toBe(false);
  });

  test("the one main button stays off until you pick, then sends the element you picked", () => {
    const { card, onPick } = show();
    const confirm = within(card).getByRole("button", { name: "Use this button" });
    expect(confirm.hasAttribute("disabled") || confirm.getAttribute("aria-disabled") === "true").toBe(true);
    fireEvent.click(within(card).getByText("Save changes to all locations"));
    fireEvent.click(within(card).getByRole("button", { name: "Use this button" }));
    expect(onPick.mock.calls).toEqual([["1100"]]);
  });

  test("None of these sends no element", () => {
    const { card, onPick } = show();
    fireEvent.click(within(card).getByRole("button", { name: "None of these" }));
    expect(onPick.mock.calls).toEqual([[null]]);
  });
});
