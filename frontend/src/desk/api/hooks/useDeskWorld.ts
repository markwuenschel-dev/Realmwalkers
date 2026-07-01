import { useCallback, useMemo } from "react";
import { api } from "../client";
import type {
  CanonEntityIn,
  CanonEntityOut,
  CanonEntityUpdateIn,
  CharacterStateIn,
  CharacterStateOut,
  ThreadBeatIn,
  ThreadIn,
  ThreadOut,
} from "../types";
import type { DeskFail } from "./shared";

export interface DeskWorldState {
  createThread: (bookId: string | null, body: ThreadIn) => Promise<void>;
  addThreadBeat: (threadId: string, body: ThreadBeatIn) => Promise<void>;
  deleteThread: (id: string) => Promise<void>;
  upsertCharacter: (bookId: string | null, name: string, body: CharacterStateIn) => Promise<void>;
  deleteCharacter: (bookId: string | null, name: string) => Promise<void>;
  createCanon: (bookId: string | null, body: CanonEntityIn) => Promise<void>;
  updateCanon: (id: string, body: CanonEntityUpdateIn) => Promise<void>;
  deleteCanon: (id: string) => Promise<void>;
  ingestCanon: (
    bookId: string | null,
    loadCollections: (id: string) => Promise<void>,
  ) => Promise<number | null>;
}

export function useDeskWorld(
  fail: DeskFail,
  setThreads: React.Dispatch<React.SetStateAction<ThreadOut[]>>,
  setCharacters: React.Dispatch<React.SetStateAction<CharacterStateOut[]>>,
  setCanon: React.Dispatch<React.SetStateAction<CanonEntityOut[]>>,
): DeskWorldState {
  const createThread = useCallback(
    async (bookId: string | null, body: ThreadIn): Promise<void> => {
      if (!bookId) return;
      try {
        const created = await api.createThread(bookId, body);
        setThreads((ts) => [...ts, created]);
      } catch (e) {
        fail(e);
      }
    },
    [fail, setThreads],
  );

  const addThreadBeat = useCallback(
    async (threadId: string, body: ThreadBeatIn): Promise<void> => {
      try {
        const updated = await api.addThreadBeat(threadId, body);
        setThreads((ts) => ts.map((t) => (t.id === updated.id ? updated : t)));
      } catch (e) {
        fail(e);
      }
    },
    [fail, setThreads],
  );

  const deleteThread = useCallback(
    async (id: string): Promise<void> => {
      try {
        await api.deleteThread(id);
        setThreads((ts) => ts.filter((t) => t.id !== id));
      } catch (e) {
        fail(e);
      }
    },
    [fail, setThreads],
  );

  const upsertCharacter = useCallback(
    async (bookId: string | null, name: string, body: CharacterStateIn): Promise<void> => {
      if (!bookId) return;
      try {
        const updated = await api.upsertCharacter(bookId, name, body);
        setCharacters((cs) => {
          const i = cs.findIndex((c) => c.character === updated.character);
          if (i < 0) return [...cs, updated].sort((a, b) => a.character.localeCompare(b.character));
          const next = [...cs];
          next[i] = updated;
          return next;
        });
      } catch (e) {
        fail(e);
      }
    },
    [fail, setCharacters],
  );

  const deleteCharacter = useCallback(
    async (bookId: string | null, name: string): Promise<void> => {
      if (!bookId) return;
      try {
        await api.deleteCharacter(bookId, name);
        setCharacters((cs) => cs.filter((c) => c.character !== name));
      } catch (e) {
        fail(e);
      }
    },
    [fail, setCharacters],
  );

  const createCanon = useCallback(
    async (bookId: string | null, body: CanonEntityIn): Promise<void> => {
      if (!bookId) return;
      try {
        const created = await api.createCanon(bookId, body);
        setCanon((cs) => [...cs, created]);
      } catch (e) {
        fail(e);
      }
    },
    [fail, setCanon],
  );

  const updateCanon = useCallback(
    async (id: string, body: CanonEntityUpdateIn): Promise<void> => {
      try {
        const updated = await api.updateCanon(id, body);
        setCanon((cs) => cs.map((c) => (c.id === updated.id ? updated : c)));
      } catch (e) {
        fail(e);
      }
    },
    [fail, setCanon],
  );

  const deleteCanon = useCallback(
    async (id: string): Promise<void> => {
      try {
        await api.deleteCanon(id);
        setCanon((cs) => cs.filter((c) => c.id !== id));
      } catch (e) {
        fail(e);
      }
    },
    [fail, setCanon],
  );

  const ingestCanon = useCallback(
    async (
      bookId: string | null,
      loadCollections: (id: string) => Promise<void>,
    ): Promise<number | null> => {
      if (!bookId) return null;
      try {
        const out = await api.ingestCanon(bookId);
        await loadCollections(bookId);
        return out.indexed;
      } catch (e) {
        fail(e);
        return null;
      }
    },
    [fail],
  );

  return useMemo(
    () => ({
      createThread,
      addThreadBeat,
      deleteThread,
      upsertCharacter,
      deleteCharacter,
      createCanon,
      updateCanon,
      deleteCanon,
      ingestCanon,
    }),
    [
      createThread,
      addThreadBeat,
      deleteThread,
      upsertCharacter,
      deleteCharacter,
      createCanon,
      updateCanon,
      deleteCanon,
      ingestCanon,
    ],
  );
}
