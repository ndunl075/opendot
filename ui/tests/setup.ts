import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
beforeEach(() => {
  const queries = new Map<string, MediaQueryList>();
  vi.stubGlobal("matchMedia", (query: string) => {
    if (!queries.has(query)) {
      const media = Object.assign(new EventTarget(), { matches: false, media: query, onchange: null });
      queries.set(query, media as MediaQueryList);
    }
    return queries.get(query)!;
  });
});

// jsdom has no dialog top layer. Browser tests exercise the real native behavior.
HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
