import { useRef, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { MemoryForgetRequest } from "../../api/types.gen";
import { Button, Dialog, ErrorState, Input, Select } from "../../design/components";
import { useMutation } from "../shared";

export function ForgetMemories({ initial, onClose, onSaved }: { initial: MemoryForgetRequest; onClose: () => void; onSaved: (count: number) => Promise<void> }) {
  const [scope, setScope] = useState(initial.scope);
  const [source, setSource] = useState("");
  const [person, setPerson] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [review, setReview] = useState<MemoryForgetRequest | null>(initial.scope === "item" ? initial : null);
  const [validation, setValidation] = useState("");
  const cancel = useRef<HTMLButtonElement>(null);
  const mutation = useMutation();
  function prepare(event: FormEvent) {
    event.preventDefault(); setValidation("");
    if (scope === "time") {
      if (!since || !until || new Date(since) > new Date(until)) { setValidation("Choose a start and end time, with the end after the start."); return; }
      setReview({ scope, since: new Date(since).toISOString(), until: new Date(until).toISOString() });
    } else if (scope === "source" && source.trim()) setReview({ scope, source: source.trim() });
    else if (scope === "person" && person.trim()) setReview({ scope, person: person.trim() });
  }
  async function forget() {
    if (!review) return;
    const result = await mutation.run(() => api.call("memory_forget", { body: review }));
    if (result) await onSaved(result.forgotten_count);
  }
  const detail = review?.scope === "item" ? "this memory" : review?.scope === "source" ? `all memories from source “${review.source}”` : review?.scope === "person" ? `all memories about “${review.person}”` : `all memories learned from ${since.replace("T", " ")} to ${until.replace("T", " ")} (your local time)`;
  return <Dialog key={review ? "confirm" : "scope"} open onClose={onClose} title={review ? "Confirm forgetting" : "Forget memories"} initialFocusRef={review ? cancel : undefined} description={review ? `Permanently forget ${detail}? This cannot be undone.` : "Choose the memories you want to remove. You will review the request before anything is forgotten."}>
    {review ? <div className="form-stack">{!!mutation.error && <ErrorState error={mutation.error} />}<div className="actions"><Button ref={cancel} variant="secondary" onClick={onClose}>Cancel</Button><Button variant="danger" loading={mutation.pending} onClick={() => void forget()}>Confirm forget</Button></div></div> :
      <form className="form-stack" onSubmit={prepare}><Select label="Forget by" value={scope} onChange={event => setScope(event.target.value as MemoryForgetRequest["scope"])}><option value="source">Source</option><option value="time">Time range</option><option value="person">Person</option></Select>
        {scope === "source" && <Input label="Source to forget" required value={source} onChange={event => setSource(event.target.value)} hint="Use the source identifier, such as gmail, calendar, github, or chat." />}
        {scope === "person" && <Input label="Person to forget" required value={person} onChange={event => setPerson(event.target.value)} />}
        {scope === "time" && <><Input label="Forget from" type="datetime-local" required value={since} onChange={event => setSince(event.target.value)} /><Input label="Forget until" type="datetime-local" required min={since || undefined} value={until} onChange={event => setUntil(event.target.value)} /></>}
        {validation && <p role="alert">{validation}</p>}<div className="actions"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit">Review forget request</Button></div>
      </form>}
  </Dialog>;
}
