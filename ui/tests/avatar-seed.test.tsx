import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Avatar, DEFAULT_AVATAR, encodeAvatarSeed, parseAvatarSeed } from "../src/design/avatar";
import { avatarColors, characters, pets } from "../src/design/characters/catalog";

describe("companion avatar seeds", () => {
  it("round-trips every supported combination within the API's 64-character limit", () => {
    for (const color of avatarColors) for (const character of ["none", ...characters.map(item => item.id)]) for (const pet of ["none", ...pets.map(item => item.id)]) {
      const choices = { color: color.id, character, pet };
      const seed = encodeAvatarSeed(choices);
      expect(seed).toBe(`v2:c=${color.id};h=${character};p=${pet}`);
      expect(seed.length).toBeLessThanOrEqual(64);
      expect(parseAvatarSeed(seed)).toEqual(choices);
    }
  });
  it.each(["moss", "open-fold-2", "", "v1:c=sky", "V2:c=sky"])("preserves the legacy renderer for %s", seed => {
    expect(parseAvatarSeed(seed)).toBeNull();
    render(<Avatar seed={seed} />);
    expect(screen.getByRole("img").querySelectorAll("path")).toHaveLength(3);
    expect(screen.getByRole("img").querySelector("rect")).toHaveAttribute("rx", "25");
  });
  it.each(["v2:", "v2:c=unknown;h=none;p=none", "v2:c=jade;h=unknown;p=cat", "v2:c=jade;h=cloud;p=unknown", "v2:c=jade;h=cloud", "v2:c=jade;h=cloud;p=cat;extra=1", "v2:c=jade;c=sky;h=none;p=none", "v2:c=jade;h=none;p=none\n", `v2:${"x".repeat(70)}`])("falls back safely for malformed input %s", seed => {
    expect(parseAvatarSeed(seed)).toEqual(DEFAULT_AVATAR);
  });
  it("sanitizes invalid choices before encoding", () => {
    expect(parseAvatarSeed(encodeAvatarSeed({ color: "invalid", character: "<script>", pet: "x".repeat(100) }))).toEqual(DEFAULT_AVATAR);
  });
  it("composes a solid color, character, and pet with unique gradient references", () => {
    render(<><Avatar seed="v2:c=jade;h=cloud;p=fox" label="First" /><Avatar seed="v2:c=jade;h=cloud;p=fox" label="Second" /></>);
    for (const name of ["First", "Second"]) {
      const avatar = screen.getByRole("img", { name });
      expect(avatar.querySelector('[data-character="cloud"]')).toBeInTheDocument();
      expect(avatar.querySelector('[data-pet="fox"]')).toHaveAttribute("shape-rendering", "crispEdges");
      expect(avatar.querySelector("circle")).toHaveAttribute("r", "46");
    }
    const ids = [...document.querySelectorAll("svg [id]")].map(element => element.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
