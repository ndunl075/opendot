import type { SVGProps } from "react";

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

export function Avatar({ seed, label = "Companion's woven ribbon avatar", size = 88, ...props }: AvatarProps) {
  const random = sequence(seed);
  const palettes = [
    ["#e0edef", "#245c6a", "#71959a", "#c5a67c"],
    ["#ece9e2", "#455b61", "#84958b", "#b58b68"],
    ["#e8e9ee", "#465d7a", "#90a5b1", "#b9a68f"],
    ["#ede9e1", "#62594c", "#8e9c96", "#c19d73"],
  ];
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
