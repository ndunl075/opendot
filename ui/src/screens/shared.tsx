import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ErrorState, Skeleton } from "../design/components";

/** Load daemon-owned data; late responses cannot replace a newer request. */
export function useResource<T>(loader: () => Promise<T>) {
  const [data, setData] = useState<T>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>();
  const generation = useRef({ value: 0 }).current;
  const reload = useCallback(async () => {
    const request = ++generation.value;
    setLoading(true); setError(undefined);
    try { const result = await loader(); if (request === generation.value) setData(result); }
    catch (cause) { if (request === generation.value) setError(cause); }
    finally { if (request === generation.value) setLoading(false); }
  }, [loader, generation]);
  useEffect(() => { void reload(); return () => { generation.value++; }; }, [reload, generation]);
  return { data, loading, error, reload, setData };
}

/** Mutations remain pessimistic, especially approvals, spend settings and deletion. */
export function useMutation() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  const [notice, setNotice] = useState("");
  const busy = useRef(false);
  const run = useCallback(async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
    if (busy.current) return undefined;
    busy.current = true; setPending(true); setError(undefined); setNotice("");
    try { return await action(); }
    catch (cause) { setError(cause); return undefined; }
    finally { busy.current = false; setPending(false); }
  }, []);
  return { run, pending, error, notice, setNotice };
}

export function ScreenHeading({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <header className="page-heading screen-heading"><div><p className="eyebrow">Your personal companion</p><h1>{title}</h1><p className="muted">{description}</p></div>{action}</header>;
}

export function Resource<T>({ resource, children }: { resource: { data?: T; loading: boolean; error?: unknown; reload: () => void }; children: (data: T) => ReactNode }) {
  if (resource.loading) return <Skeleton className="screen-skeleton" label="Loading screen" />;
  if (resource.error) return <ErrorState error={resource.error} onRetry={resource.reload} />;
  return resource.data === undefined ? null : children(resource.data);
}

/** External destinations are daemon-provided, but must still be normal web links. */
export function safeExternalUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : undefined; }
  catch { return undefined; }
}
