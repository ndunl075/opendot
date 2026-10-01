import { api } from "../../api/client";
import { Badge, Button, Card, EmptyState, ErrorState } from "../../design/components";
import { Resource, ScreenHeading, useMutation, useResource } from "../shared";

const loadActivity = () => api.call("activity_list", { body: { limit: 50 } });
export default function ActivityScreen() {
  const resource = useResource(loadActivity);
  const mutation = useMutation();
  async function older() {
    if (!resource.data?.next_cursor) return;
    const cursor = resource.data.next_cursor;
    const next = await mutation.run(() => api.call("activity_list", { body: { limit: 50, cursor } }));
    if (next) resource.setData(current => ({ ...next, items: [...(current?.items ?? []), ...next.items.filter(item => !current?.items.some(existing => existing.id === item.id))] }));
  }
  return <div className="screen-stack">
    <ScreenHeading title="Activity" description="What happened, why it happened, and the information behind each decision." action={<Button variant="secondary" onClick={() => void resource.reload()}>Refresh activity</Button>} />
    {!!mutation.error && <ErrorState error={mutation.error} onRetry={() => void older()} />}
    <Resource resource={resource}>{data => data.items.length ? <>
      <ol className="screen-stack" aria-label="Activity timeline">{data.items.map(item => <li key={item.id}><Card className="section-card" role="article" aria-label={item.title}>
        <div className="section-heading"><time dateTime={item.at}>{new Date(item.at).toLocaleString()}</time><Badge>{item.kind.replaceAll("_", " ")}</Badge></div>
        <h2>{item.title}</h2><p>{item.why}</p>
        {item.credits !== undefined && <p className="muted">{item.credits.toLocaleString()} estimated credits</p>}
        <div className="actions">{item.rule_id ? <a href={`/rules#${encodeURIComponent(item.rule_id)}`}>Rule: {item.rule_name ?? item.rule_id}</a> : <span className="muted">No rule recorded</span>}
          {item.memory_ids?.map(id => <a key={id} href={`/memory#${encodeURIComponent(id)}`}>Memory {id}</a>)}
        </div>
        {item.reviewer_note ? <details><summary>Reviewer note</summary><p>{item.reviewer_note}</p></details> : <p className="muted">No reviewer note recorded</p>}
        {!item.memory_ids?.length && <p className="muted">No memories recorded</p>}
      </Card></li>)}</ol>
      {data.next_cursor && <Button variant="secondary" loading={mutation.pending} onClick={() => void older()}>Load older activity</Button>}
    </> : <EmptyState title="Nothing recorded yet" description="Your companion's actions and the reasons behind them will appear here." />}</Resource>
  </div>;
}
