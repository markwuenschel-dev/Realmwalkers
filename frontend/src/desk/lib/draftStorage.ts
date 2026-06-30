/** Remove autosaved hand-edit buffers for deleted scene ids. */
export function purgeDraftLocalStorage(sceneIds: string[]): void {
  if (sceneIds.length === 0) return;
  const prefixes = sceneIds.map((id) => `dominion:draft:${id}:`);
  try {
    const toRemove: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && prefixes.some((p) => key.startsWith(p))) toRemove.push(key);
    }
    for (const key of toRemove) localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export function isHttpNotFound(err: unknown): boolean {
  return err instanceof Error && err.message.startsWith("404");
}
