import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { Rule, RuleCreateRequest } from "../../api/types.gen";
import { Button, Checkbox, Dialog, ErrorState, Input, Select, Textarea } from "../../design/components";
import { useMutation } from "../shared";

export const behaviors: Record<Rule["behavior"], { label: string; description: string }> = {
  auto: { label: "Automatic", description: "Do this without asking, subject to core safety rules." },
  auto_if_preapproved: { label: "Only when preapproved", description: "Act only when you explicitly asked for this exact action in your own message." },
  ask: { label: "Ask first", description: "Prepare a proposal with a reviewer note and wait for your approval." },
  handoff: { label: "Hand off to me", description: "Explain what you need to do yourself. The companion does not act." },
};

export function RuleEditor({ rule, onClose, onSaved }: { rule?: Rule; onClose: () => void; onSaved: () => Promise<void> }) {
  const [name, setName] = useState(rule?.name ?? "");
  const [action, setAction] = useState(rule?.action ?? "");
  const [description, setDescription] = useState(rule?.description ?? "");
  const [behavior, setBehavior] = useState<Rule["behavior"]>(rule?.behavior ?? "ask");
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);
  const mutation = useMutation();
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !action.trim() || rule?.locked || rule?.core_deny) return;
    const body: RuleCreateRequest = { name: name.trim(), action: action.trim(), description, behavior };
    const result = await mutation.run(() => rule
      ? api.call("rule_update", { params: { rule_id: rule.id }, body: { name: body.name, description, behavior, enabled } })
      : api.call("rule_create", { body }));
    if (result) await onSaved();
  }
  return <Dialog open onClose={onClose} title={rule ? "Edit rule" : "Create rule"} description="Rules match an action. Core safety restrictions cannot be overridden.">
    <form className="form-stack" onSubmit={event => void save(event)}>
      <Input label="Rule name" required maxLength={80} value={name} onChange={event => setName(event.target.value)} />
      <Input label="Action" required value={action} disabled={!!rule} hint={rule ? "The action cannot be changed after creation." : "Use the action identifier shown in an approval or existing rule, such as gmail.draft."} onChange={event => setAction(event.target.value)} />
      <Select label="Behavior" value={behavior} onChange={event => setBehavior(event.target.value as Rule["behavior"])} hint={behaviors[behavior].description}>{Object.entries(behaviors).map(([value, item]) => <option key={value} value={value}>{item.label} ({value})</option>)}</Select>
      <Textarea label="Description" value={description} onChange={event => setDescription(event.target.value)} />
      {rule && <Checkbox label="Rule enabled" checked={enabled} onChange={event => setEnabled(event.target.checked)} />}
      {!!mutation.error && <ErrorState error={mutation.error} />}
      <div className="actions"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" loading={mutation.pending}>Save rule</Button></div>
    </form>
  </Dialog>;
}
