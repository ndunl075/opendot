import { useId, useRef, type ReactNode, type KeyboardEvent } from "react";

export interface TabItem { value: string; label: string; content: ReactNode; disabled?: boolean }
export interface TabsProps { label: string; items: TabItem[]; value: string; onValueChange: (value: string) => void }
export function Tabs({ label, items, value, onValueChange }: TabsProps) {
  const id = useId();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  function navigate(event: KeyboardEvent, index: number) {
    const available = items.map((item, i) => item.disabled ? -1 : i).filter(i => i >= 0);
    const current = available.indexOf(index);
    let next: number;
    switch (event.key) {
      case "ArrowRight": next = available[(current + 1) % available.length]; break;
      case "ArrowLeft": next = available[(current - 1 + available.length) % available.length]; break;
      case "Home": next = available[0]; break;
      case "End": next = available[available.length - 1]; break;
      default: return;
    }
    event.preventDefault(); onValueChange(items[next].value); buttons.current[next]?.focus();
  }
  return <div className="tabs"><div role="tablist" aria-label={label} className="tab-list">
    {items.map((item, index) => <button key={item.value} type="button" role="tab" id={`${id}-tab-${index}`} aria-controls={`${id}-panel-${index}`} aria-selected={value === item.value} disabled={item.disabled}
      tabIndex={value === item.value ? 0 : -1} ref={element => { buttons.current[index] = element; }} onClick={() => onValueChange(item.value)} onKeyDown={event => navigate(event, index)}>{item.label}</button>)}
  </div>{items.map((item, index) => <div key={item.value} role="tabpanel" id={`${id}-panel-${index}`} aria-labelledby={`${id}-tab-${index}`} hidden={value !== item.value} tabIndex={0} className="tab-panel">{item.content}</div>)}</div>;
}
