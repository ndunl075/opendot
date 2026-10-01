import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUp, MessageSquare, Plus } from "lucide-react";
import { api, ApiError } from "../../api/client";
import { ChatStream, type StreamState } from "../../api/stream";
import type { StreamEvent } from "../../api/endpoints.gen";
import type { ApprovalItem, Conversation, PausedEvent } from "../../api/types.gen";
import { Badge, Button, Card, EmptyState, ErrorState, Skeleton, Textarea, UsageStamp } from "../../design/components";
import { ScreenHeading, safeExternalUrl, useResource } from "../shared";
import { ApprovalCard } from "./ApprovalCard";
import { PausedBanner } from "./PausedBanner";

const loadConversations = () => api.call("chat_conversations");
const loadPlan = () => api.call("chatgpt_status");
type Message = Conversation["messages"][number];

export default function ChatScreen() {
  const conversations = useResource(loadConversations);
  const plan = useResource(loadPlan);
  const [selection, setSelection] = useState<string | null>(null);
  const [session, setSession] = useState(0);
  return <><ScreenHeading title="Chat" description="A little space to think things through." />
    <div className="chat-layout"><Card className="conversation-sidebar"><div className="section-heading"><h2>Conversations</h2><Button size="sm" variant="ghost" onClick={() => { setSelection(null); setSession(value => value + 1); }}><Plus size={16} aria-hidden="true" />New chat</Button></div>
      {conversations.loading ? <Skeleton label="Loading conversations" className="screen-skeleton" /> : conversations.error ? <ErrorState error={conversations.error} onRetry={conversations.reload} /> : <>
        {!conversations.data?.conversations.length && <p className="muted">Your conversations will appear here.</p>}
        <nav aria-label="Conversations" className="conversation-list">{conversations.data?.conversations.map(item => <button key={item.id} type="button" aria-current={selection === item.id ? "page" : undefined} onClick={() => setSelection(item.id)}><span>{item.title}</span><small>{item.message_count} messages</small></button>)}</nav>
      </>}
    </Card><div className="chat-main"><div className="chat-plan"><MessageSquare size={17} aria-hidden="true" />
      {/* Official OpenAI DevKit assets are not in this repository. Text only. */}
      <span>{plan.data?.state === "signed_in" && plan.data.eligible ? "Using ChatGPT plan" : "ChatGPT plan"}</span>
      {safeExternalUrl(plan.data?.manage_usage_url) && <a href={safeExternalUrl(plan.data?.manage_usage_url)} target="_blank" rel="noreferrer">Manage usage</a>}
    </div>
      <ConversationPanel key={`${selection ?? "new"}-${session}`} conversationId={selection} manageUsageUrl={plan.data?.manage_usage_url} onChanged={conversations.reload} />
    </div></div>
  </>;
}

