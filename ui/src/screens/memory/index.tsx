import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { MemoryForgetRequest, MemorySearchQuery, MemorySearchResult } from "../../api/types.gen";
import { Badge, Button, Card, Dialog, EmptyState, ErrorState, Input, Textarea } from "../../design/components";
import { Resource, ScreenHeading, useMutation, useResource } from "../shared";
import { ForgetMemories } from "./ForgetMemories";

type MemoryItem = MemorySearchResult["items"][number];
function linkedMemoryId() {
  try { return decodeURIComponent(window.location.hash.slice(1)); }
  catch { return ""; }
}
function CorrectMemory({ item, onClose, onSaved }: { item: MemoryItem; onClose: () => void; onSaved: () => Promise<void> }) {
  const [statement, setStatement] = useState(item.statement);
  const mutation = useMutation();
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!statement.trim()) return;
    const result = await mutation.run(() => api.call("memory_correct", { params: { memory_id: item.id }, body: { statement: statement.trim() } }));
    if (result) await onSaved();
  }
  return <Dialog open onClose={onClose} title="Correct memory" description="Your correction replaces this fact without rewriting its history.">
    <form className="form-stack" onSubmit={event => void save(event)}><Textarea label="Corrected memory" required value={statement} onChange={event => setStatement(event.target.value)} />
      {!!mutation.error && <ErrorState error={mutation.error} />}<div className="actions"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" loading={mutation.pending}>Save correction</Button></div>
    </form>
  </Dialog>;
}

export default function MemoryScreen() {
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [person, setPerson] = useState("");
  const [query, setQuery] = useState<MemorySearchQuery>({ q: "", limit: 100 });
  const loader = useCallback(() => api.call("memory_search", { body: query }), [query]);
  const resource = useResource(loader);
  const [correcting, setCorrecting] = useState<MemoryItem | null>(null);
  const [forgetting, setForgetting] = useState<MemoryForgetRequest | null>(null);
  const [notice, setNotice] = useState("");
  const target = linkedMemoryId();
  useEffect(() => {
    if (target && !resource.loading) document.getElementById(target)?.scrollIntoView?.({ block: "center" });
  }, [resource.loading, target]);
  function search(event: FormEvent) {
    event.preventDefault();
    setQuery({ q: q.trim(), limit: 100, ...(source.trim() ? { source: source.trim() } : {}), ...(person.trim() ? { person: person.trim() } : {}) });
  }
  return <div className="screen-stack">
    <ScreenHeading title="Memory" description="See what your companion remembers. Correct a fact or decide what to forget." action={<Button variant="secondary" onClick={() => setForgetting({ scope: "source" })}>Forget memories</Button>} />
    <Card className="section-card"><form className="form-stack" onSubmit={search}><Input label="Search memories" type="search" value={q} onChange={event => setQ(event.target.value)} hint="Keyword search. No paid embedding provider is required." />
      <div className="screen-grid"><Input label="Filter by source" value={source} onChange={event => setSource(event.target.value)} placeholder="For example, gmail" /><Input label="Filter by person" value={person} onChange={event => setPerson(event.target.value)} /></div><div className="actions"><Button type="submit">Search</Button></div>
    </form></Card>
    {notice && <p role="status">{notice}</p>}
    <Resource resource={resource}>{data => <>
      {target && !data.items.some(item => item.id === target) && <p className="notice" role="status">The linked memory is not in these results. It may have been forgotten or replaced. Search by its words, source, or person to narrow the results.</p>}
      {data.total > data.items.length && <p className="notice">Showing {data.items.length} of {data.total} memories. Narrow your search to find more specific results.</p>}
      {data.items.length ? <div className="screen-stack">{data.items.map(item => <Card key={item.id} id={item.id} role="article" aria-label={`Memory: ${item.statement}`} className="section-card">
        <div className="section-heading"><Badge tone={item.confidence === "confirmed" ? "success" : "warning"}>{item.confidence === "confirmed" ? "Confirmed" : "Inferred · not confirmed"}</Badge><span className="muted">{item.source_label} · Source: {item.source}</span></div>
        <h2>{item.statement}</h2><p className="muted">Learned <time dateTime={item.learned_at}>{new Date(item.learned_at).toLocaleDateString()}</time>{item.people?.length ? ` · ${item.people.join(", ")}` : ""}</p>
        {item.superseded && <p className="muted">This memory has been replaced by a correction.</p>}
        <div className="actions"><Button variant="secondary" onClick={() => setCorrecting(item)}>Correct memory</Button><Button variant="ghost" onClick={() => setForgetting({ scope: "item", item_id: item.id })}>Forget memory</Button></div>
      </Card>)}</div> : <EmptyState title="No memories found" description="Try different keywords or filters. Memories appear here as your companion learns from your conversations and connected apps." />}
    </>}</Resource>
    {correcting && <CorrectMemory item={correcting} onClose={() => setCorrecting(null)} onSaved={async () => { setCorrecting(null); setNotice("Memory corrected."); await resource.reload(); }} />}
    {forgetting && <ForgetMemories initial={forgetting} onClose={() => setForgetting(null)} onSaved={async count => { setForgetting(null); setNotice(`Forgot ${count} ${count === 1 ? "memory" : "memories"}.`); await resource.reload(); }} />}
  </div>;
}
