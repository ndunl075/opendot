import { useRef, useState } from "react";
import { api } from "../../api/client";
import type { Rule } from "../../api/types.gen";
import { Badge, Button, Card, Dialog, EmptyState, ErrorState } from "../../design/components";
import { Resource, ScreenHeading, useMutation, useResource } from "../shared";
import { RuleEditor, behaviors } from "./RuleEditor";

const loadRules = () => api.call("rules_list");
function DeleteRule({ rule, onClose, onDeleted }: { rule: Rule; onClose: () => void; onDeleted: () => Promise<void> }) {
  const mutation = useMutation();
  const cancel = useRef<HTMLButtonElement>(null);
  async function remove() {
    if (rule.locked || rule.core_deny) return;
    const result = await mutation.run(() => api.call("rule_delete", { params: { rule_id: rule.id } }));
    if (result) await onDeleted();
  }
  return <Dialog open onClose={onClose} initialFocusRef={cancel} title={`Delete ${rule.name}?`} description="This removes your custom rule. The daemon's default behavior and core safety rules will apply.">
    {!!mutation.error && <ErrorState error={mutation.error} />}
    <div className="actions"><Button ref={cancel} variant="secondary" onClick={onClose}>Cancel</Button><Button variant="danger" loading={mutation.pending} onClick={() => void remove()}>Delete rule</Button></div>
  </Dialog>;
}
export default function RulesScreen() {
  const resource = useResource(loadRules);
  const mutation = useMutation();
  const [editor, setEditor] = useState<Rule | "new" | null>(null);
  const [deleting, setDeleting] = useState<Rule | null>(null);
  return <div className="screen-stack">
    <ScreenHeading title="Rules" description="Decide when your companion can act and when it should ask." action={<Button onClick={() => setEditor("new")}>Create rule</Button>} />
    <p className="notice">Core safety rules always apply. Spending money, deleting data in other apps, and security or credential changes stay with you.</p>
    {mutation.notice && <p role="status">{mutation.notice}</p>}
    <Resource resource={resource}>{data => data.rules.length ? <div className="screen-stack">{data.rules.map(rule => <Card key={rule.id} id={rule.id} role="article" aria-label={rule.name} className="section-card">
      <div className="section-heading"><h2>{rule.name}</h2><Badge tone={rule.core_deny ? "warning" : "neutral"}>{rule.locked || rule.core_deny ? "Locked · core safety" : rule.enabled === false ? "Disabled" : "Active"}</Badge></div>
      {rule.core_deny
        ? <p><strong>Hand off to me</strong> — This action is prohibited for the companion. No rule or approval can permit it.</p>
        : <p><strong>{behaviors[rule.behavior].label}</strong> — {behaviors[rule.behavior].description}</p>}
      <p className="muted">Action: <code>{rule.action}</code></p>
      {rule.description && <p>{rule.description}</p>}
      {!rule.locked && !rule.core_deny && <div className="actions"><Button variant="secondary" aria-label={`Edit ${rule.name}`} onClick={() => setEditor(rule)}>Edit</Button><Button variant="ghost" aria-label={`Delete ${rule.name}`} onClick={() => setDeleting(rule)}>Delete</Button></div>}
    </Card>)}</div> : <EmptyState title="No rules yet" description="Create a rule to make your preferences explicit. Core safety restrictions still apply." />}</Resource>
    {editor && <RuleEditor rule={editor === "new" ? undefined : editor} onClose={() => setEditor(null)} onSaved={async () => { mutation.setNotice(editor === "new" ? "Rule created." : "Rule updated."); setEditor(null); await resource.reload(); }} />}
    {deleting && <DeleteRule rule={deleting} onClose={() => setDeleting(null)} onDeleted={async () => { setDeleting(null); mutation.setNotice("Rule deleted."); await resource.reload(); }} />}
  </div>;
}
