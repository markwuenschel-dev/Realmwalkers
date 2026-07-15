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
  lanes_run: ["combat"],
  lanes_failed: [],
  model: "gpt-5.6-luna",
  pov_free: true,
  dialogue_rules_loaded: true,
  source_chars: 20,
  enriched_chars: 47,
  tokens_used: 565,
};

const proseBox = () => screen.getByPlaceholderText("Paste your scene here…");
const chip = (name: string) => screen.getByRole("button", { name });
const lanesSent = () => enrich.mock.calls[0][0].lanes;

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
    enrich.mockResolvedValue({
      ...RESULT,
      lanes_run: ["dialogue"],
      dialogue_rules_loaded: false,
    });
    render(<EnrichScreen />);
    fireEvent.change(proseBox(), { target: { value: "'Hi,' he said." } });
    fireEvent.click(screen.getByRole("button", { name: "Enrich" }));

    await waitFor(() => expect(screen.getByText("NO DIALOGUE RULES")).toBeTruthy());
  });
});

// Lane selection is a SET with a meaningful empty state: picking nothing runs everything. The panel
// must never make the author remember that, and must never quietly widen or narrow what they picked.
describe("EnrichScreen lane selection", () => {
  beforeEach(() => {
    sessionStorage.clear();
    enrich.mockReset();
    enrich.mockResolvedValue(RESULT);
  });

  it("sends no lanes when none are picked — the server reads that as all of them", async () => {
    render(<EnrichScreen />);
    fireEvent.change(proseBox(), { target: { value: "He swung." } });
    fireEvent.click(screen.getByRole("button", { name: "Enrich" }));

    await waitFor(() => expect(enrich).toHaveBeenCalled());
    expect(lanesSent()).toEqual([]);
  });

  it("says what an empty selection will actually run, so it need not be remembered", () => {
    render(<EnrichScreen />);
    expect(screen.getByText(/Combat → Sensory → Dialogue/)).toBeTruthy();
  });

  it("sends exactly the lanes picked", async () => {
    render(<EnrichScreen />);
    fireEvent.change(proseBox(), { target: { value: "He swung." } });
    fireEvent.click(chip("Combat"));
    fireEvent.click(chip("Dialogue"));
    fireEvent.click(screen.getByRole("button", { name: "Enrich" }));

    await waitFor(() => expect(enrich).toHaveBeenCalled());
    expect(lanesSent()).toEqual(["combat", "dialogue"]);
  });

  it("toggles a lane back off", async () => {
    render(<EnrichScreen />);
    fireEvent.change(proseBox(), { target: { value: "He swung." } });
    fireEvent.click(chip("Sensory"));
    fireEvent.click(chip("Sensory"));
    fireEvent.click(screen.getByRole("button", { name: "Enrich" }));

    await waitFor(() => expect(enrich).toHaveBeenCalled());
    expect(lanesSent()).toEqual([]); // back to the default, not a stuck selection
  });

  it("shows the run in canonical order, not the order the chips were clicked", () => {
    render(<EnrichScreen />);
    fireEvent.click(chip("Dialogue"));
    fireEvent.click(chip("Combat"));
    expect(screen.getByText(/Combat → Dialogue/)).toBeTruthy();
  });

  it("keeps a lane selection across a nav away and back", async () => {
    const first = render(<EnrichScreen />);
    fireEvent.click(chip("Sensory"));
    await waitFor(() => expect(sessionStorage.getItem("desk.inject.v1")).toBeTruthy());

    first.unmount();

    render(<EnrichScreen />);
    await waitFor(() => expect(chip("Sensory").getAttribute("aria-pressed")).toBe("true"));
  });

  it("reads a stored v1 single lane as that one lane, not as all of them", async () => {
    // v1 shipped `lane: "combat"`. Widening it to all three would silently cost 3x the tokens on a
    // click the author thought they had already configured.
    sessionStorage.setItem(
      "desk.inject.v1",
      JSON.stringify({ prose: "He swung.", pov: "", lane: "combat", beat: "", result: null }),
    );
    render(<EnrichScreen />);

    await waitFor(() => expect(chip("Combat").getAttribute("aria-pressed")).toBe("true"));
    expect(chip("Sensory").getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(screen.getByRole("button", { name: "Enrich" }));
    await waitFor(() => expect(enrich).toHaveBeenCalled());
    expect(lanesSent()).toEqual(["combat"]);
  });

  it("marks a partial result as partial instead of passing it off as complete", async () => {
    enrich.mockResolvedValue({
      ...RESULT,
      lanes_run: ["combat", "dialogue"],
      lanes_failed: [{ lane: "sensory", reason: "sensory enrichment pass returned empty output" }],
    });
    render(<EnrichScreen />);
    fireEvent.change(proseBox(), { target: { value: "He swung." } });
    fireEvent.click(screen.getByRole("button", { name: "Enrich" }));

    // The prose that DID come back is still handed over — a failed lane doesn't discard real work.
    await waitFor(() => expect(screen.getByDisplayValue(RESULT.enriched)).toBeTruthy());
    expect(screen.getByText(/Partial result — sensory did not run/)).toBeTruthy();
    expect(screen.getByText(/returned empty output/)).toBeTruthy();
  });
});
