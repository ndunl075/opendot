import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, PawPrint, Smile } from "lucide-react";
import { AvatarBase, DEFAULT_AVATAR, encodeAvatarSeed, parseAvatarSeed } from "./avatar";
import { avatarColors, characters, pets } from "./characters/catalog";
import { Character } from "./characters/Character";
import { PixelPet } from "./characters/PixelPet";
import "./avatar-picker.css";

interface Option { id: string; name: string; art: ReactNode }
const colorOptions: Option[] = avatarColors.map(color => ({ ...color, art: <svg className="avatar-swatch" viewBox="4 4 92 92"><AvatarBase colorId={color.id} /></svg> }));
const characterOptions: Option[] = [
  { id: "none", name: "None", art: <span className="avatar-none"><Smile size={38} strokeWidth={1.5} /></span> },
  ...characters.map(character => ({ ...character, art: <svg viewBox="0 0 100 100"><Character id={character.id} /></svg> })),
];
const petOptions: Option[] = [
  { id: "none", name: "None", art: <span className="avatar-none"><PawPrint size={36} strokeWidth={1.5} /></span> },
  ...pets.map(pet => ({ ...pet, art: <svg viewBox="0 0 16 16" shapeRendering="crispEdges"><PixelPet id={pet.id} /></svg> })),
];

function PickerRow({ label, singular, options, value, onChange, disabled }: { label: string; singular: string; options: Option[]; value: string; onChange: (value: string) => void; disabled: boolean }) {
  const id = useId();
  const row = useRef<HTMLDivElement>(null);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const [edges, setEdges] = useState({ start: true, end: true });
  useEffect(() => {
    const element = row.current;
    if (!element) return;
    const update = () => setEdges({ start: element.scrollLeft <= 1, end: element.scrollLeft + element.clientWidth >= element.scrollWidth - 1 });
    update();
    element.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    const observer = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(update);
    observer?.observe(element);
    return () => { element.removeEventListener("scroll", update); window.removeEventListener("resize", update); observer?.disconnect(); };
  }, []);
  useEffect(() => {
    const element = row.current;
    const index = options.findIndex(option => option.id === value);
    const button = buttons.current[index];
    if (!element || !button || !element.scrollTo) return;
    // Reveal only within this row; never scroll the enclosing dialog or page.
    const left = button.offsetLeft;
    if (index === 0) element.scrollTo({ left: 0 });
    else if (index === options.length - 1) element.scrollTo({ left: element.scrollWidth });
    else if (left - 4 < element.scrollLeft) element.scrollTo({ left: left - 4 });
    else if (left + button.offsetWidth + 4 > element.scrollLeft + element.clientWidth) element.scrollTo({ left: left + button.offsetWidth + 4 - element.clientWidth });
  }, [value, options]);
  function move(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (disabled) return;
    const last = options.length - 1;
    const next = event.key === "Home" ? 0 : event.key === "End" ? last
      : ["ArrowRight", "ArrowDown"].includes(event.key) ? (index + 1) % options.length
      : ["ArrowLeft", "ArrowUp"].includes(event.key) ? (index + last) % options.length : -1;
    if (next < 0) return;
    event.preventDefault();
    onChange(options[next].id);
    buttons.current[next]?.focus({ preventScroll: true });
  }
  function scroll(direction: number) {
    const element = row.current;
    if (element) element.scrollBy({ left: direction * Math.max(128, element.clientWidth - 128), behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }
  return <section className="avatar-picker-section">
    <div className="avatar-picker-heading"><h3 id={id}>{label}</h3><div className="avatar-picker-arrows">
      <button type="button" aria-label={`Previous ${label.toLowerCase()}`} disabled={disabled || edges.start} onClick={() => scroll(-1)}><ChevronLeft size={20} strokeWidth={1.6} /></button>
      <button type="button" aria-label={`Next ${label.toLowerCase()}`} disabled={disabled || edges.end} onClick={() => scroll(1)}><ChevronRight size={20} strokeWidth={1.6} /></button>
    </div></div>
    <div className="avatar-picker-row" ref={row} role="radiogroup" aria-labelledby={id} aria-disabled={disabled || undefined}>
      {options.map((option, index) => <button key={option.id} ref={element => { buttons.current[index] = element; }} type="button" role="radio" className="avatar-tile"
        aria-label={`${singular}: ${option.name}`} aria-checked={value === option.id} title={option.name} tabIndex={value === option.id ? 0 : -1} disabled={disabled}
        onKeyDown={event => move(event, index)} onClick={() => onChange(option.id)}><span className="avatar-tile-art" aria-hidden="true">{option.art}</span></button>)}
    </div>
  </section>;
}

export function AvatarPicker({ seed, onChange, disabled = false }: { seed: string; onChange: (seed: string) => void; disabled?: boolean }) {
  const choices = parseAvatarSeed(seed) ?? DEFAULT_AVATAR;
  return <div className="avatar-picker">
    <PickerRow label="Colors" singular="Color" options={colorOptions} value={choices.color} disabled={disabled} onChange={color => onChange(encodeAvatarSeed({ ...choices, color }))} />
    <PickerRow label="Characters" singular="Character" options={characterOptions} value={choices.character} disabled={disabled} onChange={character => onChange(encodeAvatarSeed({ ...choices, character }))} />
    <PickerRow label="Pets" singular="Pet" options={petOptions} value={choices.pet} disabled={disabled} onChange={pet => onChange(encodeAvatarSeed({ ...choices, pet }))} />
  </div>;
}
