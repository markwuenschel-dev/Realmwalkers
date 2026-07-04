"use client";

import { useEffect, useRef } from "react";

/** Console-only tab-load instrumentation: logs the elapsed time from screen mount (≈ navigation —
 *  route pages render their screen directly, so mount is the tab switch) to the first render that
 *  has real data. Pass `ready` as "this screen is showing meaningful content" — cached data counts,
 *  because instant-from-cache IS the number being tracked. Logs once per mount; a tab revisit
 *  remounts the screen and logs its own (hopefully ~0ms) timing, so cache wins are visible in the
 *  console next to cold loads. Zero overhead beyond one console.debug per mount.
 *
 *  Usage: useTabLoadTiming("manuscript", manuscript != null)
 */
export function useTabLoadTiming(screen: string, ready: boolean): void {
  const startRef = useRef<number | null>(null);
  if (startRef.current === null) startRef.current = performance.now();
  const loggedRef = useRef(false);
  useEffect(() => {
    if (!ready || loggedRef.current) return;
    loggedRef.current = true;
    const ms = Math.round(performance.now() - (startRef.current ?? performance.now()));
    console.debug(`[desk:tab-load] ${screen} first data render in ${ms}ms`);
  }, [ready, screen]);
}
