import { useId } from "react";
import type { CharacterId } from "./catalog";

// Sculpted silhouettes, all lit from the upper left. Accessories share the same
// materials and cast a small contact shadow instead of using drawn outlines.
const bodies: Record<CharacterId, { path: string; light: string; color: string; shade: string }> = {
  gumdrop: { light: "#c9f3d7", color: "#79c4a3", shade: "#3e806e", path: "M18 71C20 48 27 27 43 25C65 19 79 42 81 69C84 84 67 90 49 90C31 91 15 85 18 71Z" },
  mochi: { light: "#ffe3cd", color: "#f0ae98", shade: "#bf7474", path: "M12 65C12 43 29 32 50 32C74 31 90 47 88 68C87 83 71 89 49 88C28 89 12 83 12 65Z" },
  cloud: { light: "#e4d7fa", color: "#b09ad9", shade: "#7661a0", path: "M22 76C7 70 8 50 23 46C19 25 44 18 55 31C70 20 88 34 82 48C98 56 91 79 74 79C64 93 44 88 36 80C29 83 24 80 22 76Z" },
  star: { light: "#fff0b0", color: "#edce61", shade: "#c69137", path: "M43 20Q49 7 56 20L65 36L83 39Q99 42 87 54L74 66L75 83Q76 98 62 90L49 81L33 89Q18 97 21 80L23 65L11 53Q0 40 17 38L35 36Z" },
  pebble: { light: "#b4e9df", color: "#62adb1", shade: "#387583", path: "M26 32C39 18 63 17 76 37C95 62 79 87 56 90C32 95 12 79 15 61C16 49 20 40 26 32Z" },
  tomato: { light: "#ffc7a6", color: "#ed8972", shade: "#b54a4d", path: "M17 49C24 31 40 31 50 36C70 26 87 43 89 61C92 83 69 91 49 89C25 92 7 73 17 49Z" },
  bean: { light: "#a7bfdf", color: "#6382b0", shade: "#344563", path: "M62 16C85 18 91 38 78 51C67 61 78 77 56 89C31 102 10 78 19 54C25 39 39 40 40 29C40 20 50 15 62 16Z" },
  dune: { light: "#faeac6", color: "#d6b787", shade: "#a67e53", path: "M13 71C20 55 34 47 42 32C53 11 66 33 69 46C76 60 91 65 89 78C87 94 16 96 11 81Q10 76 13 71Z" },
  jelly: { light: "#ffcfb1", color: "#ed927e", shade: "#b25a66", path: "M43 23C60 15 75 27 77 40C79 52 64 57 69 66C77 76 75 86 61 91C40 100 18 83 20 65C20 50 24 32 43 23Z" },
  cactus: { light: "#d9e9b5", color: "#9bb67f", shade: "#5b7e62", path: "M20 64C13 53 19 36 31 32C36 18 62 18 70 30C87 32 92 52 83 65C92 88 27 100 20 73Z" },
};

