import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import type { UsageSummary } from "../src/api/types.gen";
import UsageScreen from "../src/screens/usage/UsageScreen";

const usage: UsageSummary = { days: 7, total_credits: 8, today_credits: 2, plan_label: "ChatGPT Plus", manage_usage_url: "https://chatgpt.com/#settings/Usage", budgets: { task_credits: 5, daily_credits: 50, daily_hard_stop: true }, by_day: [{ day: "2026-09-30", credits: 2 }], by_task: [{ task_id: "task-1", title: "Morning brief", credits: 2 }], by_job_type: [{ job_type: "brief", credits: 2 }] };
beforeEach(() => {
  vi.spyOn(api, "call").mockImplementation(async (name, options) => {
    if (name === "usage_budgets_set") return options?.body as never;
    return usage as never;
  });
});
it("shows task, day and job totals and saves budgets with the daily hard stop intact", async () => {
  render(<UsageScreen />);
  expect(await screen.findByText("Morning brief")).toBeInTheDocument();
  expect(screen.getByText("2026-09-30")).toBeInTheDocument();
  expect(screen.getByText("brief")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Manage usage" })).toHaveAttribute("href", usage.manage_usage_url);
  fireEvent.change(screen.getByLabelText("Per-task budget (credits)"), { target: { value: "9" } });
  fireEvent.change(screen.getByLabelText("Daily budget (credits)"), { target: { value: "60" } });
  const daily = screen.getByLabelText("Daily budget (credits)"); daily.focus();
  fireEvent.click(screen.getByRole("button", { name: "Save budgets" }));
  await waitFor(() => expect(api.call).toHaveBeenCalledWith("usage_budgets_set", { body: { task_credits: 9, daily_credits: 60, daily_hard_stop: true } }));
  expect(await screen.findByText("Budgets saved.")).toBeInTheDocument();
  expect(screen.getByLabelText("Daily budget (credits)")).toBe(daily);
  expect(daily).toHaveFocus(); expect(daily).toHaveValue(60);
});
it.each(["javascript:alert(1)", "", "https://user:secret@example.test"])("does not render a Manage usage anchor for an invalid URL %s", async manage_usage_url => {
  vi.mocked(api.call).mockResolvedValue({ ...usage, manage_usage_url });
  render(<UsageScreen />); await screen.findByText("Morning brief");
  expect(screen.queryByRole("link", { name: "Manage usage" })).not.toBeInTheDocument();
  expect(screen.getByText(/usage link is unavailable/i)).toBeInTheDocument();
});
it("renders an unavailable state instead of zero usage on 501", async () => {
  vi.mocked(api.call).mockRejectedValue(new ApiError(501, "not_implemented", "Unavailable"));
  render(<UsageScreen />);
  expect(await screen.findByText("Not available in this version yet")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save budgets" })).not.toBeInTheDocument();
});
