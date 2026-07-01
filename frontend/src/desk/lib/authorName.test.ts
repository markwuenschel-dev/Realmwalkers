import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resolveAuthorName, useAuthorName } from "./authorName";

// This project's jsdom test environment throws on real window.localStorage access ("Cannot initialize
// local storage without a `--localstorage-file` path") — the same reason every localStorage call in
// the app (including this module) is wrapped in try/catch. Stub an in-memory Storage so these tests
// exercise the read/write round trip instead of that environment limitation.
function memoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => void store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  } as Storage;
}

describe("useAuthorName", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", memoryStorage());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts empty when nothing is persisted", () => {
    const { result } = renderHook(() => useAuthorName());
    expect(result.current[0]).toBe("");
  });

  it("reads a previously persisted name on mount", () => {
    localStorage.setItem("ms_author", "Jane Doe");
    const { result } = renderHook(() => useAuthorName());
    expect(result.current[0]).toBe("Jane Doe");
  });

  it("saving updates both the returned value and localStorage", () => {
    const { result } = renderHook(() => useAuthorName());
    act(() => result.current[1]("Mark Wuenschel"));
    expect(result.current[0]).toBe("Mark Wuenschel");
    expect(localStorage.getItem("ms_author")).toBe("Mark Wuenschel");
  });

  it("is shared under the same key every export surface reads (ms_author)", () => {
    localStorage.setItem("ms_author", "Shared Author");
    const { result } = renderHook(() => useAuthorName());
    expect(result.current[0]).toBe("Shared Author");
  });

  it("degrades gracefully (no throw) when localStorage access itself throws", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("unavailable");
      },
      setItem: () => {
        throw new Error("unavailable");
      },
    });
    const { result } = renderHook(() => useAuthorName());
    expect(result.current[0]).toBe("");
    expect(() => act(() => result.current[1]("Name"))).not.toThrow();
    expect(result.current[0]).toBe("Name"); // state still updates even though persistence failed
  });
});

describe("resolveAuthorName", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the current name without prompting when it's already set", () => {
    const promptSpy = vi.spyOn(window, "prompt");
    const saveAuthor = vi.fn();
    expect(resolveAuthorName("Jane Doe", saveAuthor)).toBe("Jane Doe");
    expect(promptSpy).not.toHaveBeenCalled();
    expect(saveAuthor).not.toHaveBeenCalled();
  });

  it("trims and re-saves a name that has stray whitespace", () => {
    const saveAuthor = vi.fn();
    expect(resolveAuthorName("  Jane Doe  ", saveAuthor)).toBe("Jane Doe");
    expect(saveAuthor).toHaveBeenCalledWith("Jane Doe");
  });

  it("prompts and persists the answer when no name is set yet", () => {
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("New Author");
    const saveAuthor = vi.fn();
    expect(resolveAuthorName("", saveAuthor)).toBe("New Author");
    expect(promptSpy).toHaveBeenCalledTimes(1);
    expect(saveAuthor).toHaveBeenCalledWith("New Author");
  });

  it("returns null and does not save when the prompt is cancelled", () => {
    vi.spyOn(window, "prompt").mockReturnValue(null);
    const saveAuthor = vi.fn();
    expect(resolveAuthorName("", saveAuthor)).toBeNull();
    expect(saveAuthor).not.toHaveBeenCalled();
  });

  it("returns null when the prompt answer is blank/whitespace", () => {
    vi.spyOn(window, "prompt").mockReturnValue("   ");
    const saveAuthor = vi.fn();
    expect(resolveAuthorName("", saveAuthor)).toBeNull();
    expect(saveAuthor).not.toHaveBeenCalled();
  });
});
