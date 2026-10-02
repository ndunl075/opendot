import { useState } from "react";
import { api } from "../../api/client";
import type { FeatureSwitchState, ProviderKeyStatus, ProviderOptIn, ProviderSpend } from "../../api/types.gen";
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState, Input, Switch } from "../../design/components";
import { Resource, useMutation, useResource } from "../shared";

const loadFeatures = () => api.call("features_list");
const loadSpend = () => api.call("spend_get");
function supportsKey(provider: ProviderOptIn["provider"]): provider is ProviderKeyStatus["provider"] {
  return provider === "openai_key" || provider === "anthropic_key" || provider === "openrouter";
}

function ApiKeyField({ provider, configured, label, onChanged }: { provider: ProviderKeyStatus["provider"]; configured: boolean; label: string; onChanged: () => void }) {
  const [key, setKey] = useState("");
  const [saved, setSaved] = useState(configured);
  const [remove, setRemove] = useState(false);
  const mutation = useMutation();
  return <div className="form-stack"><p className="muted">{saved ? "Key saved. Its value is never displayed." : "No key saved."} Saving a key does not enable this provider or its features.</p>
    <form className="form-stack" onSubmit={async event => {
      event.preventDefault();
      const result = await mutation.run(() => api.call("provider_api_key_save", { params: { provider }, body: { api_key: key } }));
      // A secret is only ever user input: do not read a response into this field.
      setKey("");
      if (result) { setSaved(result.key_saved); mutation.setNotice("API key saved. Features remain unchanged."); onChanged(); }
    }}><Input label={`${label} API key`} type="password" autoComplete="new-password" required value={key} onChange={event => setKey(event.target.value)} hint="Stored in the system keychain. Enter a new value to replace the saved key." />
      <div className="actions"><Button type="submit" variant="secondary" loading={mutation.pending}>Save {label} key</Button>{saved && <Button variant="ghost" disabled={mutation.pending} onClick={() => setRemove(true)}>Remove {label} key</Button>}</div>
    </form>
    {!!mutation.error && !remove && <ErrorState error={mutation.error} />}{mutation.notice && <p role="status">{mutation.notice}</p>}
    <ConfirmDialog open={remove} error={mutation.error} onClose={() => { if (!mutation.pending) setRemove(false); }} title={`Remove ${label} API key?`} description="Features relying on this key will no longer be able to use it." confirmLabel="Remove key" danger loading={mutation.pending} onConfirm={async () => {
      const result = await mutation.run(() => api.call("provider_api_key_remove", { params: { provider } }));
      if (result) { setSaved(result.key_saved); setKey(""); setRemove(false); mutation.setNotice("Key removed."); onChanged(); }
    }} />
  </div>;
}

