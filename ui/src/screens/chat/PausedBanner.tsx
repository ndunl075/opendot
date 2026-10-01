import { Link } from "react-router-dom";
import type { PausedEvent } from "../../api/types.gen";
import { safeExternalUrl } from "../shared";

const reasons: Record<PausedEvent["reason"], { title: string; detail: string; path: string; action: string }> = {
  task_budget: { title: "Task budget reached", detail: "Review the task and adjust its budget in Usage. Continuing this paused task is not available in this version yet.", path: "/usage", action: "Review budgets" },
  daily_budget: { title: "Daily budget reached", detail: "All plan requests are paused. Wait until the next day or raise the daily budget in Usage.", path: "/usage", action: "Review daily budget" },
  rate_limited: { title: "ChatGPT plan limit reached", detail: "Check your OpenDot weekly limit in ChatGPT Settings, Usage. Keep credit use off. A reset time has not been assumed.", path: "/usage", action: "Manage usage" },
  top_tier_approval: { title: "Top-tier approval needed", detail: "This task needs permission to use a more expensive model. One-time task continuation is not available in this version yet. Settings controls automatic top-tier use for future work.", path: "/settings", action: "Review model settings" },
  anomaly: { title: "Paused for unusual activity", detail: "Review what happened in Activity before resuming your companion from its profile.", path: "/activity", action: "Review activity" },
  user: { title: "Paused by you", detail: "Your companion is holding its work. Resume it from the Companion screen when you are ready.", path: "/companion", action: "Open companion" },
};
export function PausedBanner({ event, manageUsageUrl }: { event: PausedEvent; manageUsageUrl?: string }) {
  const reason = reasons[event.reason];
  const usageUrl = safeExternalUrl(manageUsageUrl);
  return <section role="alert" className="notice paused-banner"><h3>{reason.title}</h3><p>{event.message}</p><p>{reason.detail}</p>
    {event.resume_at && <p>Daemon-reported resume time: <time dateTime={event.resume_at}>{new Date(event.resume_at).toLocaleString()}</time></p>}
    {event.reason === "rate_limited" && usageUrl ? <a href={usageUrl} target="_blank" rel="noreferrer">Manage usage</a> : <Link to={reason.path}>{reason.action}</Link>}
  </section>;
}
