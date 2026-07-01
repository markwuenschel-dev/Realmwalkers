import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DeskDataProvider, useDeskData } from "./data";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("../client", () => ({
  api: {
    books: vi.fn().mockResolvedValue([]),
    chapters: vi.fn().mockResolvedValue([]),
    pending: vi.fn().mockResolvedValue([]),
    jobsStatus: vi.fn().mockResolvedValue({
      running: false,
      queued: 0,
      failed: 0,
      active_scene: null,
      last_cache_hit_ratio: null,
      last_cache_read_tokens: null,
      last_cache_creation_tokens: null,
      last_cache_tokens_saved: null,
    }),
  },
}));

const EXPECTED_KEYS = [
  "loading",
  "error",
  "clearError",
  "books",
  "bookId",
  "setBook",
  "chapters",
  "scenes",
  "latestScenes",
  "pending",
  "refreshAll",
  "openSceneById",
  "decide",
  "draftNext",
  "createAndPropose",
] as const;

function Probe() {
  const data = useDeskData();
  return (
    <ul>
      {EXPECTED_KEYS.map((k) => (
        <li key={k} data-key={k}>
          {k in data ? "yes" : "no"}
        </li>
      ))}
    </ul>
  );
}

describe("useDeskData facade", () => {
  it("exposes the stable DeskData contract keys", () => {
    render(
      <DeskDataProvider>
        <Probe />
      </DeskDataProvider>,
    );
    for (const k of EXPECTED_KEYS) {
      expect(document.querySelector(`[data-key="${k}"]`)?.textContent).toBe("yes");
    }
  });
});
