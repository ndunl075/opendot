import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "../src/App";
import { api, ApiError } from "../src/api/client";
import { ThemeProvider } from "../src/design/theme";

afterEach(() => vi.useRealTimers());
it("refreshes footer health on interval and focus, distinguishes auth from network, and cleans up", async () => {
  vi.useFakeTimers(); let failure: unknown;
  const call = vi.spyOn(api, "call").mockImplementation(async name => {
    if (name === "health") { if (failure) throw failure; return { status: "ok" } as never; }
    return { current_step: "done", completed_steps: ["done"] } as never;
  });
  const view = render(<ThemeProvider><MemoryRouter initialEntries={["/about"]}><App /></MemoryRouter></ThemeProvider>);
  await act(async () => {}); expect(screen.getByText("Daemon connected")).toBeVisible();
  failure = new Error("Network error"); await act(async () => { vi.advanceTimersByTime(30000); });
  expect(screen.getByText("Daemon unreachable")).toBeVisible();
  failure = new ApiError(401, "unauthorized", "Bad token");
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(screen.getByText("Daemon unauthorized — your session expired")).toBeVisible();
  failure = undefined; await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(screen.getByText("Daemon connected")).toBeVisible();
  view.unmount(); const count = call.mock.calls.length;
  await act(async () => { vi.advanceTimersByTime(60000); window.dispatchEvent(new Event("focus")); });
  expect(call).toHaveBeenCalledTimes(count);
});
