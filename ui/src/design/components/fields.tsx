import { useId, type ComponentPropsWithRef, type ReactNode } from "react";

interface FieldProps { label: string; hint?: string; error?: string }
function Field({ id, label, hint, error, children }: FieldProps & { id: string; children: ReactNode }) {
  return <div className="field">
    <label className="field-label" htmlFor={id}>{label}</label>{children}
    {hint && <span className="field-hint" id={`${id}-hint`}>{hint}</span>}
    {error && <span className="field-error" id={`${id}-error`}>{error}</span>}
  </div>;
}
function descriptions(id: string, hint?: string, error?: string, extra?: string) {
  return [extra, hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(" ") || undefined;
}
export interface InputProps extends FieldProps, ComponentPropsWithRef<"input"> {}
export function Input({ label, hint, error, id: providedId, className = "", "aria-describedby": describedBy, ...props }: InputProps) {
  const generatedId = useId(); const id = providedId ?? generatedId;
  return <Field {...{ id, label, hint, error }}><input {...props} id={id} className={`input ${className}`} aria-invalid={error ? true : props["aria-invalid"]} aria-describedby={descriptions(id, hint, error, describedBy)} /></Field>;
}
export interface TextareaProps extends FieldProps, ComponentPropsWithRef<"textarea"> {}
export function Textarea({ label, hint, error, id: providedId, className = "", "aria-describedby": describedBy, ...props }: TextareaProps) {
  const generatedId = useId(); const id = providedId ?? generatedId;
  return <Field {...{ id, label, hint, error }}><textarea rows={4} {...props} id={id} className={`input textarea ${className}`} aria-invalid={error ? true : props["aria-invalid"]} aria-describedby={descriptions(id, hint, error, describedBy)} /></Field>;
}
export { Select, type SelectProps, type SelectOption } from "./Select";

export interface CheckboxProps extends Omit<ComponentPropsWithRef<"input">, "type" | "role" | "children"> { label: string; hint?: string }
export function Checkbox({ label, hint, id: providedId, className = "", "aria-describedby": describedBy, ...props }: CheckboxProps) {
  const generatedId = useId(); const id = providedId ?? generatedId;
  return <div className={`choice ${className}`}><label htmlFor={id}><input {...props} id={id} type="checkbox" aria-describedby={descriptions(id, hint, undefined, describedBy)} /><span>{label}</span></label>
    {hint && <span className="field-hint" id={`${id}-hint`}>{hint}</span>}</div>;
}

export function Switch({ label, hint, id: providedId, className = "", "aria-describedby": describedBy, ...props }: CheckboxProps) {
  const generatedId = useId(); const id = providedId ?? generatedId;
  return <div className={`choice switch ${className}`}><label htmlFor={id}><input {...props} id={id} type="checkbox" role="switch" aria-describedby={descriptions(id, hint, undefined, describedBy)} /><span>{label}</span></label>
    {hint && <span className="field-hint" id={`${id}-hint`}>{hint}</span>}</div>;
}
export { Switch as Toggle };
