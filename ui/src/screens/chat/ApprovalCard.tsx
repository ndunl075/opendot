import { useState } from "react";
import { Link } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { api } from "../../api/client";
import type { ApprovalItem, ApprovalDecisionResult, ApprovalAlwaysAllowRequest } from "../../api/types.gen";
import { Badge, Button, Card, Dialog, ErrorState, Select, Textarea } from "../../design/components";
import { useMutation } from "../shared";
import { ReviewerNote } from "../ReviewerNote";

export function ApprovalCard({ approval, onDecision }: { approval: ApprovalItem; onDecision: (result: ApprovalDecisionResult) => void }) {
  const [mode, setMode] = useState<"edit" | "allow" | null>(null);
  const [payload, setPayload] = useState(approval.payload);
  const [behavior, setBehavior] = useState<ApprovalAlwaysAllowRequest["behavior"]>("auto_if_preapproved");
  const [result, setResult] = useState<ApprovalDecisionResult>();
  const mutation = useMutation();
  const current = result?.approval ?? approval;
  const params = { approval_id: approval.id };
  async function decide(action: () => Promise<ApprovalDecisionResult>) {
    const next = await mutation.run(action);
    if (next) { setResult(next); setMode(null); onDecision(next); }
  }
  return <Card className="approval-card screen-stack" aria-label={`Approval: ${approval.title}`}>
    <div className="section-heading"><h3><ShieldCheck size={18} aria-hidden="true" />{approval.title}</h3><Badge tone={current.status === "pending" ? "warning" : "neutral"}>{current.status === "pending" ? "Needs your approval" : current.status}</Badge></div>
    <p className="muted">{approval.action}</p>
    <div className="approval-preview" aria-label="Action preview">{current.preview}</div>
    <ReviewerNote review_note={current.review_note} review_verdict={current.review_verdict} />
    {approval.rule_suggestion && <p className="muted">{approval.rule_suggestion}</p>}
    {result && <p role="status">{result.result_summary ?? (result.executed ? "Action executed." : "Decision saved. The action has not been reported as executed.")}</p>}
    {result?.created_rule_id && <Link to={`/rules#${encodeURIComponent(result.created_rule_id)}`}>View created rule</Link>}
    {mutation.error != null && <ErrorState error={mutation.error} />}
    {["pending", "edited"].includes(current.status) && <div className="actions">
      <Button disabled={mutation.pending} onClick={() => void decide(() => api.call("approval_approve", { params, body: {} }))}>Approve</Button>
      <Button variant="secondary" disabled={mutation.pending} onClick={() => { setPayload(current.payload); setMode("edit"); }}>Edit</Button>
      <Button variant="secondary" disabled={mutation.pending} onClick={() => void decide(() => api.call("approval_deny", { params, body: {} }))}>Deny</Button>
      <Button variant="ghost" disabled={mutation.pending} onClick={() => setMode("allow")}>Always allow this</Button>
    </div>}
    <Dialog open={mode === "edit"} onClose={() => { if (!mutation.pending) setMode(null); }} title="Edit proposed action" description="Review each field. Saving an edit does not approve the action.">
      <form className="form-stack" onSubmit={event => { event.preventDefault(); void decide(() => api.call("approval_edit", { params, body: { payload, approve: false } })); }}>
        {Object.entries(payload).map(([key, value]) => <Textarea key={key} label={key} value={value} onChange={event => setPayload({ ...payload, [key]: event.target.value })} />)}
        {mutation.error != null && <ErrorState error={mutation.error} />}
        <div className="actions"><Button variant="secondary" onClick={() => setMode(null)} disabled={mutation.pending}>Cancel</Button><Button type="submit" loading={mutation.pending}>Save edit</Button></div>
      </form>
    </Dialog>
    <Dialog open={mode === "allow"} onClose={() => { if (!mutation.pending) setMode(null); }} title="Always allow this action?" description="This creates a rule for future matching actions. Core safety restrictions still apply.">
      <div className="form-stack"><Select label="Rule behavior" value={behavior} onChange={event => setBehavior(event.target.value as ApprovalAlwaysAllowRequest["behavior"])}>
        <option value="auto_if_preapproved">Only when I explicitly request this action</option><option value="auto">Automatically, without asking</option>
      </Select><p>The rule matches this action type. Review its scope in Rules after saving.</p>
        {mutation.error != null && <ErrorState error={mutation.error} />}
        <div className="actions"><Button variant="secondary" disabled={mutation.pending} onClick={() => setMode(null)}>Cancel</Button><Button loading={mutation.pending} onClick={() => void decide(() => api.call("approval_always_allow", { params, body: { behavior } }))}>Create rule</Button></div>
      </div>
    </Dialog>
  </Card>;
}
