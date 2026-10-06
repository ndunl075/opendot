import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowDown, ArrowUp, Ellipsis, Mic, PanelRight, Plus, SquarePen } from "lucide-react";
import { api, ApiError } from "../../api/client";
import { ChatStream, type StreamState } from "../../api/stream";
import type { StreamEvent } from "../../api/endpoints.gen";
import type { ApprovalItem, ApprovalDecisionResult, PausedEvent } from "../../api/types.gen";
import { Badge, EmptyState, ErrorState, IconButton, Button, Skeleton, Textarea } from "../../design/components";
import { Avatar } from "../../design/avatar";
import { useResource } from "../shared";
import { CompanionDetails } from "./CompanionDetails";
import { MessageBubble } from "./MessageBubble";
import { applyStreamEvent, dedupeMessages, mergeHistory, type Message } from "./messages";
import { PausedBanner } from "./PausedBanner";

const loadConversations = () => api.call("chat_conversations");
const loadCompanion = () => api.call("companion_get");
const loadPlan = () => api.call("chatgpt_status");

export default function ChatScreen() {
  const conversations = useResource(loadConversations);
  const companion = useResource(loadCompanion);
  const plan = useResource(loadPlan);
  const [selection, setSelection] = useState<string | null>(null);
  const [session, setSession] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const detailsButton = useRef<HTMLButtonElement>(null);
  function newChat() { setSelection(null); setSession(value => value + 1); }
  return <section className="chat-screen" aria-label="Chat workspace">
    <header className="chat-toolbar"><div className="actions"><IconButton label="New chat" title="New chat" onClick={newChat}><SquarePen size={22} /></IconButton><h1>Chat</h1></div>
      {companion.data?.avatar_seed && <div className="chat-identity"><Avatar seed={companion.data.avatar_seed} size={42} label={`${companion.data.name}'s avatar`} /><span>{companion.data.name}</span></div>}
      <div className="actions"><IconButton label="Conversation history" title="Conversation history" aria-expanded={historyOpen} aria-controls="conversation-history" onClick={() => setHistoryOpen(!historyOpen)}><Ellipsis size={21} /></IconButton><IconButton ref={detailsButton} label="Companion details" title="Companion details" aria-expanded={detailsOpen} aria-controls="companion-details" onClick={() => setDetailsOpen(!detailsOpen)}><PanelRight size={21} /></IconButton></div>
    </header>
    <div className={`chat-layout ${detailsOpen ? "chat-layout--details" : ""}`}><div className="chat-main">
    <section id="conversation-history" className="conversation-sidebar" hidden={!historyOpen} aria-label="Conversation history"><div className="section-heading"><h2>Conversations</h2></div>
      {conversations.loading ? <Skeleton label="Loading conversations" className="screen-skeleton" /> : conversations.error ? <ErrorState error={conversations.error} onRetry={conversations.reload} /> : <>
        {!conversations.data?.conversations.length && <p className="muted">Your conversations will appear here.</p>}
        <nav aria-label="Conversations" className="conversation-list">{conversations.data?.conversations.map(item => <button key={item.id} type="button" aria-current={selection === item.id ? "page" : undefined} onClick={() => setSelection(item.id)}><span>{item.title}</span><small>{item.message_count} messages</small></button>)}</nav>
      </>}
    </section>
      <ConversationPanel key={`${selection ?? "new"}-${session}`} conversationId={selection} avatarSeed={companion.data?.avatar_seed} manageUsageUrl={plan.data?.manage_usage_url} onChanged={conversations.reload} onNewChat={newChat} />
    </div>{detailsOpen && <CompanionDetails onUpdated={companion.setData} onClose={() => { setDetailsOpen(false); detailsButton.current?.focus(); }} />}</div>
  </section>;
}

