// Lightweight data-fetching for the desk — no new deps (the project has no react-query). `useFetch`
// is a generic load-once-per-deps hook with loading/error state; `SelectedBookProvider` loads the
// book list and tracks the active book (the API is multi-book; default to the first GET /books).
import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./client";
import type { BookOut } from "./client";

export interface Async<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/** Run `fn` whenever `deps` change; expose loading/error/data. `fn` is intentionally not a dep — the
 * caller declares what the fetch depends on, exactly like an effect dependency array. */
export function useFetch<T>(fn: () => Promise<T>, deps: unknown[]): Async<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fn()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, loading, error, reload: () => setNonce((n) => n + 1) };
}

interface SelectedBookValue {
  books: BookOut[];
  bookId: string | null;
  selectBook: (id: string) => void;
  loading: boolean;
  error: string | null;
}

const SelectedBookContext = createContext<SelectedBookValue | null>(null);

export function SelectedBookProvider({ children }: { children: ReactNode }) {
  const { data, loading, error } = useFetch(() => api.books(), []);
  const [selected, setSelected] = useState<string | null>(null);
  const books = data ?? [];
  // Default to the first book until the user picks one; survives reloads of the book list.
  const bookId = selected ?? books[0]?.id ?? null;
  return (
    <SelectedBookContext.Provider
      value={{ books, bookId, selectBook: setSelected, loading, error }}
    >
      {children}
    </SelectedBookContext.Provider>
  );
}

export function useSelectedBook(): SelectedBookValue {
  const ctx = useContext(SelectedBookContext);
  if (!ctx) throw new Error("useSelectedBook must be used inside <SelectedBookProvider>");
  return ctx;
}
