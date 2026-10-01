import { useState } from "react";
import { Link } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import { api, isSessionError, isStaleItemError } from "../../api/client";
import type { ApprovalItem, ApprovalDecisionResult, ApprovalAlwaysAllowRequest } from "../../api/types.gen";
import { Badge, Button, Card, Dialog, ErrorState, Select, Textarea } from "../../design/components";
import { useMutation } from "../shared";
import { ReviewerNote } from "../ReviewerNote";

export function ApprovalCard({ approval, onDecision }: { approval: ApprovalItem; onDecision: (result: ApprovalDecisionResult) => void }) {
  const [mode, setMode] = useState<"edit" | "allow" | "confirm" | null>(null);
  const [payload, setPayload] = useState(approval.payload);
  const [behavior, setBehavior] = useState<ApprovalAlwaysAllowRequest["behavior"]>("auto_if_preapproved");
  const [result, setResult] = useState<ApprovalDecisionResult>();
  const [refreshed, setRefreshed] = useState<ApprovalItem>();
  const [invalid, setInvalid] = useState(false);
  const mutation = useMutation();
  const current = refreshed ?? result?.approval ?? approval;
  const actionable = current.status === "pending" && !invalid && !isSessionError(mutation.error);
  const params = { approval_id: approval.id };
  async function decide(action: () => Promise<ApprovalDecisionResult>) {
    if (!actionable) return;
    const next = await mutation.run(async () => {
      try { return await action(); }
      catch (cause) {
        if (isStaleItemError(cause)) {
          setInvalid(true); setMode(null);
          try {
            const item = await api.call("approval_get", { params });
            setRefreshed(item); onDecision({ approval: item, executed: false });
          } catch { /* Leave invalid actions hidden if the refresh also fails. */ }
        }
        throw cause;
      }
    });
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
    {actionable && <div className="actions">
      <Button disabled={mutation.pending} onClick={() => current.review_verdict === "block" ? setMode("confirm") : void decide(() => api.call("approval_approve", { params, body: {} }))}>Approve</Button>
      <Button variant="secondary" disabled={mutation.pending} onClick={() => { setPayload(current.payload); setMode("edit"); }}>Edit</Button>
      <Button variant="secondary" disabled={mutation.pending} onClick={() => void decide(() => api.call("approval_deny", { params, body: {} }))}>Deny</Button>
      <Button variant="ghost" disabled={mutation.pending} onClick={() => setMode("allow")}>Always allow this</Button>
    </div>}
    <Dialog open={mode === "edit" && actionable} onClose={() => { if (!mutation.pending) setMode(null); }} title="Edit proposed action" description="Review each field. Saving an edit does not approve the action.">
      <form className="form-stack" onSubmit={event => { event.preventDefault(); void decide(() => api.call("approval_edit", { params, body: { payload, approve: false } })); }}>
        {Object.entries(payload).map(([key, value]) => <Textarea key={key} label={key} value={value} onChange={event => setPayload({ ...payload, [key]: event.target.value })} />)}
        {mutation.error != null && <ErrorState error={mutation.error} />}
        <div className="actions"><Button variant="secondary" onClick={() => setMode(null)} disabled={mutation.pending}>Cancel</Button><Button type="submit" loading={mutation.pending}>Save edit</Button></div>
      </form>
    </Dialog>
    <Dialog open={mode === "confirm" && actionable} onClose={() => { if (!mutation.pending) setMode(null); }} title="Approve despite the reviewer’s warning?" description="The reviewer recommended blocking this action. Review the note before approving.">
      <blockquote>{current.review_note || "The reviewer marked this action as blocked without a note."}</blockquote>
      <p>{current.action}: {current.preview}</p>
      <div className="actions"><Button variant="secondary" disabled={mutation.pending} onClick={() => setMode(null)}>Cancel</Button><Button loading={mutation.pending} onClick={() => void decide(() => api.call("approval_approve", { params, body: {} }))}>Approve despite review</Button></div>
    </Dialog>
    <Dialog open={mode === "allow" && actionable} onClose={() => { if (!mutation.pending) setMode(null); }} title="Always allow this action?" description="Approve this action now and allow it automatically next time">
      <p>Action: {current.action}</p><p>Target and action preview: {current.preview}</p>
      {Object.entries(current.payload).map(([key, value]) => <p key={key}>{key}: {value}</p>)}
      {current.review_verdict === "block" && <blockquote>{current.review_note || "The reviewer marked this action as blocked without a note."}</blockquote>}
      <div className="form-stack"><Select label="Rule behavior" value={behavior} onChange={event => setBehavior(event.target.value as ApprovalAlwaysAllowRequest["behavior"])}>
        <option value="auto_if_preapproved">Only when I explicitly request this action</option><option value="auto">Automatically, without asking</option>
      </Select><p>The rule matches this action type. Review its scope in Rules after saving.</p>
        {mutation.error != null && <ErrorState error={mutation.error} />}
        <div className="actions"><Button variant="secondary" disabled={mutation.pending} onClick={() => setMode(null)}>Cancel</Button><Button loading={mutation.pending} onClick={() => void decide(() => api.call("approval_always_allow", { params, body: { behavior } }))}>Approve and create rule</Button></div>
      </div>
    </Dialog>
  </Card>;
}