function Accessory({ id, uid }: { id: CharacterId; uid: string }) {
  const fill = (material: string) => `url(#${uid}-${material})`;
  switch (id) {
    case "gumdrop": return <g filter={fill("join")}>
      <path d="M24 35C25 20 32 10 49 10C65 10 75 19 77 35Z" fill={fill("blue")} />
      <g stroke="#d5ddf8" strokeOpacity=".24" strokeWidth="2" strokeLinecap="round" fill="none"><path d="M31 28Q33 16 39 14M42 28L44 13M54 28L53 13M66 28Q65 18 62 16" /></g>
      <rect x="22" y="29" width="57" height="14" rx="7" fill={fill("cuff")} />
      <path d="M28 33Q49 30 71 34" fill="none" stroke="#d0dcf7" opacity=".35" strokeWidth="1.4" strokeLinecap="round" />
      <ellipse cx="48" cy="10" rx="8" ry="7" fill={fill("cuff")} />
      <rect x="63" y="33" width="7" height="7" rx="2" fill={fill("cream")} />
    </g>;
    case "mochi": return <g transform="rotate(-22 74 39)" filter={fill("join")}>
      <rect x="64" y="34" width="21" height="9" rx="4.5" fill={fill("pink")} />
      <rect x="68" y="36" width="12" height="2" rx="1" fill="#ffe5ce" opacity=".7" />
    </g>;
    case "cloud": return <g filter={fill("join")}>
      <path d="M59 79Q66 75 72 80L78 96Q69 99 62 96Z" fill={fill("warm")} />
      <path d="M23 73Q47 84 79 71L79 82Q50 96 24 84Q20 81 23 73Z" fill={fill("warm")} />
      <path d="M27 77Q49 86 74 77" stroke="#ffe6b8" strokeOpacity=".4" strokeWidth="2" strokeLinecap="round" fill="none" />
      <path d="M65 90L75 88M66 94L77 92" stroke="#996341" strokeOpacity=".4" strokeWidth="2" />
    </g>;
    case "star": return <g transform="rotate(20 22 61)" filter={fill("join")}>
      <rect x="13" y="56" width="18" height="11" rx="5.5" fill={fill("green")} />
      <circle cx="22" cy="60" r="3" fill={fill("cream")} />
    </g>;
    case "pebble": return <g filter={fill("join")}>
      <path d="M69 39Q80 38 79 55" stroke={fill("warm")} strokeWidth="5" fill="none" strokeLinecap="round" />
      <rect x="71" y="47" width="19" height="33" rx="8" fill={fill("warm")} />
      <rect x="74" y="63" width="14" height="12" rx="4" fill={fill("cream")} />
      <path d="M75 51Q70 48 68 44" stroke={fill("warm")} strokeWidth="4" fill="none" strokeLinecap="round" />
      <rect x="79" y="65" width="3" height="4" rx="1.5" fill={fill("warm")} />
    </g>;
    case "tomato": return <g filter={fill("join")}>
      <path d="M52 32Q48 21 57 13" stroke={fill("green")} strokeWidth="6" fill="none" strokeLinecap="round" />
      {[-65, -25, 25, 65, 105].map(angle => <ellipse key={angle} cx="51" cy="27" rx="6" ry="15" transform={`rotate(${angle} 51 37)`} fill={fill("green")} />)}
    </g>;
    case "bean": return <g filter={fill("join")}>
      <path d="M24 71Q41 82 67 70Q60 84 52 93L39 84L28 85Q23 80 24 71Z" fill={fill("warm")} />
      <path d="M28 75Q40 82 59 77" stroke="#ffdfb8" strokeOpacity=".5" strokeWidth="1.5" fill="none" />
      <path d="M43 81L51 83L47 88Z" fill="#ffedce" opacity=".8" />
      <circle cx="32" cy="79" r="1.5" fill="#ffedce" />
    </g>;
    case "dune": return <g filter={fill("join")}>
      <path d="M32 37Q55 24 70 42L68 49Q52 37 32 46Z" fill={fill("blue")} />
      <path d="M34 41C28 39 15 43 18 49C22 57 51 55 65 47Q47 46 34 41Z" fill={fill("cuff")} />
      <path d="M23 48Q35 53 53 48" stroke="#e3edf8" opacity=".4" strokeWidth="1.5" fill="none" strokeLinecap="round" />
    </g>;
    case "jelly": return <g filter={fill("join")}>
      <path d="M20 57C15 9 78 7 80 58" stroke={fill("warm")} strokeWidth="6" fill="none" />
      {[21, 78].map(x => <g key={x}>
        <path d={`M${x-8} 48Q${x-10} 41 ${x-4} 41Q${x} 36 ${x+5} 42Q${x+12} 42 ${x+10} 51Q${x+14} 58 ${x+9} 65Q${x+7} 72 ${x} 70Q${x-8} 73 ${x-10} 65Q${x-15} 57 ${x-8} 48Z`} fill={fill("cream")} />
        <ellipse cx={x-2} cy="52" rx="5" ry="8" fill="#fff4dc" opacity=".45" filter={fill("soft")} />
      </g>)}
    </g>;
    case "cactus": return <g transform="translate(68 27) rotate(15)" filter={fill("join")}>
      {[0, 72, 144, 216, 288].map(angle => <ellipse key={angle} cy="-7" rx="5.5" ry="9" transform={`rotate(${angle})`} fill={fill("pink")} />)}
      <circle r="6" fill={fill("cream")} />
    </g>;
  }
}

