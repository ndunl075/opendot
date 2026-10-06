import { cloneElement, useEffect, useId, useState, type HTMLAttributes, type ReactElement, type ReactNode, type ButtonHTMLAttributes } from "react";
import { Archive, CircleAlert, Info, X } from "lucide-react";
import { ApiError, isSessionError, isStaleItemError } from "../../api/client";
import { Button, IconButton } from "./primitives";

export interface EmptyStateProps { title: string; description?: string; icon?: ReactNode; action?: ReactNode }
export function EmptyState({ title, description, icon = <Archive size={26} />, action }: EmptyStateProps) {
  return <div className="empty-state"><div className="state-icon" aria-hidden="true">{icon}</div><h2>{title}</h2>{description && <p>{description}</p>}{action && <div className="state-action">{action}</div>}</div>;
}

export interface ErrorStateProps { error: unknown; onRetry?: () => void }
export function ErrorState({ error, onRetry }: ErrorStateProps) {
  const unavailable = error instanceof ApiError && error.code === "not_implemented";
  const expired = isSessionError(error);
  const noRetry = expired || isStaleItemError(error) || (error instanceof ApiError && error.status === 422);
  return <div className={`error-state ${unavailable ? "error-state--unavailable" : ""}`} role={unavailable ? "status" : "alert"}>
    <EmptyState title={expired ? "Your session expired" : unavailable ? "Not available in this version yet" : "Something went wrong"}
      description={expired ? (window.__OPENDOT__ ? "Restart OpenDot" : "Sign in again to reconnect to your daemon.") : unavailable ? "This feature is not supported by your version of OpenDot. You can keep using the rest of the app." : error instanceof Error ? error.message : "OpenDot could not complete this request. Please try again."}
      icon={unavailable ? <Info size={26} /> : <CircleAlert size={26} />}
      action={expired && !window.__OPENDOT__ ? <a href="/login">Sign in</a> : !unavailable && !noRetry && onRetry ? <Button variant="secondary" onClick={onRetry}>Try again</Button> : undefined} />
  </div>;
}

export interface SkeletonProps extends HTMLAttributes<HTMLDivElement> { label?: string }
export function Skeleton({ label = "Loading", className = "", ...props }: SkeletonProps) {
  return <div {...props} className={`skeleton ${className}`} role="status"><span className="sr-only">{label}</span></div>;
}

export interface ToastProps { message: string; tone?: "neutral" | "success" | "danger"; onDismiss: () => void; duration?: number }
/** Persistent by default, so a message never expires before it can be read. */
export function Toast({ message, tone = "neutral", onDismiss, duration = 0 }: ToastProps) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (!duration || paused) return;
    const timer = window.setTimeout(onDismiss, duration);
    return () => window.clearTimeout(timer);
  }, [duration, paused, onDismiss]);
  return <div className={`toast toast--${tone}`} onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)} onFocus={() => setPaused(true)} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setPaused(false); }}>
    <span role={tone === "danger" ? "alert" : "status"}>{message}</span><IconButton label="Dismiss notification" onClick={onDismiss}><X size={18} /></IconButton>
  </div>;
}

export interface TooltipProps { content: string; children: ReactElement<ButtonHTMLAttributes<HTMLButtonElement>> }
export function Tooltip({ content, children }: TooltipProps) {
  const id = useId();
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const open = (hovered || focused) && !dismissed;
  useEffect(() => {
    if (!open) return;
    const escape = (event: globalThis.KeyboardEvent) => { if (event.key === "Escape") setDismissed(true); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [open]);
  return <span className="tooltip-anchor" onMouseEnter={() => { setHovered(true); setDismissed(false); }} onMouseLeave={() => setHovered(false)} onFocus={() => { setFocused(true); setDismissed(false); }} onBlur={() => setFocused(false)}>
    {cloneElement(children, { "aria-describedby": [children.props["aria-describedby"], open ? id : null].filter(Boolean).join(" ") || undefined })}
    {open && <span className="tooltip" role="tooltip" id={id}>{content}</span>}
  </span>;
}
