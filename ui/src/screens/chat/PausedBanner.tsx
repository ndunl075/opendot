import { Link } from "react-router-dom";
import { api } from "../../api/client";
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
  const reason = reasons[event.reason];
  const usageUrl = safeExternalUrl(manageUsageUrl);
  const taskAction = event.reason === "task_budget" || event.reason === "top_tier_approval";
  const label = event.reason === "task_budget" ? "Continue anyway" : event.reason === "top_tier_approval" ? "Allow top-tier model" : event.reason === "rate_limited" ? "I've raised my limit, resume" : undefined;
  async function resume() {
    if (mutation.notice || (taskAction && !event.task_id)) return;
    if (event.reason === "rate_limited") {
      const result = await mutation.run(() => api.call("plan_limit_resume"));
      if (result) {
        const count = result.resumed_task_ids?.length ?? 0;
        mutation.setNotice(`Plan-limit pause cleared. ${count ? `${count} ${count === 1 ? "task" : "tasks"} resumed.` : "No tasks were resumed."}`);
      }
    } else if (taskAction && event.task_id) {
      const endpoint = event.reason === "task_budget" ? "task_continue" : "task_approve_top_tier";
      const result = await mutation.run(() => api.call(endpoint, { params: { task_id: event.task_id! } }));
      if (result) mutation.setNotice(result.message || `Task ${result.task_id}: ${result.state}.`);
    }
  }
  return <section role="alert" className="notice paused-banner"><h3>{reason.title}</h3><p>{event.message}</p><p>{reason.detail}</p>
    {event.resume_at && <p>Daemon-reported resume time: <time dateTime={event.resume_at}>{new Date(event.resume_at).toLocaleString()}</time></p>}
    {taskAction && !event.task_id && <p>The task ID was not supplied. This task cannot be resumed from this banner.</p>}
    <div className="actions">
      {label && <Button loading={mutation.pending} disabled={!!mutation.notice || (taskAction && !event.task_id)} onClick={() => void resume()}>{label}</Button>}
      {event.reason === "rate_limited" && usageUrl ? <a href={usageUrl} target="_blank" rel="noreferrer">Manage usage</a> : <Link to={reason.path}>{reason.action}</Link>}
    </div>
    {mutation.notice && <p role="status">{mutation.notice}</p>}
    {mutation.error != null && <ErrorState error={mutation.error} />}
  </section>;
}
