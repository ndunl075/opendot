import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { App } from "../src/App";
import { api } from "../src/api/client";
import { ThemeProvider } from "../src/design/theme";
import type { OnboardingState } from "../src/api/types.gen";

it.each(["/usage", "/settings", "/settings/general", "/settings/connections", "/chat"])("gates %s on the daemon's unfinished onboarding state", async path => {
  const state: OnboardingState = { current_step: "companion", completed_steps: [], chatgpt: { state: "signed_out", plan: "unknown", eligible: false, manage_usage_url: "https://chatgpt.com/settings/usage" } };
  vi.spyOn(api, "call").mockResolvedValue(state);
  render(<ThemeProvider><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></ThemeProvider>);
  expect(await screen.findByRole("heading", { name: "Welcome to OpenDot" })).toBeInTheDocument();
  expect(screen.getByLabelText("Companion name")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Usage" })).not.toBeInTheDocument();
});
