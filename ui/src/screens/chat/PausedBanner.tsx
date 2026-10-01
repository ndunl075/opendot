import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, isSessionError, isStaleItemError } from "../../api/client";
import { ChatStream } from "../../api/stream";
import type { PausedEvent } from "../../api/types.gen";
import { Button, ErrorState } from "../../design/components";
import { safeExternalUrl, useMutation } from "../shared";

const reasons: Record<PausedEvent["reason"], { title: string; detail: string; path: string; action: string }> = {
  task_budget: { title: "Task budget reached", detail: "Continuing may use more credits than the task budget. The daily budget still applies.", path: "/usage", action: "Review budgets" },
  daily_budget: { title: "Daily budget reached", detail: "All plan requests are paused. Wait until the next day or raise the daily budget in Usage.", path: "/usage", action: "Review daily budget" },
  rate_limited: { title: "ChatGPT plan limit reached", detail: "Resume after raising your OpenDot weekly limit in ChatGPT Settings, Usage, or after the limit has reset. Keep credit use off.", path: "/usage", action: "Manage usage" },
  top_tier_approval: { title: "Top-tier approval needed", detail: "The top-tier model uses more of your ChatGPT plan. This permission applies only to this task.", path: "/settings", action: "Review model settings" },
  anomaly: { title: "Paused for unusual activity", detail: "Review what happened in Activity before resuming your companion from its profile.", path: "/activity", action: "Review activity" },
  user: { title: "Paused by you", detail: "Your companion is holding its work. Resume it from the Companion screen when you are ready.", path: "/companion", action: "Open companion" },
};
export function PausedBanner({ event, manageUsageUrl }: { event: PausedEvent; manageUsageUrl?: string }) {
  // A new pause needs a new explicit decision; replaying the same event does not.
  return <PauseNotice key={JSON.stringify([event.conversation_id, event.message_id, event.seq, event.reason, event.task_id])} event={event} manageUsageUrl={manageUsageUrl} />;
}
function PauseNotice({ event, manageUsageUrl }: { event: PausedEvent; manageUsageUrl?: string }) {
  const mutation = useMutation();
  const [confirmed, setConfirmed] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [verificationError, setVerificationError] = useState<unknown>();
  useEffect(() => {
    if (!event.task_id || !["task_budget", "top_tier_approval", "rate_limited"].includes(event.reason)) return;
    let active = true;
    // The contract has no task-get. A fresh replay drops tasks no longer paused.
    // Only that exact, current pause can authorize a resume control.
    const check = new ChatStream(event.conversation_id, current => {
      if (!active || current.message_id !== event.message_id) return;
      setConfirmed(!invalid && current.type === "paused" && current.task_id === event.task_id && current.reason === event.reason && current.seq === event.seq);
    }, undefined, undefined, state => {
      if (!active) return;
      if (state !== "connected") setConfirmed(false);
      if (state === "unauthorized") setVerificationError(new ApiError(1008, "unauthorized", "Your session expired"));
    });
    check.connect();
    return () => { active = false; check.close(); };
  }, [event.conversation_id, event.message_id, event.reason, event.seq, event.task_id, invalid]);
  const reason = reasons[event.reason];
  const usageUrl = safeExternalUrl(manageUsageUrl);
  const taskAction = event.reason === "task_budget" || event.reason === "top_tier_approval";
  const label = event.reason === "task_budget" ? "Continue anyway" : event.reason === "top_tier_approval" ? "Allow top-tier model" : event.reason === "rate_limited" ? "I've raised my limit, resume" : undefined;
  async function resume() {
    if (mutation.notice || !confirmed || invalid || isSessionError(mutation.error)) return;
    if (event.reason === "rate_limited") {
      const result = await mutation.run(() => guarded(() => api.call("plan_limit_resume")));
      if (result) {
        const count = result.resumed_task_ids?.length ?? 0;
        mutation.setNotice(`Plan-limit pause cleared. ${count ? `${count} ${count === 1 ? "task" : "tasks"} resumed.` : "No tasks were resumed."}`);
      }
    } else if (taskAction && event.task_id) {
      const endpoint = event.reason === "task_budget" ? "task_continue" : "task_approve_top_tier";
      const result = await mutation.run(() => guarded(() => api.call(endpoint, { params: { task_id: event.task_id! } })));
      if (result) mutation.setNotice(result.message || `Task ${result.task_id}: ${result.state}.`);
    }
  }
  async function guarded<T>(action: () => Promise<T>): Promise<T> {
    try { return await action(); }
    catch (cause) { if (isStaleItemError(cause)) { setInvalid(true); setConfirmed(false); } throw cause; }
  }
  return <section role="alert" className="notice paused-banner"><h3>{reason.title}</h3><p>{event.message}</p><p>{reason.detail}</p>
    {event.resume_at && <p>Daemon-reported resume time: <time dateTime={event.resume_at}>{new Date(event.resume_at).toLocaleString()}</time></p>}
    {taskAction && !event.task_id && <p>The task ID was not supplied. This task cannot be resumed from this banner.</p>}
    {label && !confirmed && <p>OpenDot could not confirm that this task is still paused. Review its current state before continuing.</p>}
    <div className="actions">
      {event.reason === "rate_limited" && usageUrl ? <a className="button button--primary" href={usageUrl} target="_blank" rel="noreferrer">Manage usage</a> : <Link className={event.reason === "rate_limited" ? "button button--primary" : undefined} to={reason.path}>{reason.action}</Link>}
      {label && confirmed && !invalid && !isSessionError(mutation.error) && <Button variant={event.reason === "rate_limited" ? "secondary" : "primary"} loading={mutation.pending} disabled={!!mutation.notice} onClick={() => void resume()}>{label}</Button>}
    </div>
    {mutation.notice && <p role="status">{mutation.notice}</p>}
    {mutation.error != null && <ErrorState error={mutation.error} />}
    {verificationError != null && <ErrorState error={verificationError} />}
  </section>;
}
