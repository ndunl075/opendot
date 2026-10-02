import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { App } from "../src/App";
import { api } from "../src/api/client";
import { ThemeProvider } from "../src/design/theme";

it("labels every rail link, keeps tooltips and accessible names, and marks the active page", async () => {
  vi.spyOn(api, "call").mockResolvedValue({ current_step: "done", completed_steps: ["done"], status: "ok" } as never);
  render(<ThemeProvider><MemoryRouter initialEntries={["/about"]}><App /></MemoryRouter></ThemeProvider>);
  await screen.findByText("Daemon connected");
  const navigation = screen.getByRole("navigation", { name: "Main navigation" });
  for (const label of ["Onboarding", "Chat", "Companion", "Activity", "Rules", "Memory", "Connections", "Usage", "Settings"]) {
    const link = within(navigation).getByRole("link", { name: label });
    expect(link).toHaveAttribute("aria-label", label);
    expect(link).toHaveAttribute("title", label);
    expect(within(link).getByText(label)).toBeVisible();
  }
  expect(screen.getByRole("link", { name: "About" })).toHaveAttribute("aria-current", "page");
  fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
  expect(navigation.parentElement).toHaveClass("is-open");
  fireEvent.keyDown(navigation, { key: "Escape" });
  expect(screen.getByRole("button", { name: "Open navigation" })).toHaveFocus();
});
