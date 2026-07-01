"use client";

import { useEffect, useState } from "react";

// Author name for the Shunn submission header/byline — persisted so it's typed once and reused by
// every export surface (Manuscript, Inbox, Scene, Chapters, Packets), not re-typed per tab.
const STORAGE_KEY = "ms_author";

/** Persisted author name + setter. Read after mount (not in the initializer) so server prerender
 *  never touches localStorage. */
export function useAuthorName(): [string, (v: string) => void] {
  const [author, setAuthor] = useState("");
  useEffect(() => {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      if (v) setAuthor(v);
    } catch {
      /* unavailable */
    }
  }, []);
  const saveAuthor = (v: string) => {
    setAuthor(v);
    try {
      localStorage.setItem(STORAGE_KEY, v);
    } catch {
      /* ignore */
    }
  };
  return [author, saveAuthor];
}

/** Resolve the name to stamp on a Shunn export: the persisted author, or (if blank) prompt once and
 *  persist the answer so it isn't asked again. Returns null if the user cancels/leaves it blank. */
export function resolveAuthorName(current: string, saveAuthor: (v: string) => void): string | null {
  const name =
    current.trim() ||
    (typeof window === "undefined"
      ? ""
      : (window.prompt("Author name for the manuscript header / byline:") ?? "").trim());
  if (!name) return null;
  if (name !== current) saveAuthor(name);
  return name;
}
