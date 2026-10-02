import { memo } from "react";
import type { ApprovalItem, ApprovalDecisionResult } from "../../api/types.gen";
import { Badge, UsageStamp } from "../../design/components";
import { ApprovalCard } from "./ApprovalCard";
import type { Message } from "./messages";

export const MessageBubble = memo(function MessageBubble({ message, streaming, approval, onDecision }: {
  message: Message;
  streaming: boolean;
  approval?: ApprovalItem;
  onDecision: (result: ApprovalDecisionResult) => void;
}) {
  return <article className={`chat-message chat-message--${message.role}`} aria-busy={streaming} aria-label={message.role === "user" ? "Your message" : "Companion reply"}>
    <p className="sr-only">{message.role === "user" ? "You" : "Companion"}</p>
    <p className="message-text">{message.text || (streaming ? <span className="typing-indicator"><span className="sr-only">Companion is typing</span><i /><i /><i /></span> : message.pending ? "No reply received yet." : "")}</p>
    {message.failed && <p className="message-delivery">Not sent</p>}
    {message.tool_calls?.map(call => <p className="tool-record" key={call.call_id}><Badge>{call.status.replaceAll("_", " ")}</Badge> {call.summary}</p>)}
    {message.role === "assistant" && <div className={message.pending ? "message-usage--pending" : undefined} aria-hidden={message.pending || undefined}><UsageStamp model={message.usage?.model ?? "Model not reported"} effort={message.usage?.effort ?? "Unreported"} credits={message.usage?.credits ?? null} /></div>}
    {approval && <ApprovalCard approval={approval} onDecision={onDecision} />}
  </article>;
});
