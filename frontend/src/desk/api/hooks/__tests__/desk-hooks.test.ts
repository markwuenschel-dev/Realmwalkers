import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EMPTY_JOBS } from "../../constants";
import { useDeskBooks } from "../useDeskBooks";
import { useDeskJobs } from "../useDeskJobs";

vi.mock("../../client", () => ({
  api: {
    books: vi.fn(),
    jobsStatus: vi.fn(),
    jobsFailed: vi.fn(),
  },
}));

import { api } from "../../client";

describe("useDeskBooks", () => {
  beforeEach(() => {
    vi.mocked(api.books).mockReset();
  });

  it("loads books and selects the first by default", async () => {
    vi.mocked(api.books).mockResolvedValue([
      { id: "b1", title: "One", premise: null, created_at: "2020-01-01T00:00:00Z" },
      { id: "b2", title: "Two", premise: null, created_at: "2020-01-01T00:00:00Z" },
    ]);
    const fail = vi.fn();
    const setLoading = vi.fn();

    const { result } = renderHook(() => useDeskBooks(fail, setLoading));

    await waitFor(() => expect(result.current.books).toHaveLength(2));
    expect(result.current.bookId).toBe("b1");
    expect(fail).not.toHaveBeenCalled();
  });

  it("surfaces load failures via fail", async () => {
    vi.mocked(api.books).mockRejectedValue(new Error("network down"));
    const fail = vi.fn();
    const setLoading = vi.fn();

    renderHook(() => useDeskBooks(fail, setLoading));

    await waitFor(() => expect(fail).toHaveBeenCalled());
  });
});

describe("useDeskJobs", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api.jobsStatus).mockReset();
    vi.mocked(api.jobsFailed).mockReset();
  });

  it("starts with no failed jobs and backend reachable", () => {
    vi.mocked(api.jobsStatus).mockResolvedValue(EMPTY_JOBS);
    const setJobs = vi.fn();
    const loadCollections = vi.fn().mockResolvedValue(undefined);

    const { result } = renderHook(() =>
      useDeskJobs("book-1", EMPTY_JOBS, setJobs, loadCollections),
    );

    expect(result.current.failedJobs).toEqual([]);
    expect(result.current.jobsUnreachable).toBe(false);
  });

  it("marks backend unreachable after consecutive poll failures", async () => {
    vi.mocked(api.jobsStatus).mockRejectedValue(new Error("down"));
    const setJobs = vi.fn();
    const loadCollections = vi.fn();

    const { result } = renderHook(() =>
      useDeskJobs("book-1", EMPTY_JOBS, setJobs, loadCollections),
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
      await vi.advanceTimersByTimeAsync(4000);
    });

    expect(result.current.jobsUnreachable).toBe(true);
    vi.useRealTimers();
  });
});
