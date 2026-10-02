import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("src/design/tokens.css", "utf8");
function tokens(source: string) { return Object.fromEntries([...source.matchAll(/--(color-[\w-]+):\s*(#[\da-f]{6});/g)].map(match => [match[1], match[2]])); }
function luminance(hex: string) {
  const [r, g, b] = [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255).map(channel => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string) { const x = luminance(a), y = luminance(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }

describe.each(["light", "dark"])("%s theme WCAG AA", theme => {
  const palette = theme === "light" ? tokens(css.split('[data-theme="dark"]')[0]) : tokens(css.split('[data-theme="dark"]')[1].split("@media")[0]);
  const textPairs = [
    ...["bg", "rail", "surface", "surface-raised", "surface-muted", "surface-hover"].flatMap(background => ["text", "text-muted", "accent"].map(foreground => [foreground, background])),
    ["on-accent", "accent"], ["on-accent", "accent-hover"], ["on-danger", "danger"], ["on-danger", "danger-hover"],
    ["on-primary", "primary"], ["on-primary", "primary-hover"],
    ["on-chat-accent", "chat-accent"], ["on-chat-accent", "chat-accent-hover"],
    ...["accent", "success", "warning", "danger"].map(role => [role, `${role}-soft`]),
  ];
  it.each(textPairs)("%s on %s has at least 4.5:1 contrast", (foreground, background) => {
    expect(contrast(palette[`color-${foreground}`], palette[`color-${background}`])).toBeGreaterThanOrEqual(4.5);
  });
  it.each(["bg", "surface", "surface-muted", "accent-soft"])("focus and input boundaries on %s have at least 3:1 contrast", background => {
    for (const role of ["focus", "border-strong"]) expect(contrast(palette[`color-${role}`], palette[`color-${background}`])).toBeGreaterThanOrEqual(3);
  });
});
