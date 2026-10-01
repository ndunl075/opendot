import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../src/api/client";
import { ErrorState } from "../src/design/components";

afterEach(() => { delete window.__OPENDOT__; });
it("offers web login for an expired session, without retrying", () => {
  render(<ErrorState error={new ApiError(401, "unauthorized", "Bad token")} onRetry={vi.fn()} />);
  expect(screen.getByText("Your session expired")).toBeVisible();
  expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
it("asks desktop users to restart after socket authentication fails", () => {
  window.__OPENDOT__ = {};
  render(<ErrorState error={new ApiError(1008, "unauthorized", "Bad token")} />);
  expect(screen.getByText("Your session expired")).toBeVisible();
  expect(screen.getByText("Restart OpenDot")).toBeVisible(); expect(screen.queryByRole("link")).not.toBeInTheDocument();
});
it.each([404, 409, 410, 422])("shows the server message for %s without a retry", status => {
  render(<ErrorState error={new ApiError(status, "item_changed", "This action is no longer available.")} onRetry={vi.fn()} />);
  expect(screen.getByText("This action is no longer available.")).toBeVisible(); expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
it("allows a manual retry for a transient failure", () => {
  const retry = vi.fn(); render(<ErrorState error={new Error("Offline")} onRetry={retry} />);
  fireEvent.click(screen.getByRole("button", { name: "Try again" })); expect(retry).toHaveBeenCalledOnce();
});
