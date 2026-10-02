import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import type { ApprovalItem, ChatGPTStatus, StreamEvent } from "../src/api/types.gen";
import ChatScreen from "../src/screens/chat/ChatScreen";

class Socket extends EventTarget {
  static instances: Socket[] = [];
  sent: string[] = [];
  constructor() { super(); Socket.instances.push(this); }
  send(value: string) { this.sent.push(value); }
  close() { this.dispatchEvent(new Event("close")); }
  open() { this.dispatchEvent(new Event("open")); }
  event(value: StreamEvent) { this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(value) })); }
}
const status: ChatGPTStatus = { state: "signed_in", eligible: true, plan: "eligible_plus", manage_usage_url: "https://chatgpt.com/settings/usage" };
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); Socket.instances = []; });

it("sends through REST, ignores foreign broadcasts, and announces only final replies", async () => {
  vi.stubGlobal("WebSocket", Socket);
  const approval = { id: "a", title: "Draft reply", action: "gmail.create_draft", created_at: "2026-09-30", status: "pending", payload: { body: "Hello" }, preview: "Draft only.", review_note: "Check the recipient.", review_verdict: "concern" } as const;
  const call = vi.spyOn(api, "call").mockImplementation(async name => {
    if (name === "chatgpt_status") return status as never;
    if (name === "chat_send") return { conversation_id: "c", message_id: crypto.randomUUID(), stream_path: "/v1/chat/stream" } as never;
    if (name === "approval_get") return approval as never;
    return { conversations: [] } as never;
  });
  render(<MemoryRouter><ChatScreen /></MemoryRouter>);
  await waitFor(() => expect(Socket.instances).toHaveLength(1));
  const socket = Socket.instances[0];
  act(() => socket.open());
  fireEvent.change(screen.getByLabelText("Message your companion"), { target: { value: "Please draft a reply" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  act(() => socket.event({ conversation_id: "other", message_id: "foreign", type: "text_delta", seq: 99, text: "Another window's reply" }));
  await waitFor(() => expect(socket.sent.map(value => JSON.parse(value))).toContainEqual({ type: "resume", conversation_id: "c", after_seq: 0 }));
  expect(call).toHaveBeenCalledWith("chat_send", { body: { conversation_id: null, text: "Please draft a reply" } });
  expect(screen.queryByText("Another window's reply")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Message read time")).not.toBeInTheDocument();
  const base = { conversation_id: "c", message_id: "m" };
  act(() => {
    socket.event({ ...base, type: "message_started", seq: 1 });
    socket.event({ ...base, type: "text_delta", seq: 2, text: "I can help." });
  });
  expect(screen.getByText("I can help.")).toBeInTheDocument();
  expect(screen.getByLabelText("Message read time")).toHaveTextContent(/^Read /);
  expect(screen.getByRole("log")).toHaveAttribute("aria-live", "off");
  expect(screen.getByLabelText("Companion reply")).toHaveAttribute("aria-busy", "true");
  expect(screen.getByLabelText("Completed reply")).toBeEmptyDOMElement();
  expect(screen.getByText("Credits not reported")).toBeInTheDocument();
  act(() => {
    socket.event({ ...base, type: "approval_required", seq: 3, approval: { id: "a", title: "Draft reply", action: "gmail.create_draft", created_at: "2026-09-30", status: "pending", payload: { body: "Hello" }, preview: "Draft only.", review_note: "Check the recipient.", review_verdict: "concern" } });
    socket.event({ ...base, type: "completed", seq: 4, text: "I can help.", usage: { model: "catalog-model", effort: "low", credits: 0.2 } });
  });
  expect(await screen.findByRole("button", { name: "Approve" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Reviewer note" })).toHaveTextContent("Check the recipient.");
  expect(screen.getByText("Review: concern")).toBeInTheDocument();
  expect(screen.getByText("catalog-model")).toBeInTheDocument();
  expect(screen.getByText("0.2 credits")).toBeInTheDocument();
  expect(screen.getByText("Using ChatGPT plan")).toBeInTheDocument();
  expect(screen.getByLabelText("Companion reply")).toHaveAttribute("aria-busy", "false");
  expect(screen.getByLabelText("Completed reply")).toHaveTextContent("I can help.");
  fireEvent.change(screen.getByLabelText("Message your companion"), { target: { value: "Next message" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(call).toHaveBeenCalledWith("chat_send", { body: { conversation_id: "c", text: "Next message" } }));
  await waitFor(() => expect(screen.queryByLabelText("Message read time")).not.toBeInTheDocument());
});

it.each([true, false])("uses authoritative approval state across replay and fetch ordering (fetch first: %s)", async fetchFirst => {
  vi.stubGlobal("WebSocket", Socket);
  const approved: ApprovalItem = { id: "a", title: "Already decided", action: "draft", status: "approved", created_at: "2026-09-30", payload: {}, preview: "Draft" };
  let resolve!: (value: ApprovalItem) => void;
  const pending = new Promise<ApprovalItem>(done => { resolve = done; });
  let gets = 0;
  vi.spyOn(api, "call").mockImplementation(async name => {
    if (name === "chatgpt_status") return status as never;
    if (name === "chat_conversations") return { conversations: [{ id: "c", title: "Saved conversation", message_count: 1 }] } as never;
    if (name === "chat_conversation") return { id: "c", title: "Saved", messages: [{ id: "m", role: "assistant", text: "Saved reply", approval_id: "a", created_at: "2026-09-30" }] } as never;
    if (name === "approval_get") { gets++; return (gets === 1 && !fetchFirst ? await pending : approved) as never; }
    throw new Error(`Unexpected ${name}`);
  });
  render(<MemoryRouter><ChatScreen /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "Conversation history" }));
  fireEvent.click(await screen.findByRole("button", { name: /Saved conversation/ }));
  await screen.findByText("Saved reply");
  await waitFor(() => expect(Socket.instances).toHaveLength(2));
  const socket = Socket.instances.at(-1)!;
  act(() => { socket.open(); socket.event({ type: "approval_required", conversation_id: "c", message_id: "m", seq: 2, approval: { ...approved, status: "pending" } }); });
  await screen.findByText("approved");
  if (!fetchFirst) await act(async () => resolve({ ...approved, status: "pending" }));
  expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
});

it.each(["reconnect", "timeout"])("unblocks a silent %s and offers manual connection retry without resending", async cause => {
  vi.stubGlobal("WebSocket", Socket);
  const call = vi.spyOn(api, "call").mockImplementation(async name => {
    if (name === "chatgpt_status") return status as never;
    if (name === "chat_send") return { conversation_id: "c", message_id: "user", stream_path: "/v1/chat/stream" } as never;
    if (name === "chat_conversation") return { id: "c", title: "Saved", messages: [] } as never;
    return { conversations: [] } as never;
  });
  render(<MemoryRouter><ChatScreen /></MemoryRouter>);
  await waitFor(() => expect(Socket.instances).toHaveLength(1)); act(() => Socket.instances[0].open());
  fireEvent.change(screen.getByLabelText("Message your companion"), { target: { value: "Hello" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await screen.findByText("Hello");
  fireEvent.change(screen.getByLabelText("Message your companion"), { target: { value: "Next" } });
  expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  vi.useFakeTimers();
  if (cause === "reconnect") {
    act(() => Socket.instances[0].close());
    await act(async () => { vi.advanceTimersByTime(1000); });
    act(() => Socket.instances[1].open());
  } else {
    // A new delta restarts the watchdog under fake time.
    act(() => Socket.instances[0].event({ type: "text_delta", conversation_id: "c", message_id: "m", seq: 1, text: "Partial" }));
    await act(async () => { vi.advanceTimersByTime(30000); });
  }
  expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Retry connection" })); });
  expect(call.mock.calls.filter(([name]) => name === "chat_send")).toHaveLength(1);
  expect(Socket.instances.flatMap(socket => socket.sent).map(frame => JSON.parse(frame)).every(frame => frame.type === "resume")).toBe(true);
});

it("shows unavailable history without inventing conversation data", async () => {
  vi.stubGlobal("WebSocket", Socket);
  vi.spyOn(api, "call").mockResolvedValueOnce({ conversations: [{ id: "c", title: "Previous chat", message_count: 1, updated_at: "2026-09-30" }] }).mockResolvedValueOnce(status).mockRejectedValue(new ApiError(501, "not_implemented", "Not served"));
  render(<MemoryRouter><ChatScreen /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "Conversation history" }));
  fireEvent.click(await screen.findByRole("button", { name: /Previous chat/ }));
  expect(await screen.findByText("Not available in this version yet")).toBeInTheDocument();
});

it("keeps a REST-assigned conversation if the user reconnects before the send response arrives", async () => {
  vi.stubGlobal("WebSocket", Socket);
  let accept!: (value: { conversation_id: string; message_id: string; stream_path: string }) => void;
  const pending = new Promise(done => { accept = done; });
  const call = vi.spyOn(api, "call").mockImplementation(async name => {
    if (name === "chatgpt_status") return status as never;
    if (name === "chat_send") return await pending as never;
    return { conversations: [] } as never;
  });
  render(<MemoryRouter><ChatScreen /></MemoryRouter>);
  await waitFor(() => expect(Socket.instances).toHaveLength(1)); act(() => Socket.instances[0].open());
  fireEvent.change(screen.getByLabelText("Message your companion"), { target: { value: "Delayed send" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  act(() => Socket.instances[0].close());
  fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));
  await waitFor(() => expect(Socket.instances).toHaveLength(2)); act(() => Socket.instances[1].open());
  await act(async () => accept({ conversation_id: "accepted", message_id: "user", stream_path: "/v1/chat/stream" }));
  expect(screen.getByText("Delayed send")).toBeVisible();
  expect(Socket.instances[1].sent.map(value => JSON.parse(value))).toContainEqual({ type: "resume", conversation_id: "accepted", after_seq: 0 });
  expect(call.mock.calls.filter(([name]) => name === "chat_send")).toHaveLength(1);
});

it("removes a pending approval when its authoritative refresh reports that it is gone", async () => {
  vi.stubGlobal("WebSocket", Socket);
  const approval: ApprovalItem = { id: "a", title: "Draft", action: "draft", status: "pending", created_at: "2026-09-30", payload: {}, preview: "Draft" };
  let missing = false;
  vi.spyOn(api, "call").mockImplementation(async name => {
    if (name === "chatgpt_status") return status as never;
    if (name === "chat_send") return { conversation_id: "c", message_id: "user", stream_path: "/v1/chat/stream" } as never;
    if (name === "approval_get") { if (missing) throw new ApiError(404, "not_found", "Approval no longer exists."); return approval as never; }
    return { conversations: [] } as never;
  });
  render(<MemoryRouter><ChatScreen /></MemoryRouter>);
  await waitFor(() => expect(Socket.instances).toHaveLength(1)); const socket = Socket.instances[0]; act(() => socket.open());
  fireEvent.change(screen.getByLabelText("Message your companion"), { target: { value: "Draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(socket.sent).toHaveLength(1));
  const event = { type: "approval_required", conversation_id: "c", message_id: "m", seq: 2, approval } as const;
  act(() => socket.event(event)); await screen.findByRole("button", { name: "Approve" });
  missing = true; act(() => socket.event({ ...event, seq: 3 }));
  await screen.findByText("Approval no longer exists.");
  expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
});
