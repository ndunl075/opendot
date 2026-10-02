import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { expect, it } from "vitest";
import { AvatarPicker } from "../src/design/AvatarPicker";

function Picker({ disabled = false }: { disabled?: boolean }) {
  const [seed, setSeed] = useState("v2:c=slate;h=none;p=none");
  return <><AvatarPicker seed={seed} onChange={setSeed} disabled={disabled} /><output>{seed}</output></>;
}

it("provides named radio groups, one tab stop per row, and independent choices", () => {
  render(<Picker />);
  for (const [name, count] of [["Colors", 12], ["Characters", 11], ["Pets", 9]] as const) {
    const radios = within(screen.getByRole("radiogroup", { name })).getAllByRole("radio");
    expect(radios).toHaveLength(count);
    expect(radios.filter(radio => radio.tabIndex === 0)).toHaveLength(1);
  }
  fireEvent.click(screen.getByRole("radio", { name: "Color: Jade" }));
  fireEvent.click(screen.getByRole("radio", { name: "Character: Mint gumdrop" }));
  fireEvent.click(screen.getByRole("radio", { name: "Pet: Fox" }));
  expect(screen.getByRole("status")).toHaveTextContent("v2:c=jade;h=gumdrop;p=fox");
});

it("arrows select and focus, wrap at either end, and support Home and End", () => {
  render(<Picker />);
  const slate = screen.getByRole("radio", { name: "Color: Slate" });
  slate.focus();
  fireEvent.keyDown(slate, { key: "ArrowRight" });
  const sky = screen.getByRole("radio", { name: "Color: Sky" });
  expect(sky).toHaveFocus(); expect(sky).toHaveAttribute("aria-checked", "true");
  expect(slate).toHaveAttribute("tabindex", "-1");
  fireEvent.keyDown(sky, { key: "Home" }); expect(slate).toHaveFocus();
  fireEvent.keyDown(slate, { key: "ArrowLeft" });
  const sand = screen.getByRole("radio", { name: "Color: Sand" });
  expect(sand).toHaveFocus();
  fireEvent.keyDown(sand, { key: "ArrowDown" }); expect(slate).toHaveFocus();
  fireEvent.keyDown(slate, { key: "End" }); expect(sand).toHaveFocus();
  fireEvent.keyDown(sand, { key: "ArrowUp" }); expect(screen.getByRole("radio", { name: "Color: Teal" })).toHaveFocus();
});

it("keeps a legacy avatar until a choice is made and disables controls while saving", () => {
  const { rerender } = render(<AvatarPicker seed="old-ribbon" onChange={() => { throw new Error("Must not change"); }} disabled />);
  for (const radio of screen.getAllByRole("radio")) expect(radio).toBeDisabled();
  fireEvent.keyDown(screen.getByRole("radio", { name: "Color: Slate" }), { key: "ArrowRight" });
  rerender(<Picker disabled />);
  expect(screen.getByRole("status")).toHaveTextContent("v2:c=slate;h=none;p=none");
});
