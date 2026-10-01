import type { ApprovalItem } from "../api/types.gen";
import { Badge } from "../design/components";

export function ReviewerNote({ review_note, review_verdict }: Pick<ApprovalItem, "review_note" | "review_verdict">) {
  if (!review_note && !review_verdict) return null;
  const tone = review_verdict === "block" ? "danger" : review_verdict === "concern" ? "warning" : "success";
  return <section aria-label="Reviewer note" className={`reviewer-note${review_verdict === "block" ? " reviewer-note--block" : ""}`}>
    <div className="section-heading"><h3>Reviewer note</h3>{review_verdict && <Badge tone={tone}>Review: {review_verdict}</Badge>}</div>
    {review_note && <p>{review_note}</p>}
  </section>;
}
