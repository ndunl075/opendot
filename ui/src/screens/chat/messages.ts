import type { Conversation } from "../../api/types.gen";
import type { StreamEvent } from "../../api/endpoints.gen";

export type Message = Conversation["messages"][number] & {
  renderKey?: string;
  pending?: boolean;
  received?: boolean;
  failed?: boolean;
};

export function dedupeMessages(messages: Message[]): Message[] {
  return [...new Map(messages.map(message => [message.id, message])).values()];
}

/** History is authoritative; preserve live-only rows and their mounted identity. */
export function mergeHistory(live: Message[], history: Message[]): Message[] {
  const byId = new Map(live.map(message => [message.id, message]));
  const merged = new Map<string, Message>();
  for (const message of history) {
    const previous = byId.get(message.id);
    merged.set(message.id, { ...message, renderKey: previous?.renderKey, received: true });
  }
  for (const message of live) if (!merged.has(message.id)) merged.set(message.id, message);
  return [...merged.values()];
}

/** Older daemons tagged replies with the user's ID. Never mutate that row. */
export function applyStreamEvent(items: Message[], event: StreamEvent): Message[] {
  if (event.type === "error" || event.type === "paused") return items;
  const id = items.some(item => item.id === event.message_id && item.role === "user")
    ? `${event.message_id}_a` : event.message_id;
  const found = items.find(item => item.id === id && item.role !== "user")
    ?? items.find(item => item.role === "assistant" && item.pending);
  const message: Message = { ...(found ?? { role: "assistant", created_at: new Date().toISOString(), text: "" }), id, pending: false, received: true };
  let next = message;
  switch (event.type) {
    // A replayed start must not blank an already restored reply.
    case "message_started": break;
    case "text_delta": next = { ...message, text: message.text + event.text }; break;
    case "completed": next = { ...message, text: event.text, usage: event.usage }; break;
    case "approval_required": next = { ...message, approval_id: event.approval.id }; break;
    case "tool_call": next = { ...message, tool_calls: [...(message.tool_calls ?? []).filter(call => call.call_id !== event.call.call_id), event.call] }; break;
  }
  return found ? items.map(item => item === found ? next : item) : [...items, next];
}
