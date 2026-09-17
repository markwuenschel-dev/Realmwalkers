import { describe, expect, it } from "vitest";
import { CHORD_TO_HREF, DESK_ROUTES, activeRouteId } from "./routes";

// The g-chord handler (state.ts) resolves the second key through CHORD_TO_HREF, and it also owns
// bare "g" (chord start), "j" and "k" (next/previous scene). A duplicate key silently shadows a
// route, and a reserved key would never reach the chord map at all.
describe("DESK_ROUTES chord keys", () => {
  const keys = DESK_ROUTES.flatMap((r) => (r.key ? [r.key] : []));

  it("are unique", () => {
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("never use the keys the global handler reserves", () => {
    for (const reserved of ["g", "j", "k"]) expect(keys).not.toContain(reserved);
  });

  it("agree with each route's printed shortcut", () => {
    for (const r of DESK_ROUTES) {
      if (r.key) expect(r.shortcut).toBe(`G ${r.key.toUpperCase()}`);
    }
  });

  it("route g n to Notes", () => {
    expect(CHORD_TO_HREF.n).toBe("/notes");
    expect(activeRouteId("/notes")).toBe("notes");
  });
});
