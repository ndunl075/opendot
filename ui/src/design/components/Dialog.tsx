import { useEffect, useId, useRef, type ReactNode, type RefObject, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { Button, IconButton } from "./primitives";

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  initialFocusRef?: RefObject<HTMLElement | null>;
}

function focusable(dialog: HTMLDialogElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]')).filter(element => {
    if (element.tabIndex < 0 || element.matches(":disabled") || element.closest("[hidden], [inert]")) return false;
    for (let ancestor: HTMLElement | null = element; ancestor && ancestor !== dialog; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      if (style.display === "none" || style.visibility === "hidden") return false;
    }
    return true;
  });
}

export function Dialog({ open, onClose, title, description, children, initialFocusRef }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    (initialFocusRef?.current ?? focusable(dialog)[0])?.focus();
    // Native modal dialogs make the background inert. Lock scrolling as well.
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { dialog.close(); document.body.style.overflow = overflow; previous?.focus(); };
  }, [open, initialFocusRef]);

  function keyboard(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
    if (event.key !== "Tab") return;
    const controls = focusable(event.currentTarget);
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && (document.activeElement === first || !controls.includes(document.activeElement as HTMLElement))) {
      event.preventDefault(); last?.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !controls.includes(document.activeElement as HTMLElement))) {
      event.preventDefault(); first?.focus();
    }
  }
  if (!open) return null;
  return createPortal(<dialog ref={ref} className="dialog" aria-labelledby={`${id}-title`} aria-describedby={description ? `${id}-description` : undefined}
    onKeyDown={keyboard} onCancel={event => { event.preventDefault(); onClose(); }}>
    <div className="dialog-heading"><h2 id={`${id}-title`}>{title}</h2><IconButton label="Close dialog" onClick={onClose}><X size={20} /></IconButton></div>
    {description && <p className="dialog-description" id={`${id}-description`}>{description}</p>}
    <div className="dialog-content">{children}</div>
  </dialog>, document.body);
}

export interface ConfirmDialogProps extends Omit<DialogProps, "children" | "initialFocusRef"> {
  confirmLabel: string;
  onConfirm: () => void;
  danger?: boolean;
  loading?: boolean;
}
export function ConfirmDialog({ confirmLabel, onConfirm, danger = false, loading = false, ...props }: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  return <Dialog {...props} initialFocusRef={cancelRef}><div className="dialog-actions">
    <Button variant="secondary" ref={cancelRef} onClick={props.onClose}>Cancel</Button>
    <Button variant={danger ? "danger" : "primary"} loading={loading} onClick={onConfirm}>{confirmLabel}</Button>
  </div></Dialog>;
}
