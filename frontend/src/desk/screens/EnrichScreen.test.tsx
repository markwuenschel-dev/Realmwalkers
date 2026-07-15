import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EnrichScreen from "./EnrichScreen";

// Inject keeps nothing server-side, which makes the browser the ONLY copy of a result the machine
// spent ~12s and real tokens producing. Next unmounts the screen on any nav, so without persistence
// a stray click at Inbox silently destroys it — which is exactly what happened in the field. These
// tests pin that: unmount/remount must be lossless, and Clear must be the only way to lose work.

const enrich = vi.fn();
vi.mock("../api/client", () => ({
  api: {
    enrich: (...args: unknown[]) => enrich(...args),
  },
}));

const RESULT = {
  enriched: "He drove the blade through the gap in the guard.",
  lane: "combat",
  model: "gpt-5.6-luna",
  pov_free: true,
  dialogue_rules_loaded: true,
  source_chars: 20,
  enriched_chars: 47,
  tokens_used: 565,
};

const proseBox = () => screen.getByPlaceholderText("Paste your scene here…");

describe("EnrichScreen persistence", () => {
  beforeEach(() => {
    sessionStorage.clear();
    enrich.mockReset();
    enrich.mockResolvedValue(RESULT);
  });

  it("keeps the enriched result across a nav away and back", async () => {
    const first = render(<EnrichScreen />);
    fireEvent.change(proseBox(), { target: { value: "He swung. It hit." } });
    fireEvent.click(screen.getByRole("button", { name: "Enrich" }));
    await waitFor(() => expect(screen.getByDisplayValue(RESULT.enriched)).toBeTruthy());

    first.unmount(); // navigating to Inbox

    render(<EnrichScreen />); // navigating back
    await waitFor(() => expect(screen.getByDisplayValue(RESULT.enriched)).toBeTruthy());
    expect(screen.getByDisplayValue("He swung. It hit.")).toBeTruthy();
    expect(enrich).toHaveBeenCalledTimes(1); // restored, not silently re-run at cost
  });

  it("keeps unsent prose across a nav away and back", async () => {
    const first = render(<EnrichScreen />);
    fireEvent.change(proseBox(), { target: { value: "a whole pasted chapter" } });
    await waitFor(() => expect(sessionStorage.getItem("desk.inject.v1")).toBeTruthy());

    first.unmount();

    render(<EnrichScreen />);
    await waitFor(() => expect(screen.getByDisplayValue("a whole pasted chapter")).toBeTruthy());
  });

  it("sends pov as typed and omits an empty one — a prologue has no POV character", async () => {
    render(<EnrichScreen />);
    fireEvent.change(proseBox(), { target: { value: "The city burned." } });
    fireEvent.click(screen.getByRole("button", { name: "Enrich" }));

    await waitFor(() => expect(enrich).toHaveBeenCalled());
    expect(enrich.mock.calls[0][0]).toMatchObject({ pov: "", beat_text: null });
  });

  it("Clear is the only thing that discards stored work, and it does not come back", async () => {
    const first = render(<EnrichScreen />);
    fireEvent.change(proseBox(), { target: { value: "He swung. It hit." } });
    fireEvent.click(screen.getByRole("button", { name: "Enrich" }));
    await waitFor(() => expect(screen.getByDisplayValue(RESULT.enriched)).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryByDisplayValue(RESULT.enriched)).toBeNull();

    // The store is emptied by the mirror effect rewriting cleared state — not by removeItem — so
    // assert the CONTENT is gone and, critically, that a remount can't resurrect it.
    await waitFor(() =>
      expect(JSON.parse(sessionStorage.getItem("desk.inject.v1") ?? "{}")).toMatchObject({
        prose: "",
        result: null,
      }),
    );

    first.unmount();
    render(<EnrichScreen />);
    await waitFor(() => expect(proseBox()).toBeTruthy());
    expect(screen.queryByDisplayValue(RESULT.enriched)).toBeNull();
  });

  it("surfaces a stripped dialogue ruleset instead of passing off the output as good", async () => {
    enrich.mockResolvedValue({ ...RESULT, lane: "dialogue", dialogue_rules_loaded: false });
    render(<EnrichScreen />);
    fireEvent.change(proseBox(), { target: { value: "'Hi,' he said." } });
    fireEvent.click(screen.getByRole("button", { name: "Enrich" }));

    await waitFor(() => expect(screen.getByText("NO DIALOGUE RULES")).toBeTruthy());
  });
});
