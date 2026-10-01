import { LoaderCircle } from "lucide-react";
import type { ComponentPropsWithRef, HTMLAttributes, ReactNode } from "react";

export interface ButtonProps extends ComponentPropsWithRef<"button"> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
}
export function Button({ variant = "primary", size = "md", loading = false, disabled, className = "", children, type = "button", ...props }: ButtonProps) {
  return <button {...props} type={type} className={`button button--${variant} button--${size} ${className}`} disabled={disabled || loading} aria-busy={loading || undefined}>
    {loading && <LoaderCircle className="spinner" size={16} aria-hidden="true" />}{children}
    {loading && <span className="sr-only"> — Loading</span>}
  </button>;
}

export interface IconButtonProps extends Omit<ButtonProps, "children" | "aria-label"> { label: string; children: ReactNode }
export function IconButton({ label, children, className = "", variant = "ghost", ...props }: IconButtonProps) {
  return <Button {...props} variant={variant} aria-label={label} className={`icon-button ${className}`}><span aria-hidden="true">{children}</span></Button>;
}

export function Card({ className = "", ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={`card ${className}`} />;
}

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> { tone?: "neutral" | "accent" | "success" | "warning" | "danger" }
export function Badge({ tone = "neutral", className = "", ...props }: BadgeProps) {
  return <span {...props} className={`badge badge--${tone} ${className}`} />;
}

export interface UsageStampProps { model: string; effort: string; credits: number | null; className?: string }
export function UsageStamp({ model, effort, credits, className = "" }: UsageStampProps) {
  return <Badge className={`usage-stamp ${className}`}>
    <span>{model}</span><span>{effort} effort</span><span>{credits === null ? "Credits not reported" : `${credits.toLocaleString()} credits`}</span>
  </Badge>;
}
