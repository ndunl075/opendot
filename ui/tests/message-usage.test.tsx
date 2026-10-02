import { render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { applyStreamEvent, mergeHistory } from "../src/screens/chat/messages";
import { MessageBubble } from "../src/screens/chat/MessageBubble";

it("retains completed and restored usage in state without rendering it under replies", () => {
  const usage = { model: "gpt-5.6-luna", effort: "low", credits: 0.008, input_tokens: 100, output_tokens: 12, cached_input_tokens: 50 } as const;
  const messages = applyStreamEvent([], { type: "completed", conversation_id: "c", message_id: "m", seq: 1, text: "Hello there.", usage });
  expect(messages[0].usage).toEqual(usage);
  const restored = mergeHistory(messages, [{ ...messages[0], usage }]);
  expect(restored[0].usage).toEqual(usage);
  render(<MessageBubble message={restored[0]} streaming={false} onDecision={vi.fn()} />);
  const reply = screen.getByRole("article", { name: "Companion reply" });
  expect(within(reply).getByText("Hello there.")).toBeVisible();
  expect(reply).not.toHaveTextContent(/gpt-5\.6-luna|low effort|0\.008 credits/);
  expect(reply.querySelector(".usage-stamp")).toBeNull();
});
