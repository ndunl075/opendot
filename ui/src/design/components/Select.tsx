import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { Check, ChevronDown } from "lucide-react";

export interface SelectOption { value: string; label: string; disabled?: boolean }
export interface SelectProps {
  label: string;
  options: readonly SelectOption[];
  value?: string;
  defaultValue?: string;
  onChange?: (value: string) => void;
  disabled?: boolean;
  hint?: string;
  error?: string;
  id?: string;
  className?: string;
  "aria-describedby"?: string;
}

/** Select-only combobox: DOM focus stays on the trigger; navigation is provisional
 * until Enter, Space, Tab, or an option click commits it. Escape cancels. */
export function Select({ label, options, value, defaultValue, onChange, disabled, hint, error, id: providedId, className = "", "aria-describedby": describedBy }: SelectProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  const [localValue, setLocalValue] = useState(defaultValue ?? options.find(option => !option.disabled)?.value ?? "");
  const selectedValue = value ?? localValue;
  const selected = options.findIndex(option => option.value === selectedValue);
  const [expanded, setExpanded] = useState(false);
  const open = expanded && !disabled;
  const [active, setActive] = useState(-1);
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const search = useRef({ text: "", time: 0 });
  const enabled = options.map((option, index) => option.disabled ? -1 : index).filter(index => index >= 0);
  const activeIndex = enabled.includes(active) ? active : enabled[0];
  const description = [describedBy, hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(" ") || undefined;

  function close() { setExpanded(false); search.current = { text: "", time: 0 }; }
  function show(index = enabled.includes(selected) ? selected : enabled[0]) { setActive(index ?? -1); setExpanded(true); }
  function choose(index: number | undefined) {
    const option = index === undefined ? undefined : options[index];
    if (!option || option.disabled) return;
    setLocalValue(option.value); onChange?.(option.value); close();
  }

  useLayoutEffect(() => {
    if (!open || !popup.current || !trigger.current) return;
    const menu = popup.current;
    // The top layer avoids clipping inside scrolling panels and modal dialogs.
    menu.showPopover?.();
    function position() {
      const box = trigger.current!.getBoundingClientRect();
      const below = window.innerHeight - box.bottom - 14;
      const above = box.top - 14;
      const upward = below < Math.min(menu.scrollHeight, 280) && above > below;
      menu.style.width = `${Math.min(Math.max(box.width, 168), window.innerWidth - 16)}px`;
      menu.style.maxHeight = `${Math.max(36, Math.min(320, upward ? above : below))}px`;
      menu.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - menu.offsetWidth - 8))}px`;
      menu.style.top = `${upward ? Math.max(8, box.top - menu.offsetHeight - 6) : box.bottom + 6}px`;
    }
    position();
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    return () => { window.removeEventListener("resize", position); window.removeEventListener("scroll", position, true); menu.hidePopover?.(); };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    document.getElementById(`${id}-option-${activeIndex}`)?.scrollIntoView?.({ block: "nearest" });
  }, [open, activeIndex, id]);

  useEffect(() => {
    if (!open) return;
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) {
        setExpanded(false); search.current = { text: "", time: 0 };
      }
    }
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  function keyboard(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.ctrlKey || event.metaKey || disabled) return;
    if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); close(); return; }
    if (event.key === "Tab") { if (open) choose(activeIndex); return; }
    if (["ArrowDown", "ArrowUp", "Home", "End", "Enter", " "].includes(event.key) && !(event.key === " " && search.current.text && Date.now() - search.current.time < 700)) {
      event.preventDefault();
      if (event.key === "Enter" || event.key === " ") { if (open) choose(activeIndex); else show(); }
      else if (event.key === "Home") show(enabled[0]);
      else if (event.key === "End") show(enabled.at(-1));
      else if (!open) show();
      else {
        const next = Math.max(0, Math.min(enabled.length - 1, enabled.indexOf(activeIndex) + (event.key === "ArrowDown" ? 1 : -1)));
        setActive(enabled[next] ?? -1);
      }
      search.current = { text: "", time: 0 };
      return;
    }
    if (event.key.length !== 1 || event.altKey) return;
    event.preventDefault();
    const character = event.key.toLocaleLowerCase();
    const previous = Date.now() - search.current.time < 700 ? search.current.text : "";
    const text = previous === character ? character : previous + character;
    search.current = { text, time: Date.now() };
    const start = open ? activeIndex : selected;
    const ordered = [...enabled.filter(index => index > start), ...enabled.filter(index => index <= start)];
    if (text.length > 1 && options[start]?.label.toLocaleLowerCase().startsWith(text)) { show(start); return; }
    const match = ordered.find(index => options[index].label.toLocaleLowerCase().startsWith(text));
    if (match !== undefined) show(match); else if (!open) show();
  }

  return <div className="field" ref={root}>
    <label className="field-label" htmlFor={id} id={`${id}-label`}>{label}</label>
    <button ref={trigger} id={id} type="button" role="combobox" className={`select-trigger ${className}`} disabled={disabled}
      aria-labelledby={`${id}-label`} aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? `${id}-listbox` : undefined}
      aria-activedescendant={open && activeIndex !== undefined ? `${id}-option-${activeIndex}` : undefined}
      aria-invalid={error ? true : undefined} aria-describedby={description}
      onClick={() => { if (open) close(); else show(); }} onKeyDown={keyboard} onBlur={close}>
      <span>{options[selected]?.label ?? "Choose an option"}</span><ChevronDown size={16} aria-hidden="true" />
    </button>
    {open && <div ref={popup} id={`${id}-listbox`} role="listbox" aria-labelledby={`${id}-label`} popover="manual" className="select-popover">
      {options.map((option, index) => <div key={option.value} id={`${id}-option-${index}`} role="option" aria-selected={option.value === selectedValue}
        aria-disabled={option.disabled || undefined} className={`select-option ${index === activeIndex ? "is-active" : ""}`}
        onPointerDown={event => event.preventDefault()} onPointerMove={() => { if (!option.disabled) setActive(index); }}
        onClick={() => { if (!option.disabled) { choose(index); trigger.current?.focus(); } }}>
        <span>{option.label}</span>{option.value === selectedValue && <Check size={16} aria-hidden="true" />}
      </div>)}
    </div>}
    {hint && <span className="field-hint" id={`${id}-hint`}>{hint}</span>}
    {error && <span className="field-error" id={`${id}-error`}>{error}</span>}
  </div>;
}
