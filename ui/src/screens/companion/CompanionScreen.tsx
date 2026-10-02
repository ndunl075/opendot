import { useRef, useState } from "react";
import { api } from "../../api/client";
import type { CompanionProfile, CompanionTaskList } from "../../api/types.gen";
import { Avatar, createAvatarSeed } from "../../design/avatar";
import { Badge, Button, Card, Dialog, EmptyState, ErrorState, Skeleton, Tabs } from "../../design/components";
import { CompanionEditor } from "./CompanionEditor";
import { ScreenHeading, useMutation, useResource } from "../shared";

const loadProfile = () => api.call("companion_get");
const loadTasks = () => api.call("companion_tasks");
const tabLabels: Record<keyof CompanionTaskList, string> = { in_progress: "In progress", scheduled: "Scheduled", completed: "Completed" };
const emptyLabels: Record<keyof CompanionTaskList, string> = { in_progress: "A little breathing room", scheduled: "Nothing scheduled", completed: "Completed work will appear here" };

function TaskList({ tasks, category }: { tasks: CompanionTaskList[keyof CompanionTaskList]; category: keyof CompanionTaskList }) {
  if (!tasks.length) return <EmptyState title={emptyLabels[category]} description="Start a conversation to give your companion something to work on." />;
  return <ul className="screen-stack" aria-label={`${tabLabels[category]} tasks`}>{tasks.map(task => <li key={task.id}><Card className="section-card form-stack"><h3>{task.title}</h3>{task.summary && <p>{task.summary}</p>}
    <div className="actions"><Badge>{task.job_type}</Badge><span className="muted">{task.credits_used === undefined ? "Credits not reported" : `${task.credits_used} credits`}</span></div>
    {task.scheduled_for && <p className="muted">Scheduled for <time dateTime={task.scheduled_for}>{new Date(task.scheduled_for).toLocaleString()}</time></p>}
    {task.completed_at && <p className="muted">Completed <time dateTime={task.completed_at}>{new Date(task.completed_at).toLocaleString()}</time></p>}
  </Card></li>)}</ul>;
}

export default function CompanionScreen() {
  const profile = useResource(loadProfile);
  const tasks = useResource(loadTasks);
  const mutation = useMutation();
  const [tab, setTab] = useState("in_progress");
  const [rename, setRename] = useState<"rename" | "customize" | null>(null);
  const [reset, setReset] = useState(false);
  const resetCancelRef = useRef<HTMLButtonElement>(null);
  async function update(action: () => Promise<CompanionProfile>) {
    const result = await mutation.run(action);
    if (result) { profile.setData(result); return true; }
    return false;
  }
  async function resetCompanion() {
    if (await update(() => api.call("companion_reset", { body: { confirm: "reset", forget_memory: true } }))) { setReset(false); void tasks.reload(); mutation.setNotice("Companion reset. Its tasks and learned memories have been cleared."); }
  }
  if (profile.loading) return <Skeleton className="screen-skeleton" label="Loading companion" />;
  if (profile.error) return <ErrorState error={profile.error} onRetry={profile.reload} />;
  if (!profile.data) return null;
  const companion = profile.data;
  return <section className="screen-stack"><ScreenHeading title="Companion" description="See what is underway, shape its identity, and take a pause whenever you need." />
    <Card className="section-card form-stack companion-profile"><div className="companion-profile-heading"><Avatar seed={companion.avatar_seed} label={`${companion.name}'s avatar`} /><div><h2>{companion.name}</h2><Badge tone={companion.paused ? "warning" : "success"}>{companion.paused ? "Paused" : "Ready to help"}</Badge></div></div>
      {companion.paused && <p className="notice" role="status">{companion.paused_reason ?? "Your companion is paused."} The agent loop and queued actions are on hold.</p>}
      <div className="actions"><Button loading={mutation.pending} onClick={() => void update(() => companion.paused ? api.call("companion_resume") : api.call("companion_pause", { body: { reason: "Paused by you" } }))}>{companion.paused ? "Resume" : "Pause"}</Button>
        <Button variant="secondary" disabled={mutation.pending} onClick={() => setRename("rename")}>Rename</Button>
        <Button variant="secondary" disabled={mutation.pending} onClick={() => setRename("customize")}>Customize</Button>
        <Button variant="secondary" disabled={mutation.pending} onClick={() => void update(() => api.call("companion_avatar", { body: { avatar_seed: createAvatarSeed() } }))}>Re-roll avatar</Button>
        <Button variant="danger" disabled={mutation.pending} onClick={() => setReset(true)}>Reset</Button>
      </div>
    </Card>
    {mutation.error != null && !rename && !reset && <ErrorState error={mutation.error} />}
    {mutation.notice && <p className="notice" role="status">{mutation.notice}</p>}
    {tasks.loading ? <Skeleton className="screen-skeleton" label="Loading companion tasks" /> : tasks.error ? <ErrorState error={tasks.error} onRetry={tasks.reload} /> : tasks.data && <Tabs label="Companion work" value={tab} onValueChange={setTab} items={(Object.keys(tabLabels) as (keyof CompanionTaskList)[]).map(category => ({ value: category, label: tabLabels[category], content: <TaskList category={category} tasks={tasks.data![category]} /> }))} />}
    {rename && <CompanionEditor companion={companion} renameOnOpen={rename === "rename"} onUpdated={profile.setData} onClose={() => setRename(null)} />}
    <Dialog title="Reset companion?" description="This clears the companion’s tasks, conversations, and learned memories. This cannot be undone." open={reset} onClose={() => { if (!mutation.pending) setReset(false); }} initialFocusRef={resetCancelRef}>
      <div className="form-stack">{mutation.error != null && <ErrorState error={mutation.error} />}<div className="actions"><Button ref={resetCancelRef} variant="secondary" disabled={mutation.pending} onClick={() => setReset(false)}>Cancel</Button><Button variant="danger" loading={mutation.pending} onClick={() => void resetCompanion()}>Reset companion</Button></div></div>
    </Dialog>
  </section>;
}
