import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import type { ChatGPTSignInStart, OnboardingState } from "../../api/types.gen";
import { Avatar, DEFAULT_AVATAR, encodeAvatarSeed } from "../../design/avatar";
import { AvatarPicker } from "../../design/AvatarPicker";
import { Badge, Button, Card, Checkbox, ErrorState, Input, Skeleton } from "../../design/components";
import { ConnectionPicker } from "../connections/ConnectionPicker";
import { safeExternalUrl, ScreenHeading, useMutation, useResource } from "../shared";

const loadOnboarding = () => api.call("onboarding_get");

function CompanionSetup({ state, onSave, pending }: { state: OnboardingState; onSave: (name: string, seed: string) => void; pending: boolean }) {
  const [name, setName] = useState(state.companion_name ?? "");
  const [seed, setSeed] = useState(state.avatar_seed ?? encodeAvatarSeed(DEFAULT_AVATAR));
  function submit(event: FormEvent) { event.preventDefault(); if (name.trim()) onSave(name.trim(), seed); }
  return <form className="form-stack onboarding-identity" onSubmit={submit}>
    <h2>Meet your companion</h2><p>Give it a name and a look of its own. You can change both later.</p>
    <Input label="Companion name" value={name} required maxLength={40} disabled={pending} autoComplete="off" onChange={event => setName(event.target.value)} />
    <div className="identity-avatar"><Avatar seed={seed} size={144} label="Selected companion avatar" /></div>
    <AvatarPicker seed={seed} onChange={setSeed} disabled={pending} />
    <div className="actions"><Button type="submit" loading={pending} disabled={!name.trim()}>Save companion</Button></div>
  </form>;
}

