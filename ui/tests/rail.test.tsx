import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { App } from "../src/App";
import { api } from "../src/api/client";
import { ThemeProvider } from "../src/design/theme";

it("labels every rail link, keeps tooltips and accessible names, and marks the active page", async () => {
  vi.spyOn(api, "call").mockResolvedValue({ current_step: "done", completed_steps: ["done"], status: "ok" } as never);
  render(<ThemeProvider><MemoryRouter initialEntries={["/about"]}><App /></MemoryRouter></ThemeProvider>);
  await act(async () => {});
  expect(screen.queryByText("Daemon connected")).not.toBeInTheDocument();
  const navigation = screen.getByRole("navigation", { name: "Main navigation" });
  expect(within(navigation).getAllByRole("link")).toHaveLength(2);
  for (const label of ["Chat", "Settings"]) {
    const link = within(navigation).getByRole("link", { name: label });
    expect(link).toHaveAttribute("aria-label", label);
    expect(link).toHaveAttribute("title", label);
    expect(within(link).getByText(label)).toBeVisible();
  }
  expect(within(navigation).getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
  expect(screen.queryByRole("link", { name: "Onboarding" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Local workspace settings" })).toHaveTextContent("OD");
  fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
  expect(navigation.parentElement).toHaveClass("is-open");
  fireEvent.keyDown(navigation, { key: "Escape" });
  expect(screen.getByRole("button", { name: "Open navigation" })).toHaveFocus();
});
