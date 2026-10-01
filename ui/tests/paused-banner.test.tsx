import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api, createApi, ApiError } from "../src/api/client";
import type { PausedEvent } from "../src/api/types.gen";
import { PausedBanner } from "../src/screens/chat/PausedBanner";

const base = { type: "paused", conversation_id: "conversation", message_id: "message", seq: 2, message: "Paused by daemon", task_id: "task/a" } as const;
const replay = vi.hoisted(() => ({ event: undefined as PausedEvent | undefined, confirm: true }));
vi.mock("../src/api/stream", () => ({ ChatStream: class {
  constructor(_id: string, private receive: (event: PausedEvent) => void) {}
  connect() { if (replay.confirm && replay.event?.task_id) this.receive(replay.event); }
  close() {}
} }));
const cases = [
  { reason: "task_budget", label: "Continue anyway", path: "/v1/tasks/task%2Fa/continue", warning: /may use more credits than the task budget/i, result: { task_id: "task/a", state: "queued", message: "Budget override accepted." }, shown: "Budget override accepted." },
  { reason: "top_tier_approval", label: "Allow top-tier model", path: "/v1/tasks/task%2Fa/approve-top-tier", warning: /uses more of your ChatGPT plan/i, result: { task_id: "task/a", state: "queued", message: "Top-tier permission saved." }, shown: "Top-tier permission saved." },
  { reason: "rate_limited", label: "I've raised my limit, resume", path: "/v1/companion/resume-plan-limit", warning: /limit has reset/i, result: { resumed_task_ids: ["task/a", "task/b"] }, shown: "Plan-limit pause cleared. 2 tasks resumed." },
] as const;
function banner(reason: PausedEvent["reason"], task_id: string | null = base.task_id) {
  replay.event = { ...base, reason, task_id };
  return <MemoryRouter><PausedBanner event={{ ...base, reason, task_id }} manageUsageUrl="https://chatgpt.com/settings/usage" /></MemoryRouter>;
}

describe("explicit pause actions", () => {
  it.each(cases)("$reason posts once, disables pending requests, and shows the response", async ({ reason, label, path, warning, result, shown }) => {
    let resolve!: (response: Response) => void;
    const fetchMock = vi.fn(() => new Promise<Response>(done => { resolve = done; }));
    vi.spyOn(api, "call").mockImplementation(createApi(() => undefined, fetchMock).call);
    render(banner(reason));
    expect(screen.getByText(warning)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    const button = screen.getByRole("button", { name: label });
    fireEvent.click(button);
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(path, expect.objectContaining({ method: "POST" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    await act(async () => resolve(new Response(JSON.stringify(result), { status: 200 })));
    expect(screen.getByRole("status")).toHaveTextContent(shown);
    expect(button).toBeDisabled();
    if (reason === "rate_limited") expect(screen.getByRole("link", { name: "Manage usage" })).toHaveAttribute("href", "https://chatgpt.com/settings/usage");
  });

  it.each(cases)("$reason reports failure without automatically retrying", async ({ reason, label }) => {
    const call = vi.spyOn(api, "call").mockRejectedValue(new Error("Connection lost"));
    const view = render(banner(reason));
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(await screen.findByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: label })).toBeEnabled();
    view.rerender(banner(reason));
    expect(call).toHaveBeenCalledTimes(1);
  });

  it.each(cases.slice(0, 2))("$reason cannot act without a task ID", ({ reason, label }) => {
    const call = vi.spyOn(api, "call");
    render(banner(reason, null));
    expect(screen.queryByRole("button", { name: label })).not.toBeInTheDocument();
    expect(screen.getByText(/task ID was not supplied/i)).toBeInTheDocument();
    expect(call).not.toHaveBeenCalled();
  });

  it("reports zero resumed tasks without claiming work resumed", async () => {
    vi.spyOn(api, "call").mockResolvedValue({ resumed_task_ids: [] });
    render(banner("rate_limited"));
    fireEvent.click(screen.getByRole("button", { name: "I've raised my limit, resume" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Plan-limit pause cleared. No tasks were resumed.");
  });

  it("uses task state when the daemon provides no result message", async () => {
    vi.spyOn(api, "call").mockResolvedValue({ task_id: "task/a", state: "queued" });
    render(banner("task_budget"));
    fireEvent.click(screen.getByRole("button", { name: "Continue anyway" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Task task/a: queued.");
  });

  it("requires a new decision for a new pause and ignores a late result from the old one", async () => {
    let resolve!: (value: { task_id: string; state: string; message: string }) => void;
    const call = vi.spyOn(api, "call").mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    const view = render(banner("task_budget"));
    fireEvent.click(screen.getByRole("button", { name: "Continue anyway" }));
    view.rerender(banner("top_tier_approval", "task/b"));
    expect(screen.getByRole("button", { name: "Allow top-tier model" })).toBeEnabled();
    await act(async () => resolve({ task_id: "task/a", state: "queued", message: "Old result" }));
    expect(screen.queryByText("Old result")).not.toBeInTheDocument();
    expect(call).toHaveBeenCalledTimes(1);
    call.mockResolvedValue({ task_id: "task/b", state: "queued", message: "New result" });
    fireEvent.click(screen.getByRole("button", { name: "Allow top-tier model" }));
    expect(await screen.findByRole("status")).toHaveTextContent("New result");
    expect(call).toHaveBeenLastCalledWith("task_approve_top_tier", { params: { task_id: "task/b" } });
  });
});

it("hides actions when a fresh replay cannot confirm that the task is paused", () => {
  replay.confirm = false;
  render(banner("task_budget"));
  expect(screen.queryByRole("button", { name: "Continue anyway" })).not.toBeInTheDocument();
  expect(screen.getByText(/could not confirm/i)).toBeInTheDocument();
  replay.confirm = true;
});

it("makes Manage usage primary and resume secondary", () => {
  render(banner("rate_limited"));
  expect(screen.getByRole("link", { name: "Manage usage" })).toHaveClass("button--primary");
  expect(screen.getByRole("button", { name: "I've raised my limit, resume" })).toHaveClass("button--secondary");
});

it("hides a stale task action after a conflict without replaying the mutation", async () => {
  const call = vi.spyOn(api, "call").mockRejectedValue(new ApiError(409, "not_paused", "Task is no longer paused."));
  render(banner("task_budget")); fireEvent.click(screen.getByRole("button", { name: "Continue anyway" }));
  expect(await screen.findByText("Task is no longer paused.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Continue anyway" })).not.toBeInTheDocument(); expect(call).toHaveBeenCalledOnce();
});