function ConversationPanel({ conversationId, avatarSeed, manageUsageUrl, onChanged, onNewChat }: { conversationId: string | null; avatarSeed?: string; manageUsageUrl?: string; onChanged: () => void; onNewChat: () => void }) {
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
  const [streamingId, setStreamingId] = useState<string>();
  const [announcement, setAnnouncement] = useState("");
  const [interrupted, setInterrupted] = useState(false);
  const sending = useRef(false);
  const alive = useRef(false);
  const hasLoaded = useRef(false);
  const approvalRequests = useRef(new Map<string, number>());
  const stream = useRef<ChatStream | null>(null);
  const actualId = useRef(conversationId);
  const transcript = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const lastScrollTop = useRef(0);
  const composer = useRef<HTMLTextAreaElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [showLatest, setShowLatest] = useState(false);
  const queuedEvents = useRef<StreamEvent[]>([]);

  const onDecision = useCallback((result: ApprovalDecisionResult) => {
    const id = result.approval.id;
    approvalRequests.current.set(id, (approvalRequests.current.get(id) ?? 0) + 1);
    setApprovals(items => ({ ...items, [id]: result.approval }));
  }, []);

  const refreshApproval = useCallback(async (id: string) => {
    const revision = (approvalRequests.current.get(id) ?? 0) + 1;
    approvalRequests.current.set(id, revision);
    try {
      const item = await api.call("approval_get", { params: { approval_id: id } });
      if (alive.current && approvalRequests.current.get(id) === revision) setApprovals(items => ({ ...items, [id]: item }));
    } catch (cause) {
      if (alive.current && approvalRequests.current.get(id) === revision) {
        setApprovalError(cause);
        setApprovals(items => { const next = { ...items }; delete next[id]; return next; });
      }
    }
  }, []);

  const receive = useCallback((event: StreamEvent) => {
    if (!actualId.current || event.conversation_id !== actualId.current) return;
    if (sending.current) { queuedEvents.current.push(event); return; }
    setInterrupted(false);
    if (event.type === "error") { setError(new ApiError(400, event.code, event.message)); setBusy(false); setStreamingId(undefined); return; }
    if (event.type === "paused") { setPaused(event); setBusy(false); setStreamingId(undefined); onChanged(); return; }
    if (event.type === "approval_required") { void refreshApproval(event.approval.id); setBusy(false); setStreamingId(undefined); }
    if (event.type === "message_started") { setBusy(true); setStreamingId(event.message_id); setAnnouncement(""); setPaused(undefined); setPreviouslyPaused(false); setError(undefined); }
    if (event.type === "completed") { setBusy(false); setStreamingId(undefined); setAnnouncement(event.text); onChanged(); }
    if (event.type === "text_delta") { setBusy(true); setStreamingId(event.message_id); }
    setMessages(items => applyStreamEvent(items, event));
  }, [onChanged, refreshApproval]);

  useEffect(() => {
    let active = true;
    alive.current = true;
    const requests = approvalRequests.current;
    const transport = new ChatStream(actualId.current ?? "", receive, undefined, undefined, value => {
      if (!active) return;
      setState(value);
      if (value === "reconnecting" || value === "failed" || value === "unauthorized") {
        setBusy(false); setStreamingId(undefined); setInterrupted(true);
      }
      if (value === "unauthorized") setError(new ApiError(1008, "unauthorized", "Your session expired"));
      if (value === "failed") setError(new Error("The chat connection could not be restored. Try connecting again."));
    });
    stream.current = transport;
    async function start() {
      // Let StrictMode's initial cleanup cancel before creating a real socket.
      await Promise.resolve();
      if (!active) return;
      setError(undefined); setApprovalError(undefined); setLoading(!hasLoaded.current && !!actualId.current);
      try {
        if (actualId.current) {
          const conversation = await api.call("chat_conversation", { params: { conversation_id: actualId.current } });
          if (!active) return;
          setMessages(items => mergeHistory(items, conversation.messages)); setPreviouslyPaused(conversation.paused ?? false);
          const ids = [...new Set(conversation.messages.flatMap(message => message.approval_id ? [message.approval_id] : []))];
          for (const id of ids) void refreshApproval(id);
        }
        if (active) transport.connect();
      } catch (cause) { if (active) setError(cause); }
      finally { if (active) { hasLoaded.current = true; setLoading(false); } }
    }
    void start();
    return () => {
      active = false; alive.current = false;
      for (const [id, revision] of requests) requests.set(id, revision + 1);
      transport.close(); stream.current = null;
    };
  }, [conversationId, receive, attempt, refreshApproval]);

  useEffect(() => {
    if (!busy) return;
    const timer = setTimeout(() => { setBusy(false); setStreamingId(undefined); setInterrupted(true); }, 30000);
    return () => clearTimeout(timer);
  }, [busy, messages]);

  const scrollToLatest = useCallback(() => {
    const element = transcript.current;
    if (!element) return;
    const behavior = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth";
    element.scrollTo?.({ top: element.scrollHeight, behavior });
  }, []);
  useEffect(() => { if (following.current) scrollToLatest(); else setShowLatest(true); }, [messages, approvals, paused, scrollToLatest]);
  useEffect(() => {
    const element = transcript.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (following.current) scrollToLatest();
    });
    observer.observe(element);
    if (content.current) observer.observe(content.current);
    return () => observer.disconnect();
  }, [scrollToLatest]);
  useLayoutEffect(() => {
    const element = composer.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [text]);
  async function send() {
    const value = text.trim();
    if (!value || busy || sending.current || state !== "connected" || loading) return;
    sending.current = true; setBusy(true); setError(undefined); setInterrupted(false);
    const id = `local-${crypto.randomUUID()}`;
    const created_at = new Date().toISOString();
    setMessages(items => [...items.filter(item => !item.pending),
      { id, renderKey: id, role: "user", text: value, created_at },
      { id: `${id}_a`, renderKey: `${id}_a`, role: "assistant", text: "", created_at, pending: true, received: false }
    ]);
    setText(""); composer.current?.focus();
    try {
      const accepted = await api.call("chat_send", { body: { conversation_id: actualId.current, text: value } });
      if (!alive.current) return;
      actualId.current = accepted.conversation_id;
      setMessages(items => dedupeMessages(items.map(item => item.id === id ? { ...item, id: accepted.message_id } : item.id === `${id}_a` ? { ...item, id: `${accepted.message_id}_a` } : item)));
      sending.current = false;
      for (const event of queuedEvents.current.splice(0)) receive(event);
      stream.current?.subscribe(accepted.conversation_id);
      onChanged();
    } catch (cause) { if (alive.current) {
      setError(cause); setBusy(false); setStreamingId(undefined);
      setMessages(items => items.filter(item => item.id !== `${id}_a`).map(item => item.id === id ? { ...item, failed: true } : item));
      setText(current => current || value);
    } }
    finally { sending.current = false; queuedEvents.current = []; }
  }
  // A send acknowledgement alone is not evidence of a read. Only a subsequent
  // assistant response establishes that the companion has started processing it.
  const lastUserIndex = messages.map(message => message.role).lastIndexOf("user");
  const readAt = lastUserIndex < 0 ? undefined : messages.slice(lastUserIndex + 1).find(message => message.role === "assistant" && message.received !== false)?.created_at;
  const readDate = readAt ? new Date(readAt) : undefined;
  const readTime = readDate && Number.isFinite(readDate.getTime()) ? readDate.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : undefined;
  return <section className="conversation-panel" aria-label="Current conversation">
    <div className="stream-status"><Badge tone={state === "connected" ? "success" : "warning"}>{state === "connected" ? "Connected" : state === "reconnecting" ? "Reconnecting — resuming replies" : state === "unauthorized" ? "Session expired" : state === "closed" || state === "failed" ? "Disconnected" : "Connecting"}</Badge><span className="sr-only" role="status">{busy ? "Companion is responding" : state}</span></div>
    {state === "reconnecting" && <p className="notice">Connection lost. OpenDot is reconnecting and requesting missed replies. Messages are never automatically sent twice.</p>}
    {interrupted && state !== "unauthorized" && <p className="notice">The reply was interrupted or has not arrived. Check for a reply before sending again. <Button variant="secondary" onClick={() => { setBusy(false); setInterrupted(false); setAttempt(value => value + 1); }}>Retry connection</Button></p>}
    {error != null && <ErrorState error={error} onRetry={() => { setBusy(false); setAttempt(value => value + 1); }} />}
    {approvalError != null && <ErrorState error={approvalError} onRetry={() => setAttempt(value => value + 1)} />}
    {paused ? <PausedBanner event={paused} manageUsageUrl={manageUsageUrl} /> : previouslyPaused && <p className="notice" role="alert">This conversation is paused. The saved history does not include its pause reason. <Link to="/companion">Review your companion</Link> and <Link to="/usage">budgets</Link>.</p>}
    <div className="sr-only" role="status" aria-label="Completed reply" aria-live="polite" aria-atomic="true">{announcement}</div>
    <div className="transcript-container"><div ref={transcript} className="transcript" role="log" aria-label="Conversation messages" aria-live="off" onWheel={event => { if (event.deltaY < 0) { following.current = false; setShowLatest(true); } }} onTouchMove={() => { following.current = false; }} onScroll={event => {
      const el = event.currentTarget;
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      // Intermediate events from our smooth scroll must not disable following.
      if (el.scrollTop < lastScrollTop.current - 1) following.current = false;
      if (nearBottom) following.current = true;
      lastScrollTop.current = el.scrollTop;
      setShowLatest(!following.current);
    }}>
      <div ref={content} className="transcript-content">
      {loading ? <Skeleton className="screen-skeleton" label="Loading conversation" /> : !messages.length && <EmptyState title="What’s on your mind?" icon={<Avatar seed={avatarSeed ?? "v2:c=slate;h=none;p=none"} size={72} />} />}
      {messages.map((message, index) => <Fragment key={message.renderKey ?? message.id}>
        <MessageBubble message={message} streaming={busy && message.role === "assistant" && (!!message.pending || message.id === streamingId || message.id === `${streamingId}_a`)} approval={message.approval_id ? approvals[message.approval_id] : undefined} onDecision={onDecision} />
        {index === lastUserIndex && <p className="chat-read-receipt" aria-label={readTime ? "Message read time" : undefined} aria-hidden={!readTime} title={readTime ? "Your companion has started responding" : undefined}>{readTime ? <><span>Read </span><time dateTime={readAt}>{readTime}</time></> : "\u00a0"}</p>}
      </Fragment>)}
      </div>
    </div>{showLatest && <Button variant="secondary" className="jump-to-latest" onClick={() => { following.current = true; setShowLatest(false); scrollToLatest(); }}><ArrowDown size={16} />Jump to latest</Button>}</div>
    <form className="chat-composer" onSubmit={event => { event.preventDefault(); send(); }}>
      <IconButton label="Start a new conversation" title="Start a new conversation" onClick={onNewChat} disabled={busy || !!text.trim()}><Plus size={24} /></IconButton>
      <Textarea ref={composer} label="Message your companion" placeholder="Send a message" rows={1} value={text} onChange={event => setText(event.target.value)} hint="Enter to send. Shift + Enter for a new line." onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }} />
      <IconButton label="Voice input unavailable" title="Voice input is not available yet" className="voice-button" disabled><Mic size={22} strokeWidth={1.7} /></IconButton>
      <IconButton label="Send message" title={busy ? "Companion is responding" : "Send message"} loading={busy} className="send-button" type="submit" disabled={!text.trim() || state !== "connected" || loading}>{!busy && <ArrowUp size={22} />}</IconButton>
    </form>
  </section>;
}
