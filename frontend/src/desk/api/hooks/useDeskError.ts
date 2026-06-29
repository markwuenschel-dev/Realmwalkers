import { useCallback, useState } from "react";
import { toDeskError, type DeskErrorState } from "./shared";

export function useDeskError(): DeskErrorState {
  const [error, setError] = useState<string | null>(null);
  const clearError = useCallback(() => setError(null), []);
  const fail = useCallback((e: unknown) => setError(toDeskError(e)), []);
  return { error, setError, clearError, fail };
}