function ConversationPanel({ conversationId, manageUsageUrl, onChanged }: { conversationId: string | null; manageUsageUrl?: string; onChanged: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [approvals, setApprovals] = useState<Record<string, ApprovalItem>>({});
  const [paused, setPaused] = useState<PausedEvent>();
  const [previouslyPaused, setPreviouslyPaused] = useState(false);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(!!conversationId);
  const [error, setError] = useState<unknown>();
  const [approvalError, setApprovalError] = useState<unknown>();
  const [state, setState] = useState<StreamState>("connecting");
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const stream = useRef<ChatStream | null>(null);
  const actualId = useRef(conversationId);
  const transcript = useRef<HTMLDivElement>(null);
  const following = useRef(true);

  const receive = useCallback((event: StreamEvent) => {
    actualId.current = event.conversation_id;
    if (event.type === "error") { setError(new ApiError(400, event.code, event.message)); setBusy(false); return; }
    if (event.type === "paused") { setPaused(event); setBusy(false); onChanged(); return; }
    if (event.type === "approval_required") { setApprovals(items => ({ ...items, [event.approval.id]: event.approval })); setBusy(false); }
    if (event.type === "message_started") { setBusy(true); setPaused(undefined); setError(undefined); }
    if (event.type === "completed") { setBusy(false); onChanged(); }
    setMessages(items => {
      const found = items.find(item => item.id === event.message_id);
      const message: Message = found ?? { id: event.message_id, role: "assistant", created_at: new Date().toISOString(), text: "" };
      let next = message;
      switch (event.type) {
        case "message_started": next = { ...message, text: "", usage: null, tool_calls: [] }; break;
        case "text_delta": next = { ...message, text: message.text + event.text }; break;
        case "completed": next = { ...message, text: event.text, usage: event.usage }; break;
        case "approval_required": next = { ...message, approval_id: event.approval.id }; break;
        case "tool_call": next = { ...message, tool_calls: [...(message.tool_calls ?? []).filter(call => call.call_id !== event.call.call_id), event.call] }; break;
      }
      return found ? items.map(item => item.id === next.id ? next : item) : [...items, next];
    });
  }, [onChanged]);

  useEffect(() => {
    let active = true;
    const transport = new ChatStream(conversationId ?? "", receive, undefined, undefined, value => { if (active) setState(value); });
    stream.current = transport;
    async function start() {
      // Let StrictMode's initial cleanup cancel before creating a real socket.
      await Promise.resolve();
      if (!active) return;
      setError(undefined); setLoading(!!conversationId);
      try {
        if (conversationId) {
          const conversation = await api.call("chat_conversation", { params: { conversation_id: conversationId } });
          if (!active) return;
          setMessages(conversation.messages); setPreviouslyPaused(conversation.paused ?? false);
          const ids = [...new Set(conversation.messages.flatMap(message => message.approval_id ? [message.approval_id] : []))];
          for (const id of ids) {
            void api.call("approval_get", { params: { approval_id: id } }).then(item => {
              if (active) setApprovals(previous => ({ ...previous, [id]: previous[id] ?? item }));
            }).catch(cause => { if (active) setApprovalError(cause); });
          }
        }
        if (active) transport.connect();
      } catch (cause) { if (active) setError(cause); }
      finally { if (active) setLoading(false); }
    }
    void start();
    return () => { active = false; transport.close(); stream.current = null; };
  }, [conversationId, receive, attempt]);

  useEffect(() => { if (following.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight; }, [messages, approvals, paused]);
  function send() {
    const value = text.trim();
    if (!value || busy) return;
    const sent = stream.current?.send({ type: "send", conversation_id: actualId.current, text: value });
    if (!sent) { setError(new Error("The chat connection is not ready.")); return; }
    setMessages(items => [...items, { id: crypto.randomUUID(), role: "user", text: value, created_at: new Date().toISOString() }]);
    setText(""); setBusy(true); setError(undefined); following.current = true;
  }
  return <section className="conversation-panel" aria-label="Current conversation">
    <div className="stream-status"><Badge tone={state === "connected" ? "success" : "warning"}>{state === "connected" ? "Connected" : state === "reconnecting" ? "Reconnecting — resuming replies" : state === "closed" ? "Disconnected" : "Connecting"}</Badge><span className="sr-only" role="status">{busy ? "Companion is responding" : state}</span></div>
    {state === "reconnecting" && <p className="notice">Connection lost. OpenDot is reconnecting and requesting missed replies. Messages are never automatically sent twice.</p>}
    {error != null && <ErrorState error={error} onRetry={() => setAttempt(value => value + 1)} />}
    {approvalError != null && <ErrorState error={approvalError} onRetry={() => setAttempt(value => value + 1)} />}
    {paused ? <PausedBanner event={paused} manageUsageUrl={manageUsageUrl} /> : previouslyPaused && <p className="notice" role="alert">This conversation is paused. The saved history does not include its pause reason. <Link to="/companion">Review your companion</Link> and <Link to="/usage">budgets</Link>.</p>}
    <div ref={transcript} className="transcript" role="log" aria-label="Conversation messages" aria-live="polite" aria-relevant="additions text" onScroll={event => { const el = event.currentTarget; following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }}>
      {loading ? <Skeleton className="screen-skeleton" label="Loading conversation" /> : !messages.length && <EmptyState title="What’s on your mind?" description="Ask a question, think through a task, or make a little room in your day. Actions wait for your permission." icon={<MessageSquare size={28} />} />}
      {messages.map(message => <article key={message.id} className={`chat-message chat-message--${message.role}`} aria-label={message.role === "user" ? "Your message" : "Companion reply"}>
        <p className="message-author">{message.role === "user" ? "You" : "Companion"}</p><p className="message-text">{message.text || (busy ? "Thinking…" : "")}</p>
        {message.tool_calls?.map(call => <p className="tool-record" key={call.call_id}><Badge>{call.status.replaceAll("_", " ")}</Badge> {call.summary}</p>)}
        {message.role === "assistant" && <UsageStamp model={message.usage?.model ?? "Model not reported"} effort={message.usage?.effort ?? "Unreported"} credits={message.usage?.credits ?? null} />}
        {message.approval_id && approvals[message.approval_id] && <ApprovalCard approval={approvals[message.approval_id]} onDecision={result => setApprovals(items => ({ ...items, [result.approval.id]: result.approval }))} />}
      </article>)}
    </div>
    <form className="chat-composer" onSubmit={event => { event.preventDefault(); send(); }}>
      <Textarea label="Message your companion" rows={3} value={text} onChange={event => setText(event.target.value)} hint="Enter to send. Shift + Enter for a new line." onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} />
      <Button type="submit" disabled={!text.trim() || busy || state !== "connected" || loading}><ArrowUp size={17} aria-hidden="true" />Send message</Button>
    </form>
  </section>;
}
