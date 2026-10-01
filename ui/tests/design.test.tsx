import { act, fireEvent, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { Avatar, createAvatarSeed } from "../src/design/avatar";
import { ThemeProvider, useTheme } from "../src/design/theme";
import { Button, ConfirmDialog, Dialog, ErrorState, Input, Tabs, Tooltip, UsageStamp } from "../src/design/components";
import { ApiError } from "../src/api/client";

beforeEach(() => { localStorage.clear(); document.documentElement.removeAttribute("data-theme"); });

it("draws the same accessible avatar from the same seed, and a different picture for a new seed", () => {
  const draw = (seed: string) => renderToStaticMarkup(<Avatar seed={seed} label="My companion" />);
  expect(draw("river")).toBe(draw("river"));
  expect(draw("river")).not.toBe(draw("stone"));
  expect(draw("river")).toContain('role="img"');
  expect(draw("river")).toContain('aria-label="My companion"');
  expect(createAvatarSeed()).not.toBe(createAvatarSeed());
});

function ThemeControls() {
  const { preference, theme, setTheme } = useTheme();
  return <><output>{preference}:{theme}</output>{(["light", "dark", "system"] as const).map(value =>
    <button key={value} onClick={() => setTheme(value)}>{value}</button>)}</>;
}

describe("themes", () => {
  it("follows the OS, saves an override, restores it, and can return to system", () => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const view = render(<ThemeProvider><ThemeControls /></ThemeProvider>);
    expect(screen.getByText("system:light")).toBeInTheDocument();
    act(() => { Object.defineProperty(media, "matches", { value: true, configurable: true }); media.dispatchEvent(new Event("change")); });
    expect(document.documentElement.dataset.theme).toBe("dark");
    fireEvent.click(screen.getByText("light", { selector: "button" }));
    expect(localStorage.getItem("opendot-theme")).toBe("light");
    view.unmount();
    render(<ThemeProvider><ThemeControls /></ThemeProvider>);
    expect(screen.getByText("light:light")).toBeInTheDocument();
    fireEvent.click(screen.getByText("system", { selector: "button" }));
    expect(screen.getByText("system:dark")).toBeInTheDocument();
    expect(localStorage.getItem("opendot-theme")).toBeNull();
  });

  it("ignores invalid persisted preferences and works when storage is blocked", () => {
    localStorage.setItem("opendot-theme", "invalid");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Blocked"); });
    render(<ThemeProvider><ThemeControls /></ThemeProvider>);
    fireEvent.click(screen.getByText("dark", { selector: "button" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});

it("explains not_implemented without offering a futile retry", () => {
  render(<ErrorState error={new ApiError(501, "not_implemented", "Unavailable")} onRetry={vi.fn()} />);
  expect(screen.getByRole("heading", { name: "Not available in this version yet" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
});

it("allows retry for other errors", () => {
  const retry = vi.fn();
  render(<ErrorState error={new ApiError(503, "unavailable", "Unavailable")} onRetry={retry} />);
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(retry).toHaveBeenCalledOnce();
});

it("traps focus in both directions, ignores disabled controls, closes with Escape and restores focus", () => {
  function Example() {
    const [open, setOpen] = useState(false);
    return <><Button onClick={() => setOpen(true)}>Open dialog</Button><Dialog open={open} onClose={() => setOpen(false)} title="Review action">
      <Input label="Name" /><Button disabled>Unavailable</Button><Button>Last action</Button>
    </Dialog></>;
  }
  render(<Example />);
  const opener = screen.getByRole("button", { name: "Open dialog" });
  opener.focus(); fireEvent.click(opener);
  const first = screen.getByRole("button", { name: "Close dialog" });
  const last = screen.getByRole("button", { name: "Last action" });
  expect(first).toHaveFocus();
  fireEvent.keyDown(first, { key: "Tab", shiftKey: true }); expect(last).toHaveFocus();
  fireEvent.keyDown(last, { key: "Tab" }); expect(first).toHaveFocus();
  fireEvent.keyDown(first, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(opener).toHaveFocus();
});

it("starts confirmation on the safe choice and only confirms on explicit action", () => {
  const confirm = vi.fn();
  render(<ConfirmDialog open title="Forget memory?" description="This removes the selected memory." confirmLabel="Forget" onClose={vi.fn()} onConfirm={confirm} danger />);
  expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Forget" }));
  expect(confirm).toHaveBeenCalledOnce();
});

it("supports keyboard tabs, with linked panels and disabled items skipped", () => {
  function Example() {
    const [value, setValue] = useState("one");
    return <Tabs label="Tasks" value={value} onValueChange={setValue} items={[
      { value: "one", label: "In progress", content: "Working" },
      { value: "two", label: "Scheduled", content: "Later", disabled: true },
      { value: "three", label: "Completed", content: "Finished" },
    ]} />;
  }
  render(<Example />);
  fireEvent.keyDown(screen.getByRole("tab", { name: "In progress" }), { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "Completed" })).toHaveFocus();
  expect(screen.getByRole("tabpanel")).toHaveTextContent("Finished");
  fireEvent.keyDown(screen.getByRole("tab", { name: "Completed" }), { key: "Home" });
  expect(screen.getByRole("tab", { name: "In progress" })).toHaveFocus();
});

it("exposes and dismisses a tooltip from the keyboard", () => {
  render(<Tooltip content="Only you can approve"><Button>Approval help</Button></Tooltip>);
  fireEvent.focus(screen.getByRole("button"));
  expect(screen.getByRole("tooltip")).toHaveTextContent("Only you can approve");
  fireEvent.mouseEnter(screen.getByRole("button"));
  fireEvent.mouseLeave(screen.getByRole("button"));
  expect(screen.getByRole("tooltip")).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("button"), { key: "Escape" });
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
});

it("does not report unknown credits as zero", () => {
  render(<UsageStamp model="Test model" effort="low" credits={null} />);
  expect(screen.getByText(/credits not reported/i)).toBeInTheDocument();
});

it("renders the exact non-affiliation text on About", () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response('{"status":"ok"}'));
  render(<ThemeProvider><MemoryRouter initialEntries={["/about"]}><App /></MemoryRouter></ThemeProvider>);
  expect(screen.getByText("OpenDot is an independent open-source project. It is not affiliated with, endorsed by, or sponsored by OpenAI or Anthropic. ChatGPT is a trademark of OpenAI.")).toBeInTheDocument();
});