export function Character({ id }: { id: CharacterId }) {
  const uid = useId();
  const body = bodies[id];
  const fill = (part: string) => `url(#${uid}-${part})`;
  const materials = {
    blue: ["#afc2e7", "#718ab9", "#445982"], cuff: ["#cbd9ef", "#94afd3", "#5f79a0"],
    warm: ["#ffd8a3", "#d9a16e", "#a36542"], cream: ["#fff5d9", "#e7ceb0", "#b69979"],
    green: ["#b9dda3", "#7eab76", "#426c53"], pink: ["#ffd1d2", "#dc929f", "#a65e7b"],
  };
  return <g data-character={id}>
    <defs>
      <radialGradient id={`${uid}-body`} cx="30%" cy="22%" r="92%"><stop stopColor={body.light} /><stop offset=".47" stopColor={body.color} /><stop offset="1" stopColor={body.shade} /></radialGradient>
      {Object.entries(materials).map(([name, colors]) => <radialGradient key={name} id={`${uid}-${name}`} cx="28%" cy="18%" r="90%"><stop stopColor={colors[0]} /><stop offset=".5" stopColor={colors[1]} /><stop offset="1" stopColor={colors[2]} /></radialGradient>)}
      <radialGradient id={`${uid}-eye`} cx="32%" cy="22%" r="75%"><stop stopColor="#555d63" /><stop offset=".55" stopColor="#20282c" /><stop offset="1" stopColor="#101519" /></radialGradient>
      <radialGradient id={`${uid}-shine`}><stop stopColor="#fff" stopOpacity=".5" /><stop offset="1" stopColor="#fff" stopOpacity="0" /></radialGradient>
      <filter id={`${uid}-soft`} x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="1.8" /></filter>
      <filter id={`${uid}-join`} x="-30%" y="-35%" width="160%" height="175%"><feDropShadow dx=".4" dy="1.6" stdDeviation="1" floodColor="#342536" floodOpacity=".25" /></filter>
      <filter id={`${uid}-volume`} x="-15%" y="-15%" width="130%" height="135%">
        <feGaussianBlur in="SourceAlpha" stdDeviation="1.5" result="blur" />
        <feOffset in="blur" dx="1.2" dy="1.5" result="rim" />
        <feComposite in="SourceAlpha" in2="rim" operator="out" result="edge" />
        <feFlood floodColor="#fff" floodOpacity=".38" />
        <feComposite in2="edge" operator="in" result="light" />
        <feOffset in="blur" dx="-1.2" dy="-2" result="inset" />
        <feComposite in="SourceAlpha" in2="inset" operator="out" result="lowerEdge" />
        <feFlood floodColor="#362b39" floodOpacity=".15" />
        <feComposite in2="lowerEdge" operator="in" result="shade" />
        <feMerge><feMergeNode in="SourceGraphic" /><feMergeNode in="shade" /><feMergeNode in="light" /></feMerge>
      </filter>
      <clipPath id={`${uid}-clip`}><path d={body.path} /></clipPath>
    </defs>
    <ellipse cx="51" cy="92" rx="30" ry="3.5" fill="#303138" opacity=".18" filter={fill("soft")} />
    <path d={body.path} fill={fill("body")} filter={fill("volume")} />
    <g clipPath={fill("clip")}>
      <ellipse cx="32" cy="38" rx="20" ry="15" transform="rotate(-35 32 38)" fill={fill("shine")} />
      {id === "cactus" && <g fill="none" strokeLinecap="round">
        <path d="M36 31Q24 58 35 82M51 28Q42 55 51 87M64 33Q73 59 65 84" stroke="#456b48" strokeWidth="2.5" opacity=".12" />
        <path d="M32 39L29 43M72 47L75 51M27 68L24 72M65 77L68 81M48 30V34" stroke="#edf3cf" strokeWidth="1.8" opacity=".65" />
      </g>}
      <g fill="#dd8587" opacity={id === "star" ? ".6" : ".35"} filter={fill("soft")}><ellipse cx="32" cy="66" rx="5" ry="3" /><ellipse cx="68" cy="66" rx="5" ry="3" /></g>
      <g fill={fill("eye")} filter={fill("join")}><ellipse cx="40" cy="57" rx="3.1" ry="4.1" /><ellipse cx="60" cy="57" rx="3.1" ry="4.1" /></g>
      <g fill="#fff" opacity=".9"><ellipse cx="39.1" cy="55.4" rx="1" ry="1.2" /><ellipse cx="59.1" cy="55.4" rx="1" ry="1.2" /></g>
      <path d="M47 65Q50 68 53 65" fill="none" stroke="#574549" strokeWidth="1.5" strokeLinecap="round" />
      {id === "mochi" && <g fill="#ad7460" opacity=".8"><circle cx="26" cy="62" r="1" /><circle cx="31" cy="60" r="1.1" /><circle cx="29" cy="66" r="1.1" /><circle cx="70" cy="60" r="1.1" /><circle cx="75" cy="63" r="1" /><circle cx="71" cy="66" r="1.1" /></g>}
    </g>
    <Accessory id={id} uid={uid} />
  </g>;
}
