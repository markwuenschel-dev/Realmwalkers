import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useSelection } from "./useSelection";

describe("useSelection", () => {
  it("starts with nothing selected", () => {
    const { result } = renderHook(() => useSelection());
    expect(result.current.ids).toEqual([]);
    expect(result.current.count).toBe(0);
  });

  it("toggle adds an id that is not selected", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggle("a"));
    expect(result.current.ids).toContain("a");
    expect(result.current.count).toBe(1);
  });

  it("toggle removes an id that is already selected", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggle("a"));
    act(() => result.current.toggle("a"));
    expect(result.current.ids).not.toContain("a");
    expect(result.current.count).toBe(0);
  });

  it("has returns true for selected ids and false for others", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggle("x"));
    expect(result.current.has("x")).toBe(true);
    expect(result.current.has("y")).toBe(false);
  });

  it("toggleAll selects all visible ids when none are selected", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggleAll(["a", "b", "c"]));
    expect(result.current.ids.sort()).toEqual(["a", "b", "c"]);
  });

  it("toggleAll deselects all when all visible ids are selected", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggleAll(["a", "b"]));
    act(() => result.current.toggleAll(["a", "b"]));
    expect(result.current.ids).toEqual([]);
  });

  it("toggleAll selects all when only some visible ids are selected", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggle("a"));
    act(() => result.current.toggleAll(["a", "b", "c"]));
    expect(result.current.ids.sort()).toEqual(["a", "b", "c"]);
  });

  it("toggleAll with empty array clears selection (select all visible = select none)", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggle("a"));
    act(() => result.current.toggleAll([]));
    expect(result.current.ids).toEqual([]);
  });

  it("clear removes all selections", () => {
    const { result } = renderHook(() => useSelection());
    act(() => result.current.toggle("a"));
    act(() => result.current.toggle("b"));
    act(() => result.current.clear());
    expect(result.current.ids).toEqual([]);
    expect(result.current.count).toBe(0);
  });
});
