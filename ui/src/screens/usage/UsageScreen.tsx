import { useState } from "react";
import { api } from "../../api/client";
import type { UsageBudgets, UsageSummary } from "../../api/types.gen";
import { Badge, Button, Card, EmptyState, ErrorState, Input } from "../../design/components";
import { Resource, ScreenHeading, safeExternalUrl, useMutation, useResource } from "../shared";

const loadUsage = () => api.call("usage_get", { body: { days: 7 } });

function Budgets({ budgets, onSaved }: { budgets: UsageBudgets; onSaved: (value: UsageBudgets) => void }) {
  const [task, setTask] = useState(budgets.task_credits?.toString() ?? "");
  const [daily, setDaily] = useState(budgets.daily_credits?.toString() ?? "");
  const mutation = useMutation();
  return <Card className="section-card"><h2>Usage budgets</h2><p className="muted">A task pauses at its limit. The daily budget stops all plan requests until the next day or until you raise it. Paid fallbacks never turn on automatically.</p>
    <form className="form-stack" onSubmit={async event => {
      event.preventDefault();
      const result = await mutation.run(() => api.call("usage_budgets_set", { body: { task_credits: Number(task), daily_credits: Number(daily), daily_hard_stop: true } }));
      if (result) { onSaved(result); mutation.setNotice("Budgets saved."); }
    }}>
      <div className="screen-grid"><Input label="Per-task budget (credits)" type="number" required min="0" step="any" value={task} onChange={event => setTask(event.target.value)} hint="Estimated credits for each task." />
        <Input label="Daily budget (credits)" type="number" required min="0" step="any" value={daily} onChange={event => setDaily(event.target.value)} hint="A hard stop for all plan requests." /></div>
      <div className="actions"><Button type="submit" loading={mutation.pending}>Save budgets</Button><Badge>Daily hard stop always on</Badge></div>
      {!!mutation.error && <ErrorState error={mutation.error} />}{mutation.notice && <p role="status">{mutation.notice}</p>}
    </form></Card>;
}

function Breakdown({ usage }: { usage: UsageSummary }) {
  return <div className="screen-grid">
    <Card className="section-card"><h2>By task</h2>{usage.by_task.length ? <table className="data-table"><caption className="sr-only">Credits by task</caption><thead><tr><th scope="col">Task</th><th scope="col">Credits</th></tr></thead><tbody>{usage.by_task.map(item => <tr key={item.task_id}><th scope="row">{item.title}</th><td>{item.credits.toLocaleString()}</td></tr>)}</tbody></table> : <EmptyState title="No task usage yet" description="Tasks will appear after their first model reply." />}</Card>
    <Card className="section-card"><h2>By day</h2>{usage.by_day.length ? <table className="data-table"><caption className="sr-only">Credits by day</caption><thead><tr><th scope="col">Day</th><th scope="col">Credits</th></tr></thead><tbody>{usage.by_day.map(item => <tr key={item.day}><th scope="row">{item.day}</th><td>{item.credits.toLocaleString()}</td></tr>)}</tbody></table> : <EmptyState title="No daily usage yet" description="Daily totals will appear here when reported." />}</Card>
    <Card className="section-card"><h2>By job type</h2>{usage.by_job_type.length ? <table className="data-table"><caption className="sr-only">Credits by job type</caption><thead><tr><th scope="col">Job type</th><th scope="col">Credits</th></tr></thead><tbody>{usage.by_job_type.map(item => <tr key={item.job_type}><th scope="row">{item.job_type}</th><td>{item.credits.toLocaleString()}</td></tr>)}</tbody></table> : <EmptyState title="No job usage yet" description="Job totals will appear here when reported." />}</Card>
  </div>;
}

export default function UsageScreen() {
  const resource = useResource(loadUsage);
  return <div className="screen-stack"><ScreenHeading title="Usage" description="See where your plan goes, and decide where it stops." />
    <Resource resource={resource}>{usage => <>
      <Card className="section-card"><div className="section-heading"><div><h2>{usage.total_credits.toLocaleString()} estimated credits</h2><p className="muted">Last {usage.days} days · {usage.today_credits.toLocaleString()} today</p></div><a className="button button--secondary" href={safeExternalUrl(usage.manage_usage_url)} target="_blank" rel="noreferrer">Manage usage</a></div>
        {/* Official OpenAI DevKit assets may be placed here when supplied under their license. */}
        <Badge>Using ChatGPT plan</Badge><p>{usage.plan_label}</p><p className="muted">Daily budget remaining: {usage.daily_budget_remaining == null ? "Not reported" : `${usage.daily_budget_remaining.toLocaleString()} credits`}. Keep credit use off in ChatGPT Settings, Usage.</p>
        {usage.paused_for_budget && <p role="status" className="notice">Plan requests are paused for the daily budget. Wait until the next day, or raise your daily budget below.</p>}
      </Card>
      <Budgets budgets={usage.budgets} onSaved={budgets => { resource.setData(current => current ? { ...current, budgets } : current); void resource.reload(); }} />
      <Breakdown usage={usage} />
    </>}</Resource>
  </div>;
}