function ProviderCard({ provider, onChanged }: { provider: ProviderOptIn; onChanged: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const mutation = useMutation();
  async function toggle(enabled: boolean) {
    const result = await mutation.run(() => api.call("provider_enabled_set", { params: { provider: provider.provider }, body: { enabled } }));
    if (result) { setConfirm(false); mutation.setNotice("Saved"); onChanged(); }
  }
  const warning = provider.cost_warning || (provider.provider === "local" ? "Local models use your own hardware, electricity and memory." : "Requests through this provider are billed separately to your API account.");
  return <Card className="section-card form-stack"><h3>{provider.label}</h3>
    {provider.provider === "chatgpt_plan" ? <><Badge>Using ChatGPT plan</Badge><p className="muted">The default model path. Manage weekly limits in ChatGPT Settings, Usage; keep credit use off.</p></> : <>
      <Switch label={`Enable ${provider.label}`} checked={provider.enabled} disabled={mutation.pending} hint={warning} onChange={event => { if (event.target.checked) setConfirm(true); else void toggle(false); }} />
      {supportsKey(provider.provider) && <ApiKeyField provider={provider.provider} configured={provider.configured ?? false} label={provider.label} onChanged={onChanged} />}
      {provider.provider === "local" && <p className="muted">{provider.configured ? "Local provider configured." : "Local provider is not configured. Server address setup is not available in this version yet."}</p>}
    </>}
    {!!mutation.error && !confirm && <ErrorState error={mutation.error} />}{mutation.notice && <span className="muted text-sm" role="status">{mutation.notice}</span>}
    <ConfirmDialog open={confirm} error={mutation.error} onClose={() => { if (!mutation.pending) setConfirm(false); }} title={`Enable ${provider.label}?`} description={`${warning} Features each have their own switch and remain unchanged.`} confirmLabel="Enable provider" loading={mutation.pending} onConfirm={() => { void toggle(true); }} />
  </Card>;
}

function Feature({ feature, onChanged }: { feature: FeatureSwitchState; onChanged: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const mutation = useMutation();
  async function toggle(enabled: boolean) {
    const result = await mutation.run(() => api.call("feature_set", { params: { feature: feature.name }, body: { enabled } }));
    if (result) { setConfirm(false); mutation.setNotice("Saved"); onChanged(); }
  }
  return <div className="form-stack"><Switch label={feature.title} checked={feature.enabled ?? false} disabled={mutation.pending || (!feature.enabled && feature.available === false)} hint={feature.cost_warning} onChange={event => { if (event.target.checked) setConfirm(true); else void toggle(false); }} />
    <p className="muted">{feature.requirement}</p>{!!mutation.error && !confirm && <ErrorState error={mutation.error} />}{mutation.notice && <span className="muted text-sm" role="status">{mutation.notice}</span>}
    <ConfirmDialog open={confirm} error={mutation.error} onClose={() => { if (!mutation.pending) setConfirm(false); }} title={`Enable ${feature.title}?`} description={feature.cost_warning} confirmLabel="Enable feature" loading={mutation.pending} onConfirm={() => { void toggle(true); }} />
  </div>;
}

function SpendCap({ spend, onChanged }: { spend: ProviderSpend; onChanged: () => void }) {
  const [cap, setCap] = useState(spend.cap_set ? spend.cap_usd.toString() : "");
  const mutation = useMutation();
  return <form className="form-stack" onSubmit={async event => {
    event.preventDefault();
    const result = await mutation.run(() => api.call("spend_cap_set", { params: { provider: spend.provider }, body: { cap_usd: Number(cap) } }));
    if (result) { mutation.setNotice("Saved"); onChanged(); }
  }}><Input label={`${spend.provider} monthly spend cap (USD)`} disabled={mutation.pending} required type="number" min="0" step="0.01" value={cap} onChange={event => { setCap(event.target.value); mutation.setNotice(""); }} hint={`${spend.month}: $${spend.spent_usd.toFixed(2)} spent. ${spend.cap_set ? "" : "No cap is set."}`} />
    <div className="actions"><Button variant="secondary" type="submit" loading={mutation.pending}>Save {spend.provider} cap</Button>{mutation.notice && <span className="muted text-sm" role="status">{mutation.notice}</span>}</div>{!!mutation.error && <ErrorState error={mutation.error} />}
  </form>;
}

export function ProviderSettings({ providers, onChanged }: { providers: ProviderOptIn[]; onChanged: () => void }) {
  const features = useResource(loadFeatures);
  const spend = useResource(loadSpend);
  return <section className="screen-stack" aria-labelledby="providers-title"><div><h2 id="providers-title">Providers and optional features</h2><p className="muted">Optional providers and features start off. A saved API key never enables either.</p></div>
    <div className="screen-grid">{providers.map(provider => <ProviderCard key={provider.provider} provider={provider} onChanged={() => { void features.reload(); onChanged(); }} />)}</div>
    {!providers.length && <EmptyState title="No providers reported" description="Provider settings will appear when your daemon reports them." />}
    <Card className="section-card form-stack"><h3>Feature switches</h3><Resource resource={features}>{data => data.features.length ? data.features.map(feature => <Feature key={feature.name} feature={feature} onChanged={() => { void features.reload(); }} />) : <EmptyState title="No optional features" description="Your daemon has not reported optional features." />}</Resource></Card>
    <Card className="section-card form-stack"><h3>Provider spend caps</h3><p className="muted">Separate API billing limits, measured in USD. Saving a cap does not enable spending.</p><Resource resource={spend}>{data => data.providers.length ? data.providers.map(item => <SpendCap key={item.provider} spend={item} onChanged={() => { void spend.reload(); }} />) : <EmptyState title="No provider spending reported" description="API provider caps will appear here when available." />}</Resource></Card>
  </section>;
}
