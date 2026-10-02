import { useId, type SVGProps } from "react";
import { avatarColors, characters, pets, type CharacterId, type PetId } from "./characters/catalog";
import { Character } from "./characters/Character";
import { PixelPet } from "./characters/PixelPet";

export interface AvatarChoices { color: string; character: string; pet: string }
export const DEFAULT_AVATAR: Readonly<AvatarChoices> = { color: "slate", character: "none", pet: "none" };

function validChoices(value: AvatarChoices): boolean {
  return avatarColors.some(color => color.id === value.color)
    && (value.character === "none" || characters.some(character => character.id === value.character))
    && (value.pet === "none" || pets.some(pet => pet.id === value.pet));
}

/** null means a legacy ribbon; malformed v2 seeds use the safe plain slate ring. */
export function parseAvatarSeed(seed: string): AvatarChoices | null {
  if (!seed.startsWith("v2:")) return null;
  const match = seed.length <= 64 ? /^v2:c=([a-z]+);h=([a-z]+);p=([a-z]+)$/.exec(seed) : null;
  const choices = match && match[0] === seed ? { color: match[1], character: match[2], pet: match[3] } : null;
  return choices && validChoices(choices) ? choices : { ...DEFAULT_AVATAR };
}

export function encodeAvatarSeed(choices: AvatarChoices): string {
  const { color, character, pet } = validChoices(choices) ? choices : DEFAULT_AVATAR;
  return `v2:c=${color};h=${character};p=${pet}`;
}

export interface AvatarProps extends Omit<SVGProps<SVGSVGElement>, "children" | "role" | "aria-label"> {
  seed: string;
  label?: string;
  size?: number;
}

// FNV-1a + an integer PRNG: no random calls, generated IDs, or browser-specific hashing.
function sequence(seed: string) {
  let state = 2166136261;
  for (let i = 0; i < seed.length; i++) state = Math.imul(state ^ seed.charCodeAt(i), 16777619);
  return () => { state ^= state << 13; state ^= state >>> 17; state ^= state << 5; return (state >>> 0) / 4294967296; };
}

/** A new seed is the only random step. Persist it with the companion in the daemon. */
export function createAvatarSeed(): string { return crypto.randomUUID(); }

export function Avatar({ seed, ...props }: AvatarProps) {
  const choices = parseAvatarSeed(seed);
  return choices ? <ComposedAvatar choices={choices} {...props} /> : <RibbonAvatar seed={seed} {...props} />;
}

/** Shared by color swatches and previews so their geometry and shading stay identical. */
export function AvatarBase({ colorId, ring = true }: { colorId: string; ring?: boolean }) {
  const uid = useId();
  const color = avatarColors.find(color => color.id === colorId) ?? avatarColors[0];
  return <g data-avatar-base={ring ? "ring" : "disc"}>
    <defs><radialGradient id={`${uid}-color`} cx="32%" cy="25%" r="80%"><stop stopColor={color.light} /><stop offset="1" stopColor={color.color} /></radialGradient></defs>
    <circle cx="50" cy="50" r="46" fill={`url(#${uid}-color)`} opacity={ring ? 1 : 0.32} />
    {ring && <circle data-ring-center="" cx="50" cy="50" r="13.8" fill="#ffffff" />}
  </g>;
}

function ComposedAvatar({ choices, label = "Companion avatar", size = 88, ...props }: Omit<AvatarProps, "seed"> & { choices: AvatarChoices }) {
  return <svg width={size} height={size} viewBox="0 0 100 100" {...props} role="img" aria-label={label}>
    <AvatarBase colorId={choices.color} ring={choices.character === "none"} />
    {choices.character !== "none" && <g transform="translate(7 4) scale(.86)"><Character id={choices.character as CharacterId} /></g>}
    {choices.pet !== "none" && <g transform="translate(65 65) scale(2.15)"><PixelPet id={choices.pet as PetId} /></g>}
  </svg>;
}

function RibbonAvatar({ seed, label = "Companion's woven ribbon avatar", size = 88, ...props }: AvatarProps) {
  const random = sequence(seed);
  const palettes = [1, 2, 3, 4].map(index => ["bg", "ink", "mid", "tip"].map(part => `var(--avatar-${index}-${part})`));
  const colors = palettes[Math.floor(random() * palettes.length)];
  const bend = Math.round(30 + random() * 22);
  const rise = Math.round(21 + random() * 17);
  const turn = Math.round(random() * 18 - 9);
  return <svg width={size} height={size} viewBox="0 0 100 100" {...props} role="img" aria-label={label}>
    <rect width="100" height="100" rx="25" fill={colors[0]} />
    <g transform={`rotate(${turn} 50 50)`} fill="none" strokeWidth="9" strokeLinecap="square">
      <path d={`M24 76 V${rise} Q${bend} 12 62 29 L76 43`} stroke={colors[1]} />
      <path d={`M39 80 V${rise + 13} Q${bend + 15} 30 70 46 L78 54`} stroke={colors[2]} />
      <path d={`M55 81 V${rise + 26} Q${bend + 24} 47 78 65`} stroke={colors[3]} />
    </g>
  </svg>;
}
