import { useCallback, useEffect, useState } from "react";
import { api } from "../client";
import type { BookOut } from "../types";
import type { DeskFail } from "./shared";

export interface DeskBooksState {
  books: BookOut[];
  bookId: string | null;
  setBook: (id: string) => void;
  setBookId: (id: string | null) => void;
  createBook: (title: string) => Promise<void>;
}

export function useDeskBooks(fail: DeskFail, setLoading: (v: boolean) => void): DeskBooksState {
  const [books, setBooks] = useState<BookOut[]>([]);
  const [bookId, setBookId] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const bks = await api.books();
        if (!alive) return;
        setBooks(bks);
        setBookId((cur) => cur ?? bks[0]?.id ?? null);
      } catch (e) {
        if (alive) fail(e);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [fail, setLoading]);

  const setBook = useCallback((id: string) => setBookId(id), []);

  const createBook = useCallback(
    async (title: string): Promise<void> => {
      try {
        const book = await api.createBook({ title });
        setBooks((bs) => [...bs, book]);
        setBookId(book.id);
      } catch (e) {
        fail(e);
      }
    },
    [fail],
  );

  return { books, bookId, setBook, setBookId, createBook };
}
