import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../src/api/client";
import type { ChatGPTStatus, StreamEvent } from "../src/api/types.gen";
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
afterEach(() => { vi.unstubAllGlobals(); Socket.instances = []; });

it("sends over the typed stream and renders deltas, approval, and final usage", async () => {
  vi.stubGlobal("WebSocket", Socket);
  vi.spyOn(api, "call").mockResolvedValue({ conversations: [] }).mockResolvedValueOnce({ conversations: [] }).mockResolvedValueOnce(status);
  render(<MemoryRouter><ChatScreen /></MemoryRouter>);
  await waitFor(() => expect(Socket.instances).toHaveLength(1));
  const socket = Socket.instances[0];
  act(() => socket.open());
  fireEvent.change(screen.getByLabelText("Message your companion"), { target: { value: "Please draft a reply" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  expect(socket.sent.map(value => JSON.parse(value))).toContainEqual({ type: "send", conversation_id: null, text: "Please draft a reply" });
  const base = { conversation_id: "c", message_id: "m" };
  act(() => {
    socket.event({ ...base, type: "message_started", seq: 1 });
    socket.event({ ...base, type: "text_delta", seq: 2, text: "I can help." });
  });
  expect(screen.getByText("I can help.")).toBeInTheDocument();
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
});

it("shows unavailable history without inventing conversation data", async () => {
  vi.stubGlobal("WebSocket", Socket);
  vi.spyOn(api, "call").mockResolvedValueOnce({ conversations: [{ id: "c", title: "Previous chat", message_count: 1, updated_at: "2026-09-30" }] }).mockResolvedValueOnce(status).mockRejectedValue(new ApiError(501, "not_implemented", "Not served"));
  render(<MemoryRouter><ChatScreen /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: /Previous chat/ }));
  expect(await screen.findByText("Not available in this version yet")).toBeInTheDocument();
});
