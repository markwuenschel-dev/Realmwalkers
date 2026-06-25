import { useCallback, useMemo, useState } from "react";

// Multi-select over a list of string ids, shared by every screen that wants bulk actions (inbox
// scenes, ledger canon/threads/characters). Keep it dumb: the screen owns the items, this owns which
// ids are ticked. Pass the currently-visible ids to `toggleAll` so "select all" respects filters.
export function useSelection() {
  const [ids, setIds] = useState<Set<string>>(new Set());

  const toggle = useCallback((id: string) => {
    setIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAll = useCallback((visible: string[]) => {
    setIds((prev) => {
      const allOn = visible.length > 0 && visible.every((id) => prev.has(id));
      return allOn ? new Set() : new Set(visible);
    });
  }, []);

  const clear = useCallback(() => setIds(new Set()), []);
  const has = useCallback((id: string) => ids.has(id), [ids]);

  return useMemo(
    () => ({ ids: [...ids], count: ids.size, has, toggle, toggleAll, clear }),
    [ids, has, toggle, toggleAll, clear],
  );
}
