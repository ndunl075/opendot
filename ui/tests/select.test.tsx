import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { expect, it, vi } from "vitest";
import { Select, Dialog } from "../src/design/components";

const options = [{ value: "system", label: "System" }, { value: "light", label: "Light" }, { value: "locked", label: "Locked", disabled: true }, { value: "dark", label: "Dark" }];
function Example() {
  const [value, setValue] = useState("system");
  return <><Select label="Appearance" value={value} onChange={setValue} options={options} hint="Choose a theme" /><button>Outside</button></>;
}
function active() {
  return document.getElementById(screen.getByRole("combobox").getAttribute("aria-activedescendant")!);
}
it("labels the custom listbox, exposes selection, and selects with a pointer", () => {
  const { container } = render(<Example />);
  const trigger = screen.getByRole("combobox", { name: "Appearance" });
  expect(container.querySelector("select")).toBeNull();
  expect(trigger).toHaveAccessibleDescription("Choose a theme");
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(trigger);
  expect(trigger).toHaveAttribute("aria-controls", screen.getByRole("listbox", { name: "Appearance" }).id);
  expect(screen.getByRole("option", { name: "System" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("option", { name: "Dark" }));
  expect(trigger).toHaveTextContent("Dark");
  expect(trigger).toHaveFocus();
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});
it("supports arrows, Home/End, Enter/Space, and cancels with Escape without changing selection", () => {
  render(<Example />);
  const trigger = screen.getByRole("combobox");
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  expect(active()).toHaveTextContent("System");
  fireEvent.keyDown(trigger, { key: "ArrowDown" }); expect(active()).toHaveTextContent("Light");
  fireEvent.keyDown(trigger, { key: "ArrowDown" }); expect(active()).toHaveTextContent("Dark");
  fireEvent.keyDown(trigger, { key: "Home" }); expect(active()).toHaveTextContent("System");
  fireEvent.keyDown(trigger, { key: "End" }); expect(active()).toHaveTextContent("Dark");
  fireEvent.keyDown(trigger, { key: "ArrowUp" }); expect(active()).toHaveTextContent("Light");
  fireEvent.keyDown(trigger, { key: "Escape" });
  expect(trigger).toHaveTextContent("System");
  expect(trigger).toHaveFocus();
  fireEvent.keyDown(trigger, { key: " " });
  fireEvent.keyDown(trigger, { key: "End" });
  fireEvent.keyDown(trigger, { key: "Enter" });
  expect(trigger).toHaveTextContent("Dark");
  fireEvent.keyDown(trigger, { key: "Home" });
  fireEvent.keyDown(trigger, { key: " " });
  expect(trigger).toHaveTextContent("System");
});
it("supports multi-character and repeated-character typeahead and closes on outside pointer or blur", () => {
  render(<Select label="Fruit" options={[{ value: "a", label: "Apple" }, { value: "b", label: "Banana" }, { value: "c", label: "Blueberry" }]} />);
  const trigger = screen.getByRole("combobox");
  fireEvent.keyDown(trigger, { key: "b" }); expect(active()).toHaveTextContent("Banana");
  fireEvent.keyDown(trigger, { key: "b" }); expect(active()).toHaveTextContent("Blueberry");
  fireEvent.keyDown(trigger, { key: "l" }); expect(active()).toHaveTextContent("Blueberry");
  fireEvent.keyDown(trigger, { key: "Enter" }); expect(trigger).toHaveTextContent("Blueberry");
  fireEvent.click(trigger); fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  fireEvent.click(trigger); fireEvent.blur(trigger);
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});
it("commits the active option on Tab without preventing focus navigation", () => {
  render(<Example />);
  const trigger = screen.getByRole("combobox");
  fireEvent.keyDown(trigger, { key: "End" });
  expect(fireEvent.keyDown(trigger, { key: "Tab" })).toBe(true);
  expect(trigger).toHaveTextContent("Dark");
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});
it("respects disabled controls/options and exposes errors", () => {
  const change = vi.fn();
  const view = render(<Select label="Appearance" options={options} disabled onChange={change} error="Try again" />);
  expect(screen.getByRole("combobox")).toBeDisabled();
  expect(screen.getByRole("combobox")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByRole("combobox")).toHaveAccessibleDescription("Try again");
  fireEvent.click(screen.getByRole("combobox"));
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  view.rerender(<Select label="Appearance" options={options} onChange={change} />);
  fireEvent.click(screen.getByRole("combobox"));
  fireEvent.click(screen.getByRole("option", { name: "Locked" }));
  expect(change).not.toHaveBeenCalled();
});
it("consumes Escape inside a dialog before the dialog closes", () => {
  const close = vi.fn();
  render(<Dialog open title="Settings" onClose={close}><Example /></Dialog>);
  const trigger = screen.getByRole("combobox");
  fireEvent.click(trigger); fireEvent.keyDown(trigger, { key: "Escape" });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  expect(close).not.toHaveBeenCalled();
  fireEvent.keyDown(trigger, { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
});