function UsageAcknowledgement({ state, onAcknowledge, onCheck, pending }: { state: OnboardingState; onAcknowledge: () => void; onCheck: () => void; pending: boolean }) {
  const [weekly, setWeekly] = useState(false);
  const [creditsOff, setCreditsOff] = useState(false);
  const creditsEnabled = state.chatgpt.credits_enabled === true;
  const creditsUnknown = state.chatgpt.credits_enabled === null;
  return <div className="form-stack"><h2>Keep usage in your hands</h2>
    <Badge tone="accent">Using ChatGPT plan</Badge>
    <p>In ChatGPT Settings, Usage, set a weekly limit for OpenDot and keep credit use off. ChatGPT credits cost real money. OpenDot does not turn them on or switch to a paid provider.</p>
    {creditsEnabled && <p className="notice" role="alert">Your account reports credit use is on. Turn it off in ChatGPT Settings before continuing.</p>}
    {creditsUnknown && <p className="notice" role="alert">OpenDot can't check this setting. In ChatGPT Settings, Usage, make sure credit use is off.</p>}
    {safeExternalUrl(state.chatgpt.manage_usage_url) && <a href={safeExternalUrl(state.chatgpt.manage_usage_url)} target="_blank" rel="noreferrer">Manage usage</a>}
    <Checkbox label="I set a weekly OpenDot limit" checked={weekly} onChange={event => setWeekly(event.target.checked)} />
    <Checkbox label="I kept ChatGPT credit use off" disabled={creditsEnabled || pending} checked={!creditsEnabled && creditsOff} onChange={event => setCreditsOff(event.target.checked)} />
    {creditsEnabled && <Button variant="secondary" loading={pending} onClick={() => { setCreditsOff(false); onCheck(); }}>Check again</Button>}
    <Button loading={pending} disabled={!weekly || !creditsOff || creditsEnabled} onClick={onAcknowledge}>Acknowledge usage settings</Button>
  </div>;
}

export default function OnboardingScreen({ onComplete }: { onComplete?: () => void }) {
  const resource = useResource(loadOnboarding);
  const mutation = useMutation();
  const [started, setStarted] = useState<ChatGPTSignInStart>();
  const [pollError, setPollError] = useState<unknown>();
  const [polling, setPolling] = useState(false);
  const [expired, setExpired] = useState(false);
  const data = resource.data;
  const setData = resource.setData;
  const pendingSignIn = data?.chatgpt.state === "pending";

  useEffect(() => {
    if (!polling && !pendingSignIn) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (started && Date.parse(started.expires_at) <= Date.now()) { setExpired(true); setPolling(false); return; }
      try {
        const status = await api.call("chatgpt_status");
        if (!alive) return;
        setData(previous => previous ? { ...previous, chatgpt: status, current_step: status.state === "signed_in" && status.eligible && (status.plan === "eligible_plus" || status.plan === "eligible_pro") ? "weekly_limit" : previous.current_step } : previous);
        if (status.state === "signed_in" || status.state === "error") { setPolling(false); return; }
        timer = setTimeout(() => void poll(), Math.max(1, started?.poll_interval_seconds ?? 2) * 1000);
      } catch (error) { if (alive) { setPollError(error); setPolling(false); } }
    }
    void poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [polling, pendingSignIn, started, setData]);

  async function startSignIn() {
    // Reserve the tab during the click, before the request loses user activation.
    let tab: Window | null = null;
    try { tab = window.open("about:blank", "_blank"); } catch { /* The visible link remains the fallback. */ }
    setExpired(false); setPollError(undefined);
    const result = await mutation.run(() => api.call("chatgpt_start", { body: { open_browser: true } }));
    const url = safeExternalUrl(result?.authorize_url);
    if (result) { setStarted(result); setPolling(Boolean(url)); }
    if (tab) {
      if (url) {
        try { tab.location.href = url; tab.opener = null; }
        catch { tab.close(); }
      } else tab.close();
    }
  }
  async function saveCompanion(name: string, avatar_seed: string) {
    const result = await mutation.run(() => api.call("onboarding_companion", { body: { name, avatar_seed } }));
    if (result) setData(result);
  }
  async function acknowledge() {
    if (data?.chatgpt.credits_enabled === true) return;
    const result = await mutation.run(() => api.call("onboarding_acknowledge", { body: { weekly_limit_set: true, credits_off: true } }));
    if (result) setData(result);
  }
  async function checkCredits() {
    const status = await mutation.run(() => api.call("chatgpt_status"));
    if (status) setData(previous => previous ? { ...previous, chatgpt: status } : previous);
  }
  async function finish(openChat = false) {
    const result = await mutation.run(() => api.call("onboarding_complete"));
    if (result) { setData(result); if (openChat && (result.current_step === "done" || result.completed_steps.includes("done"))) onComplete?.(); }
  }

  if (resource.loading) return <Skeleton className="screen-skeleton" label="Loading onboarding" />;
  if (resource.error) return <ErrorState error={resource.error} onRetry={resource.reload} />;
  if (!data) return null;
  const ineligible = data.chatgpt.state === "signed_in" && (!data.chatgpt.eligible || !["eligible_plus", "eligible_pro"].includes(data.chatgpt.plan));
  const step = data.current_step;
  return <section className="screen-stack onboarding-screen"><ScreenHeading title="Welcome to OpenDot" description="A companion on your computer, with you in control." />
    <Card className="section-card form-stack">
      {mutation.error != null && <ErrorState error={mutation.error} />}
      {ineligible ? <div className="form-stack"><h2>ChatGPT Plus or Pro is required</h2><p role="alert">This account cannot share its ChatGPT plan with OpenDot. Free and Go accounts are not supported. Setup cannot continue.</p>{data.chatgpt.ineligible_reason && <p>{data.chatgpt.ineligible_reason}</p>}<Button onClick={() => void startSignIn()} loading={mutation.pending || polling}>Continue with ChatGPT</Button></div> : <>
        {step === "companion" && <CompanionSetup state={data} onSave={(name, seed) => void saveCompanion(name, seed)} pending={mutation.pending} />}
        {step === "chatgpt" && <div className="form-stack"><h2>Connect your ChatGPT plan</h2><p>OpenDot works with ChatGPT Plus or Pro. Sign-in happens in your browser on this computer.</p>
          {/* Official OpenAI DevKit assets are not in this repository. Use text only until licensed official assets are supplied. */}
          <Button onClick={() => void startSignIn()} loading={mutation.pending || polling}>Continue with ChatGPT</Button>
          {polling && <p role="status">Waiting for ChatGPT sign-in. Complete the browser step to continue.</p>}
          {started && safeExternalUrl(started.authorize_url) && <a href={safeExternalUrl(started.authorize_url)} target="_blank" rel="noreferrer">Sign-in tab didn't open? Open ChatGPT sign-in</a>}
          {expired && <p role="alert">This sign-in expired. Select Continue with ChatGPT to try again.</p>}
          {data.chatgpt.error && <p role="alert">{data.chatgpt.error}</p>}
        </div>}
        {step === "weekly_limit" && <UsageAcknowledgement state={data} onAcknowledge={() => void acknowledge()} onCheck={() => void checkCredits()} pending={mutation.pending} />}
        {step === "connections" && <div className="form-stack"><h2>Connect your apps</h2><ConnectionPicker githubOptional onRefresh={() => void resource.reload()} /><p className="muted">You can add or manage read-only accounts in Connections at any time.</p><div className="actions"><Button onClick={() => void finish()} loading={mutation.pending}>Continue to introduction</Button><Button variant="secondary" loading={mutation.pending} onClick={() => void finish()}>Skip for now</Button></div></div>}
        {(step === "intro" || step === "done") && <div className="form-stack"><Avatar seed={data.avatar_seed ?? "open-fold-1"} /><h2>Hello, I’m {data.companion_name ?? "your companion"}</h2><p>{data.intro_message ?? "I can help you keep track of work, remember what matters, and ask before taking action. You can pause me at any time."}</p><Button loading={mutation.pending} onClick={() => { if (step === "done" || data.completed_steps.includes("done")) onComplete?.(); else void finish(true); }}>Open chat</Button></div>}
      </>}
      {pollError != null && <ErrorState error={pollError} onRetry={() => { setPollError(undefined); setPolling(true); }} />}
    </Card>
  </section>;
}
